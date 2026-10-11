// pnpm record-media [stills|hero|demo|all]: renders the README media kit into docs/media/.
// Started from design/export/record-media.mjs. The design pages are served from
// design/export by a small built-in HTTP server (the hero pages load .jsx files, which do
// not work from file:// URLs). `stills` needs only Chromium; `hero` and `demo` also need
// ffmpeg on PATH, and `demo` needs the app running (`pnpm dev` with `pnpm seed`).
//
//   APP_URL=http://localhost:3110 pnpm record-media all
import { execFileSync } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
// Playwright is a dependency of the frontend package; resolve it from there.
const requireFromFrontend = createRequire(path.join(root, "frontend", "package.json"));
const { chromium } = requireFromFrontend("@playwright/test");

const DESIGN_DIR = path.join(root, "design", "export");
const APP = process.env.APP_URL ?? "http://localhost:3110";
const OUT = path.join(root, "docs", "media");
const TMP = path.join(OUT, ".tmp");
const SAMPLE_PDF = path.join(root, "samples", "S08-2026-1043.pdf");
const what = process.argv[2] ?? "all";

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".jsx": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
};

/** Serves design/export on a free local port; only files inside that folder. */
function serveDesign() {
  const server = createServer((request, response) => {
    const name = decodeURIComponent(new URL(request.url ?? "/", "http://x").pathname);
    const file = path.normalize(path.join(DESIGN_DIR, name));
    if (!file.startsWith(DESIGN_DIR) || !existsSync(file) || statSync(file).isDirectory()) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "Content-Type": TYPES[path.extname(file)] ?? "application/octet-stream" });
    createReadStream(file).pipe(response);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({ server, url: `http://127.0.0.1:${port}` });
    });
  });
}

function ffmpeg(args) {
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", ...args], { stdio: "inherit" });
}

function needFfmpeg() {
  try {
    execFileSync("ffmpeg", ["-version"], { stdio: "ignore" });
  } catch {
    console.error("record-media: ffmpeg not found. Install it and run again (stills work without it).");
    process.exit(1);
  }
}

const STILLS = [
  ["Media Architecture.dc.html", "architecture.png", 1600, 1000],
  ["Media Lifecycle.dc.html", "lifecycle.png", 1600, 1000],
  ["Media Hero Poster.dc.html", "hero-poster.png", 1280, 720],
  ["Media Social Preview.dc.html", "social-preview.png", 1280, 640],
  ["Media Feature Verdicts.dc.html", "feature-verdicts.png", 1200, 675],
  ["Media Feature Evidence.dc.html", "feature-evidence.png", 1200, 675],
  ["Media Feature Checks.dc.html", "feature-checks.png", 1200, 675],
  ["Media Feature Approval.dc.html", "feature-approval.png", 1200, 675],
];

// The feature pages frame the app screens with fixed offsets that do not match the
// exported screens (the evidence frame shows the empty foot of the document pane, the
// inbox frame starts past the supplier column, the approvals table is wider than its
// frame). Each entry re-frames the nth iframe on the page: the screen area from x, y
// (screen pixels) fills the frame's width at the iframe's own viewport size; `scroll`
// scrolls the pane holding that heading so the heading sits at the top of it.
const FRAMES = {
  "Media Feature Verdicts.dc.html": [{ x: 96, y: 104, viewport: [1440, 900] }],
  "Media Feature Evidence.dc.html": [{ x: 96, y: 250, viewport: [1440, 900], scroll: "Fields" }],
  "Media Feature Approval.dc.html": [
    { x: 96, y: 56, viewport: [1440, 900] },
    { x: 96, y: 56, viewport: [1440, 900] },
  ],
};

async function reframe(page, file) {
  const frames = FRAMES[file];
  if (!frames) return;
  await page.evaluate((specs) => {
    const iframes = document.querySelectorAll("iframe");
    specs.forEach((spec, index) => {
      const iframe = iframes[index];
      const box = iframe.parentElement.clientWidth;
      const scale = box / (spec.viewport[0] - spec.x);
      Object.assign(iframe.style, {
        left: `${-spec.x * scale}px`,
        top: `${-spec.y * scale}px`,
        width: `${spec.viewport[0]}px`,
        height: `${spec.viewport[1]}px`,
        transform: `scale(${scale})`,
      });
    });
  }, frames);
  await page.waitForTimeout(500);
  const children = page.mainFrame().childFrames();
  for (const [index, spec] of frames.entries()) {
    if (!spec.scroll) continue;
    await children[index].evaluate((heading) => {
      const target = [...document.querySelectorAll("h1, h2, h3, h4")].find((node) => node.textContent.trim() === heading);
      let pane = target?.parentElement;
      while (pane && !(pane.scrollHeight > pane.clientHeight && /auto|scroll/.test(getComputedStyle(pane).overflowY))) pane = pane.parentElement;
      if (pane) pane.scrollTop += target.getBoundingClientRect().top - pane.getBoundingClientRect().top - 16;
    }, spec.scroll);
  }
  await page.waitForTimeout(300);
}

