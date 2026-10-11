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

async function shot(browser, design, file, out, width, height, query = "", texts = {}) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  await page.goto(`${design}/${encodeURI(file)}${query}`);
  await settle(page);
  await reframe(page, file);
  // Fills in text the design leaves as a placeholder, such as the end card's links.
  await page.evaluate((replacements) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent.trim();
      if (text in replacements) node.textContent = replacements[text];
    }
  }, texts);
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

/** Scrolls a control into the middle of its own pane; the window itself stays put. */
async function scrollPane(page, locator) {
  await locator.evaluate((node) => node.scrollIntoView({ block: "center", behavior: "smooth" }));
  await page.waitForTimeout(900);
  await page.evaluate(() => window.scrollTo(0, 0));
}

// The demo draws its own pointer: the browser's cursor is not part of a recorded video.
// It follows the real mouse events, keeps its place across page loads and rings on a click.
function drawPointer() {
  const draw = () => {
    if (document.getElementById("demo-pointer")) return;
    const pointer = document.createElement("div");
    pointer.id = "demo-pointer";
    pointer.innerHTML =
      '<svg width="30" height="30" viewBox="0 0 30 30"><path d="M6 3.5l17.5 11-7.9 1.8 4.5 8.7-3.6 1.8-4.5-8.8L6 23.5z" fill="#1f1c18" stroke="#fffdf9" stroke-width="1.7" stroke-linejoin="round"/></svg>';
    Object.assign(pointer.style, { position: "fixed", left: "0", top: "0", zIndex: "2147483647", pointerEvents: "none", opacity: "0", filter: "drop-shadow(0 2px 3px rgba(31,28,24,.3))" });
    const ring = document.createElement("div");
    Object.assign(ring.style, { position: "fixed", left: "0", top: "0", width: "40px", height: "40px", borderRadius: "50%", border: "3px solid #2d5bd3", zIndex: "2147483646", pointerEvents: "none", opacity: "0" });
    document.documentElement.append(ring, pointer);
    const place = (x, y) => {
      pointer.style.transform = `translate(${x - 6}px, ${y - 3.5}px)`;
      pointer.style.opacity = "1";
      sessionStorage.setItem("demo-pointer", JSON.stringify({ x, y }));
    };
    const saved = JSON.parse(sessionStorage.getItem("demo-pointer") || "null");
    if (saved) place(saved.x, saved.y);
    addEventListener("mousemove", (event) => place(event.clientX, event.clientY), true);
    addEventListener(
      "mousedown",
      (event) => {
        const at = `translate(${event.clientX - 20}px, ${event.clientY - 20}px)`;
        ring.animate(
          [
            { opacity: 0.7, transform: `${at} scale(0.35)` },
            { opacity: 0, transform: `${at} scale(1.3)` },
          ],
          { duration: 520, easing: "cubic-bezier(.2,.7,.3,1)" },
        );
      },
      true,
    );
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", draw);
  else draw();
}

let mouseAt = { x: 1500, y: 640 };

/** Moves the mouse to x, y on an ease-in-out curve, as a hand would. */
async function glide(page, x, y, ms) {
  const from = { ...mouseAt };
  const distance = Math.hypot(x - from.x, y - from.y);
  const duration = ms ?? Math.min(900, Math.max(350, distance * 0.9));
  const steps = Math.max(8, Math.round(duration / 16));
  for (let step = 1; step <= steps; step++) {
    const t = step / steps;
    const eased = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
    await page.mouse.move(from.x + (x - from.x) * eased, from.y + (y - from.y) * eased);
    await page.waitForTimeout(16);
  }
  mouseAt = { x, y };
}

/** Glides to the control's centre, pauses briefly and clicks it. */
async function press(page, locator) {
  await locator.waitFor();
  const box = await locator.boundingBox();
  await glide(page, box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(200);
  await page.mouse.down();
  await page.waitForTimeout(90);
  await page.mouse.up();
}

// The storyboard of design/export/Media Demo Storyboard.dc.html: cards are rendered
// frames, app shots are recorded live against the running app with the sandbox. An app
// shot calls ready() once its screen is shown; its clip, and so its caption, starts there.
const SHOTS = [
  { card: ["Media Demo Title.dc.html", ""], seconds: 4 },
  {
    app: async (page, ready) => {
      await page.goto(APP);
      const open = page.getByRole("button", { name: "Open the sandbox" }).first();
      await open.waitFor();
      await ready();
      await page.waitForTimeout(600);
      await press(page, open);
      await page.waitForURL(/\/app\/inbox/);
    },
    seconds: 5,
    caption: "No sign-up: open the sandbox",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=1"], seconds: 3 },
  {
    app: async (page, ready) => {
      await press(page, page.getByRole("tab", { name: /^All/ }));
      await page.getByRole("row").filter({ hasText: "RE-2026-0413" }).waitFor();
      await glide(page, 1180, 330, 500);
      await ready();
      await page.waitForTimeout(500);
      await glide(page, 1180, 860, 5200);
    },
    seconds: 8,
    caption: "Twelve invoices, each with a verdict",
  },
  {
    app: async (page, ready) => {
      await ready();
      await press(page, page.getByRole("button", { name: "Upload invoices" }).first());
      await page.waitForTimeout(500);
      await page.setInputFiles('input[type="file"]', SAMPLE_PDF);
      await page.waitForTimeout(7500);
      await page.keyboard.press("Escape");
    },
    seconds: 10,
    caption: "Watch it detect, validate, read, check",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=2"], seconds: 3 },
  {
    app: async (page, ready) => {
      await page.goto(`${APP}/app/inbox?tab=all`);
      // The seeded plain PDF (sample S08), not the copy uploaded in the shot before.
      await press(page, page.getByRole("row").filter({ hasText: "Druckerei Sommer" }).last());
      await page.getByRole("heading", { name: "Fields" }).waitFor();
      await ready();
      await page.waitForTimeout(700);
      const eye = page.getByRole("button", { name: "Show where IBAN came from" });
      await scrollPane(page, eye);
      await press(page, eye);
      const evidence = page.getByRole("group", { name: "Where IBAN came from" });
      await evidence.waitFor();
      await page.waitForTimeout(2600);
      await press(page, evidence.getByRole("button", { name: "Show in document" }));
      await page.evaluate(() => window.scrollTo(0, 0));
    },
    seconds: 10,
    caption: "Every AI value shows its source",
  },
  {
    app: async (page, ready) => {
      const edit = page.getByRole("button", { name: "Edit Supplier VAT ID" });
      await scrollPane(page, edit);
      await ready();
      await press(page, edit);
      await page.getByLabel("Supplier VAT ID").fill("");
      await page.getByLabel("Supplier VAT ID").pressSequentially("DE360588120", { delay: 90 });
      await page.waitForTimeout(500);
      await press(page, page.getByRole("button", { name: "Save", exact: true }));
      await page.evaluate(() => window.scrollTo(0, 0));
    },
    seconds: 6,
    caption: "Low confidence? Type it from paper",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=3"], seconds: 3 },
  {
    app: async (page, ready) => {
      await page.goto(`${APP}/app/inbox?tab=all`);
      await press(page, page.getByRole("row").filter({ hasText: "RE-2026-0413" }));
      const official = page.getByRole("button", { name: "Show official message" }).first();
      await official.waitFor();
      await ready();
      await page.waitForTimeout(800);
      await press(page, official);
      await page.waitForTimeout(2200);
      await press(page, page.getByRole("button", { name: "Show in XML" }).first());
      await page.evaluate(() => window.scrollTo(0, 0));
    },
    seconds: 8,
    caption: "Rule BR-DE-15, explained in plain English",
  },
  {
    app: async (page, ready) => {
      await page.goto(`${APP}/app/inbox?tab=all`);
      await press(page, page.getByRole("row").filter({ hasText: "BN-88290" }));
      const resolve = page.locator(".card").filter({ hasText: "C05" }).getByRole("button", { name: "Resolve…" });
      await resolve.waitFor();
      await ready();
      await page.waitForTimeout(700);
      await press(page, resolve);
      await page.getByLabel("Note").pressSequentially("Checked with the supplier on the number in our files.", { delay: 40 });
      await press(page, page.getByRole("button", { name: "Resolve check" }));
      await page.waitForTimeout(500);
      await press(page, page.getByRole("button", { name: "Mark reviewed" }));
    },
    seconds: 11,
    caption: "New bank account? Check before paying",
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=4"], seconds: 3 },
  {
    app: async (page, ready) => {
      await page.goto(`${APP}/app/approvals`);
      const row = page.locator("li, tr").filter({ hasText: "BN-88290" }).first();
      await row.waitFor();
      await ready();
      await page.waitForTimeout(900);
      await press(page, row.getByRole("button", { name: "Approve" }));
      // The button grows into "Approving…" and wraps; the pointer moves off the row.
      await page.waitForTimeout(250);
      await glide(page, mouseAt.x - 260, mouseAt.y + 170, 700);
    },
    seconds: 6,
    caption: "Then someone approves it",
  },
  {
    app: async (page, ready) => {
      await page.goto(`${APP}/app/exports`);
      const csv = page.locator("label").filter({ hasText: "CSV, one row per invoice" }).filter({ hasNotText: "invoice line" });
      await csv.first().waitFor();
      await ready();
      await page.waitForTimeout(700);
      await press(page, csv.first());
      await page.waitForTimeout(300);
      await press(page, page.getByRole("button", { name: /^Export \d+ invoices?$/ }));
    },
    seconds: 7,
    caption: "One CSV for the tax advisor",
  },
  { card: ["Media Demo End.dc.html", "", { "sandbox URL": "eingang.vercel.app", "GitHub repository URL": "github.com/alihdrndm/eingang" }], seconds: 5 },
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

// Length of each cross-dissolve in seconds, and a near-lossless codec for the parts.
const FADE = 0.6;
const SEGMENT_CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p"];

async function demo(browser, design) {
  needFfmpeg();
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: path.join(TMP, "app"), size: { width: 1920, height: 1080 } },
    acceptDownloads: true,
  });
  await context.addInitScript(drawPointer);
  const app = await context.newPage();
  const recordingStart = Date.now(); // the page's video starts with the page, not with the first shot
  const parts = [];
  for (const [index, shotSpec] of SHOTS.entries()) {
    const segment = path.join(TMP, `seg-${String(index + 1).padStart(2, "0")}.mp4`);
    if (shotSpec.card) {
      const png = path.join(TMP, `card-${index + 1}.png`);
      await shot(browser, design, shotSpec.card[0], png, 1920, 1080, shotSpec.card[1], shotSpec.card[2]);
      ffmpeg(["-loop", "1", "-t", String(shotSpec.seconds + FADE), "-i", png, "-r", "30", ...SEGMENT_CODEC, segment]);
    } else {
      let started;
      await shotSpec.app(app, async () => {
        await app.waitForLoadState("networkidle");
        await app.waitForTimeout(500);
        started = Date.now();
      });
      if (!started) throw new Error(`Shot ${index + 1} ("${shotSpec.caption}") never showed its screen`);
      // A slow shot (opening a sandbox seeds twelve invoices) keeps its last seconds.
      const hold = shotSpec.seconds + FADE;
      const used = (Date.now() - started) / 1000;
      if (used > hold) started = Date.now() - hold * 1000;
      else await app.waitForTimeout((hold - used) * 1000);
      shotSpec.span = [started, Date.now()];
      // Idle time, so the next shot's navigation stays out of this clip's dissolve.
      await app.waitForTimeout(1200);
    }
    parts.push({ shotSpec, segment });
  }
  const recordingEnd = Date.now();
  await context.close();
  const raw = path.join(TMP, "app", readdirSync(path.join(TMP, "app")).find((name) => name.endsWith(".webm")));
  // The browser starts writing the video a moment after the page opens; without this
  // offset every clip ends on the first second or two of the next screen.
  const length = Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", raw]).toString());
  const measured = (recordingEnd - recordingStart) / 1000 - length;
  // The webm's duration is not always written; one second matches what the frames show.
  const lag = Number.isFinite(measured) && measured > 0.2 ? measured : 1;
  console.log(`video ${length} s, wall ${((recordingEnd - recordingStart) / 1000).toFixed(2)} s, lag ${lag.toFixed(2)} s`);
  for (const { shotSpec, segment } of parts) {
    if (!shotSpec.span) continue;
    const caption = segment.replace(".mp4", "-cap.png");
    await captionPng(browser, shotSpec.caption, caption);
    const from = Math.max(0, (shotSpec.span[0] - recordingStart) / 1000 - lag).toFixed(2);
    // The caption fades in once the dissolve into this shot is over and out before the next one.
    const length = shotSpec.seconds + FADE;
    const captionFade = `fade=t=in:st=${FADE}:d=0.35:alpha=1,fade=t=out:st=${(length - FADE - 0.35).toFixed(2)}:d=0.35:alpha=1`;
    ffmpeg([
      "-ss", from, "-t", String(length), "-i", raw,
      "-loop", "1", "-t", String(length), "-i", caption,
      "-filter_complex", `[0:v]fps=30,scale=1920:1080,setsar=1[v];[1:v]format=rgba,${captionFade}[c];[v][c]overlay=0:0:shortest=1`,
      ...SEGMENT_CODEC, "-an", segment,
    ]);
  }
  // Every cut is a cross-dissolve; the film fades in from black and out to black.
  const inputs = parts.flatMap((part) => ["-i", part.segment]);
  const filters = parts.map((_, index) => `[${index}:v]settb=AVTB,fps=30,format=yuv420p[s${index}]`);
  let previous = "s0";
  let offset = 0;
  parts.forEach((part, index) => {
    if (index === 0) return;
    offset += parts[index - 1].shotSpec.seconds;
    filters.push(`[${previous}][s${index}]xfade=transition=fade:duration=${FADE}:offset=${offset.toFixed(2)}[x${index}]`);
    previous = `x${index}`;
  });
  const total = parts.reduce((sum, part) => sum + part.shotSpec.seconds, 0) + FADE;
  filters.push(`[${previous}]fade=t=in:st=0:d=0.6,fade=t=out:st=${(total - 0.9).toFixed(2)}:d=0.9[out]`);
  ffmpeg([...inputs, "-filter_complex", filters.join(";"), "-map", "[out]", "-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", path.join(OUT, "demo.mp4")]);
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
} catch (error) {
  // Print the real failure first: cleaning up after a broken recording can fail as well.
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser.close();
  server.close();
  rmSync(TMP, { recursive: true, force: true });
}