async function settle(page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(2500); // the design runtime, icons and nested frames
}

async function shot(browser, design, file, out, width, height, query = "") {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  await page.goto(`${design}/${encodeURI(file)}${query}`);
  await settle(page);
  await reframe(page, file);
  await page.screenshot({ path: out, clip: { x: 0, y: 0, width, height } });
  await page.close();
}

async function stills(browser, design) {
  for (const [file, out, width, height] of STILLS) {
    await shot(browser, design, file, path.join(OUT, out), width, height);
    console.log("✓", out);
  }
}

async function hero(browser, design) {
  needFfmpeg();
  // Record the live loop at 1280×720, then cut one 12 s cycle into a 960 px, 15 fps GIF.
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: TMP, size: { width: 1280, height: 720 } },
  });
  const page = await context.newPage();
  await page.goto(`${design}/${encodeURI("Media Hero.dc.html")}?record=1`);
  await settle(page);
  await page.waitForTimeout(26000); // two full cycles; the second is kept
  await context.close();
  const webm = path.join(TMP, readdirSync(TMP).find((name) => name.endsWith(".webm")));
  const start = process.env.HERO_SS ?? "14.5"; // adjust if the cycle is off by a frame
  for (let colors = 160; ; colors -= 32) {
    ffmpeg([
      "-ss", start, "-t", "12", "-i", webm,
      "-vf", `fps=15,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=${colors}:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3`,
      path.join(OUT, "hero.gif"),
    ]); // prettier-ignore
    const megabytes = statSync(path.join(OUT, "hero.gif")).size / 1048576;
    if (megabytes < 8 || colors <= 48) {
      console.log(`✓ hero.gif ${megabytes.toFixed(1)} MB, ${colors} colours`);
      break;
    }
  }
}

// The storyboard of design/export/Media Demo Storyboard.dc.html: cards are rendered
// frames, app shots are recorded live against the running app with the sandbox.
const SHOTS = [
  { card: ["Media Demo Title.dc.html", ""], seconds: 4 },
  {
    app: async (page) => {
      await page.goto(APP);
      await page.getByRole("button", { name: "Open the sandbox" }).first().click();
      await page.waitForURL(/\/app\/inbox/);
    },
    seconds: 5,
    caption: "No sign-up: open the sandbox",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=1"], seconds: 3 },
  {
    app: async (page) => {
      await page.getByRole("tab", { name: /^All/ }).click();
      await page.mouse.move(1500, 300);
      await page.mouse.move(1500, 800, { steps: 40 });
    },
    seconds: 8,
    caption: "Twelve invoices, each with a verdict",
  },
  {
    app: async (page) => {
      await page.getByRole("button", { name: "Upload invoices" }).first().click();
      await page.setInputFiles('input[type="file"]', SAMPLE_PDF);
      await page.waitForTimeout(8000);
      await page.keyboard.press("Escape");
    },
    seconds: 10,
    caption: "Watch it detect, validate, read, check",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=2"], seconds: 3 },
  {
    app: async (page) => {
      await page.getByRole("tab", { name: /^All/ }).click();
      await page.getByRole("row").filter({ hasText: "RE-2026-0413" }).click();
      await page.getByRole("button", { name: "Show official message" }).first().click();
      await page.waitForTimeout(2500);
    },
    seconds: 7,
    caption: "Rule BR-DE-15, explained in plain English",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=3"], seconds: 3 },
  {
    app: async (page) => {
      await page.goto(`${APP}/app/inbox?tab=all`);
      await page.getByRole("row").filter({ hasText: "BN-88290" }).click();
      await page.locator(".card").filter({ hasText: "C05" }).getByRole("button", { name: "Resolve…" }).click();
      await page.getByLabel("Note").pressSequentially("Checked with the supplier on the number in our files.", { delay: 40 });
      await page.getByRole("button", { name: "Resolve check" }).click();
      await page.getByRole("button", { name: "Mark reviewed" }).click();
    },
    seconds: 10,
    caption: "New bank account? Check before paying",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=4"], seconds: 3 },
  {
    app: async (page) => {
      await page.goto(`${APP}/app/approvals`);
      await page.locator("li, tr").filter({ hasText: "BN-88290" }).first().getByRole("button", { name: "Approve" }).click();
    },
    seconds: 6,
    caption: "Then someone approves it",
  },
  {
    app: async (page) => {
      await page.goto(`${APP}/app/exports`);
      await page.locator("label").filter({ hasText: "CSV, one row per invoice" }).filter({ hasNotText: "invoice line" }).click();
      await page.getByRole("button", { name: /^Export \d+ invoices?$/ }).click();
    },
    seconds: 6,
    caption: "One CSV for the tax advisor",
  },
  { card: ["Media Demo End.dc.html", ""], seconds: 4 },
];

/** A caption as a transparent PNG, in the self-hosted Instrument Sans (no font CDN). */
async function captionPng(browser, text, out) {
  const font = path.join(root, "frontend", "node_modules", "@fontsource", "instrument-sans", "files", "instrument-sans-latin-600-normal.woff2");
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const fontUrl = existsSync(font) ? `data:font/woff2;base64,${(await import("node:fs")).readFileSync(font).toString("base64")}` : "";
  await page.setContent(
    `<html><head><style>@font-face{font-family:Caption;font-weight:600;src:url(${fontUrl}) format("woff2")}</style></head>` +
      `<body style="margin:0;background:transparent"><div style="position:absolute;left:0;right:0;bottom:96px;display:grid;justify-items:center">` +
      `<div style="padding:20px 40px;background:#1f1c18;border-radius:12px;font:600 44px/1.2 Caption,sans-serif;color:#fffdf9">${text}</div></div></body></html>`,
  );
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: out, omitBackground: true });
  await page.close();
}

async function demo(browser, design) {
  needFfmpeg();
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: path.join(TMP, "app"), size: { width: 1920, height: 1080 } },
    acceptDownloads: true,
  });
  const app = await context.newPage();
  const parts = [];
  for (const [index, shotSpec] of SHOTS.entries()) {
    const segment = path.join(TMP, `seg-${String(index + 1).padStart(2, "0")}.mp4`);
    if (shotSpec.card) {
      const png = path.join(TMP, `card-${index + 1}.png`);
      await shot(browser, design, shotSpec.card[0], png, 1920, 1080, shotSpec.card[1]);
      ffmpeg(["-loop", "1", "-t", String(shotSpec.seconds), "-i", png, "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", segment]);
    } else {
      const started = Date.now();
      await shotSpec.app(app);
      const used = (Date.now() - started) / 1000;
      if (used < shotSpec.seconds) await app.waitForTimeout((shotSpec.seconds - used) * 1000);
      shotSpec.span = [started, Date.now()];
    }
    parts.push({ shotSpec, segment });
  }
  const recordingStart = SHOTS.find((item) => item.span).span[0];
  await context.close();
  const raw = path.join(TMP, "app", readdirSync(path.join(TMP, "app")).find((name) => name.endsWith(".webm")));
  for (const { shotSpec, segment } of parts) {
    if (!shotSpec.span) continue;
    const caption = segment.replace(".mp4", "-cap.png");
    await captionPng(browser, shotSpec.caption, caption);
    const from = ((shotSpec.span[1] - recordingStart) / 1000 - shotSpec.seconds).toFixed(2);
    ffmpeg(["-ss", from, "-t", String(shotSpec.seconds), "-i", raw, "-i", caption, "-filter_complex", "[0:v]fps=30,scale=1920:1080[v];[v][1:v]overlay=0:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", segment]);
  }
  const list = path.join(TMP, "list.txt");
  writeFileSync(list, parts.map((part) => `file '${path.resolve(part.segment)}'`).join("\n"));
  ffmpeg(["-f", "concat", "-safe", "0", "-i", list, "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", path.join(OUT, "demo.mp4")]);
  console.log("✓ demo.mp4");
}

mkdirSync(OUT, { recursive: true });
mkdirSync(TMP, { recursive: true });
const { server, url: design } = await serveDesign();
const browser = await chromium.launch();
try {
  if (what === "stills" || what === "all") await stills(browser, design);
  if (what === "hero" || what === "all") await hero(browser, design);
  if (what === "demo" || what === "all") await demo(browser, design);
} finally {
  await browser.close();
  server.close();
  rmSync(TMP, { recursive: true, force: true });
}
