// pnpm record-media [stills|hero|demo|all]: renders the README media kit into docs/media/.
// Started from design/export/record-media.mjs. The design pages are served from
// design/export by a small built-in HTTP server (the hero pages load .jsx files, which do
// not work from file:// URLs). `stills` needs only Chromium; `hero` and `demo` also need
// ffmpeg on PATH, and `demo` needs the app running (`pnpm dev` with `pnpm seed`).
//
//   APP_URL=http://localhost:3110 pnpm record-media all
import { execFileSync, spawnSync } from "node:child_process";
import { createReadStream, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
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
const UBL_FIXTURE = path.join(root, "backend", "tests", "fixtures", "invoices", "invoice.ubl.xml");
const NEW_INVOICE = path.join(TMP, "RE-2026-0431.xml");
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
  // Each step goes where the pointer should be by now, so a slow step shortens the
  // path's resolution, not the glide: it lasts `duration` on any machine.
  const started = Date.now();
  for (;;) {
    const t = Math.min(1, (Date.now() - started) / duration);
    const eased = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
    await page.mouse.move(from.x + (x - from.x) * eased, from.y + (y - from.y) * eased);
    if (t === 1) break;
    await page.waitForTimeout(10);
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

// The storyboard of design/export/Media Demo Storyboard.dc.html. Cards are rendered
// frames. A scene is one continuous recording of the running app, cut into beats: a beat
// starts with beat(caption) once its screen has settled and ends where the next beat
// starts (a seamless cut, only the caption changes) or at skip(), which leaves out what
// follows, such as a page load, and joins the next beat with a short dissolve.
const TITLE = { "Eingang — a supplier-invoice inbox for German e-invoicing": "A supplier-invoice inbox for German e-invoicing" };
const LINKS = { "sandbox URL": "eingang.vercel.app", "GitHub repository URL": "github.com/alihdrndm/eingang" };

const SHOTS = [
  { card: ["Media Demo Title.dc.html", "", TITLE], seconds: 3.6 },
  { card: ["Media Demo Chapters.dc.html", "?ch=1"], seconds: 2.6 },
  {
    scene: async (page, beat, skip) => {
      await page.goto(APP);
      const open = page.getByRole("button", { name: "Open the sandbox" }).first();
      await open.waitFor();
      await beat("No sign-up: open the sandbox");
      await page.waitForTimeout(700);
      await press(page, open);
      await page.waitForTimeout(700);
      // Opening a sandbox seeds twelve invoices; the wait is left out.
      await skip();
      await page.waitForURL(/\/app\/inbox/);
      await page.getByRole("tab", { name: /^All/ }).waitFor();
      await beat("No sign-up: open the sandbox");
      await page.waitForTimeout(900);
      await press(page, page.getByRole("tab", { name: /^All/ }));
      await page.getByRole("row").filter({ hasText: "SW-2026-77812" }).waitFor();
      await beat("Twelve invoices, each with a verdict", { settle: false });
      await glide(page, 1180, 300, 600);
      await glide(page, 1180, 830, 3000);
      await page.waitForTimeout(500);
      await beat("A new invoice: detected, validated, checked", { settle: false });
      await press(page, page.getByRole("button", { name: "Upload invoices" }).first());
      await page.waitForTimeout(400);
      await page.setInputFiles('input[type="file"]', NEW_INVOICE);
      const dialog = page.getByRole("dialog", { name: "Upload invoices" });
      await dialog.getByText("Processing").waitFor();
      await page.waitForTimeout(1500);
      // The worker takes several seconds; the middle of the wait is left out.
      await skip();
      await dialog.getByText(/Needs review|Awaiting approval/).waitFor({ timeout: 30000 });
      await beat("A new invoice: detected, validated, checked", { settle: false });
      await page.waitForTimeout(1600);
      await press(page, dialog.getByRole("button", { name: "Close", exact: true }).last());
      await page.getByRole("row").filter({ hasText: "RE-2026-0431" }).waitFor();
      await glide(page, 760, 470, 700);
      await page.waitForTimeout(1400);
    },
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=2"], seconds: 2.6 },
  {
    scene: async (page, beat) => {
      await page.goto(`${APP}/app/inbox?tab=all`);
      // The seeded plain PDF (sample S08), read by the model when the samples were built.
      await press(page, page.getByRole("row").filter({ hasText: "Druckerei Sommer" }).last());
      await page.getByRole("heading", { name: "Fields" }).waitFor();
      await beat("Every AI value shows its source");
      await page.waitForTimeout(900);
      const eye = page.getByRole("button", { name: "Show where IBAN came from" });
      await scrollPane(page, eye);
      await press(page, eye);
      const evidence = page.getByRole("group", { name: "Where IBAN came from" });
      await evidence.waitFor();
      await page.waitForTimeout(1800);
      await press(page, evidence.getByRole("button", { name: "Show in document" }));
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(1800);
      const edit = page.getByRole("button", { name: "Edit Supplier VAT ID" });
      await scrollPane(page, edit);
      await beat("Low confidence? Type it from paper", { settle: false });
      await press(page, edit);
      await page.getByLabel("Supplier VAT ID").fill("");
      await page.getByLabel("Supplier VAT ID").pressSequentially("DE360588120", { delay: 85 });
      await page.waitForTimeout(400);
      await press(page, page.getByRole("button", { name: "Save", exact: true }));
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(1300);
    },
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=3"], seconds: 2.6 },
  {
    scene: async (page, beat, skip) => {
      await page.goto(`${APP}/app/inbox?tab=all`);
      await press(page, page.getByRole("row").filter({ hasText: "RE-2026-0413" }));
      const official = page.getByRole("button", { name: "Show official message" }).first();
      await official.waitFor();
      await beat("Rule BR-DE-15, explained in plain English");
      await page.waitForTimeout(1300);
      await press(page, official);
      await page.waitForTimeout(1900);
      await press(page, page.getByRole("button", { name: "Show in XML" }).first());
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(2000);
      await skip();
      await page.goto(`${APP}/app/inbox?tab=all`);
      await press(page, page.getByRole("row").filter({ hasText: "BN-88290" }));
      const resolve = page.locator(".card").filter({ hasText: "C05" }).getByRole("button", { name: "Resolve…" });
      await resolve.waitFor();
      await beat("New bank account? Check before paying");
      await page.waitForTimeout(1200);
      await press(page, resolve);
      await page.getByLabel("Note").pressSequentially("Checked with the supplier on the number in our files.", { delay: 35 });
      await page.waitForTimeout(300);
      await press(page, page.getByRole("button", { name: "Resolve check" }));
      await page.waitForTimeout(900);
      await press(page, page.getByRole("button", { name: "Mark reviewed" }));
      await page.waitForTimeout(1500);
    },
  },
  { card: ["Media Demo Chapters.dc.html", "?ch=4"], seconds: 2.6 },
  {
    scene: async (page, beat, skip) => {
      await page.goto(`${APP}/app/approvals`);
      const row = page.locator("li, tr").filter({ hasText: "BN-88290" }).first();
      await row.waitFor();
      await beat("Then someone approves it");
      await page.waitForTimeout(1100);
      await press(page, row.getByRole("button", { name: "Approve" }));
      // The button grows into "Approving…" and wraps; the pointer moves off the row.
      await page.waitForTimeout(250);
      await glide(page, mouseAt.x - 260, mouseAt.y + 170, 700);
      await page.waitForTimeout(1300);
      await skip();
      await page.goto(`${APP}/app/exports`);
      const csv = page.locator("label").filter({ hasText: "CSV, one row per invoice" }).filter({ hasNotText: "invoice line" }).first();
      await csv.waitFor();
      await beat("One CSV for the tax advisor");
      await page.waitForTimeout(900);
      await press(page, csv);
      await page.waitForTimeout(300);
      await press(page, page.getByRole("button", { name: /^Export \d+ invoices?$/ }));
      await page.waitForTimeout(2200);
    },
  },
  { card: ["Media Demo End.dc.html", "", LINKS], seconds: 4.6 },
];

/** A caption as a transparent PNG, in the self-hosted Instrument Sans (no font CDN). */
async function captionPng(browser, text, out) {
  const font = path.join(root, "frontend", "node_modules", "@fontsource", "instrument-sans", "files", "instrument-sans-latin-600-normal.woff2");
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const fontUrl = existsSync(font) ? `data:font/woff2;base64,${(await import("node:fs")).readFileSync(font).toString("base64")}` : "";
  await page.setContent(
    `<html><head><style>@font-face{font-family:Caption;font-weight:600;src:url(${fontUrl}) format("woff2")}</style></head>` +
      `<body style="margin:0;background:transparent"><div style="position:absolute;left:0;right:0;bottom:64px;display:grid;justify-items:center">` +
      `<div style="padding:14px 30px;background:rgba(31,28,24,.94);border-radius:10px;box-shadow:0 10px 30px rgba(31,28,24,.22);font:600 36px/1.25 Caption,sans-serif;letter-spacing:-.005em;color:#fffdf9">${text}</div></div></body></html>`,
  );
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: out, omitBackground: true });
  await page.close();
}

// Joins: a dissolve into and out of a card, a short dissolve over a left-out stretch, and
// a seamless cut between beats of one continuous recording. Captions swap in CAPTION_FADE.
const JOIN = { dissolve: 0.6, quick: 0.4, cut: 0 };
const CAPTION_FADE = 0.25;
const SEGMENT_CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p"];

async function demo(browser, design) {
  needFfmpeg();
  rmSync(TMP, { recursive: true, force: true });
  mkdirSync(TMP, { recursive: true });
  // An invoice the sandbox has not seen, so the upload runs every step instead of
  // reporting a duplicate: the test invoice with a new number.
  writeFileSync(NEW_INVOICE, readFileSync(UBL_FIXTURE, "utf8").replaceAll("RE-2026-0412", "RE-2026-0431"));
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: path.join(TMP, "app"), size: { width: 1920, height: 1080 } },
    acceptDownloads: true,
  });
  await context.addInitScript(drawPointer);
  const app = await context.newPage();
  const recordingStart = Date.now(); // the page's video starts with the page, not with the first shot
  // A black screen with a known end on this clock; finding its end in the video gives the
  // offset between the two clocks. The video starts with the first painted page, and up to
  // a few seconds later on a cold browser, so the white lead-in is long.
  await app.setContent('<body style="margin:0;background:#fffdf9"></body>');
  await app.waitForTimeout(3500);
  await app.setContent('<body style="margin:0;background:#000"></body>');
  await app.waitForTimeout(1500);
  const markerEnd = (Date.now() - recordingStart) / 1000;
  await app.setContent('<body style="margin:0;background:#fffdf9"></body>');
  const parts = [];
  for (const [index, shotSpec] of SHOTS.entries()) {
    if (shotSpec.card) {
      const png = path.join(TMP, `card-${index + 1}.png`);
      await shot(browser, design, shotSpec.card[0], png, 1920, 1080, shotSpec.card[1], shotSpec.card[2]);
      parts.push({ card: png, length: shotSpec.seconds, join: "dissolve" });
      continue;
    }
    let open = null;
    let join = "dissolve";
    const close = () => {
      if (!open) return;
      open.end = Date.now();
      parts.push(open);
      open = null;
    };
    const beat = async (caption, { settle = true } = {}) => {
      if (settle) {
        await app.waitForLoadState("networkidle");
        await app.waitForTimeout(400);
      }
      const contiguous = open !== null;
      close();
      open = { caption, start: Date.now(), join: contiguous ? "cut" : join };
      join = "quick";
    };
    const skip = async () => close();
    await shotSpec.scene(app, beat, skip);
    close();
    if (join === "dissolve") throw new Error(`Scene ${index + 1} never started a beat`);
    // Idle time, so the next scene's navigation stays out of this scene's last frames.
    await app.waitForTimeout(1000);
  }
  await context.close();
  const raw = path.join(TMP, "app", readdirSync(path.join(TMP, "app")).find((name) => name.endsWith(".webm")));
  // The browser starts writing the video a moment after the page opens; without this
  // offset every clip would end on the first frames of what comes next.
  const detect = spawnSync("ffmpeg", ["-hide_banner", "-t", "8", "-i", raw, "-vf", "blackdetect=d=0.5:pix_th=0.05", "-an", "-f", "null", "-"], { encoding: "utf8" });
  const blackEnd = Number(/black_end:([\d.]+)/.exec(detect.stderr)?.[1]);
  if (!Number.isFinite(blackEnd)) throw new Error("The clock marker was not found in the recording");
  const lag = markerEnd - blackEnd;
  console.log(`clock offset ${lag.toFixed(3)} s`);

  const captions = new Map();
  for (const [index, part] of parts.entries()) {
    part.file = path.join(TMP, `part-${String(index + 1).padStart(2, "0")}.mp4`);
    if (part.card) {
      ffmpeg(["-loop", "1", "-t", String(part.length), "-i", part.card, "-r", "30", ...SEGMENT_CODEC, part.file]);
      continue;
    }
    part.length = (part.end - part.start) / 1000;
    if (!captions.has(part.caption)) {
      const png = path.join(TMP, `caption-${captions.size + 1}.png`);
      await captionPng(browser, part.caption, png);
      captions.set(part.caption, png);
    }
    // The caption fades in after the join into this beat and out before the join out of
    // it, unless the neighbouring beat carries the same caption.
    const next = parts[index + 1];
    const fades = [];
    if (part.join === "cut" ? parts[index - 1].caption !== part.caption : !(part.join === "quick" && parts[index - 1].caption === part.caption)) {
      fades.push(`fade=t=in:st=${JOIN[part.join]}:d=${CAPTION_FADE}:alpha=1`);
    }
    if (next && next.caption !== part.caption) {
      const outEnd = part.length - JOIN[next.join];
      fades.push(`fade=t=out:st=${(outEnd - CAPTION_FADE).toFixed(2)}:d=${CAPTION_FADE}:alpha=1`);
    }
    const captionFilter = ["format=rgba", ...fades].join(",");
    const from = Math.max(0, (part.start - recordingStart) / 1000 - lag).toFixed(3);
    ffmpeg([
      "-ss", from, "-t", part.length.toFixed(3), "-i", raw,
      "-loop", "1", "-t", part.length.toFixed(3), "-i", captions.get(part.caption),
      "-filter_complex", `[0:v]fps=30,scale=1920:1080,setsar=1[v];[1:v]${captionFilter}[c];[v][c]overlay=0:0:shortest=1`,
      ...SEGMENT_CODEC, "-an", part.file,
    ]);
    // Encoded length: the clip is cut on whole frames.
    part.length = Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", part.file]).toString());
  }

  if (process.env.DEMO_DEBUG) for (const part of parts) console.log(part.card ? "card" : part.caption, part.join, part.length.toFixed(2));
  const inputs = parts.flatMap((part) => ["-i", part.file]);
  const filters = parts.map((_, index) => `[${index}:v]settb=AVTB,fps=30,format=yuv420p[s${index}]`);
  let previous = "s0";
  let total = parts[0].length;
  parts.forEach((part, index) => {
    if (index === 0) return;
    const overlap = JOIN[part.join];
    if (overlap === 0) filters.push(`[${previous}][s${index}]concat=n=2:v=1:a=0,settb=AVTB,fps=30[x${index}]`);
    else filters.push(`[${previous}][s${index}]xfade=transition=fade:duration=${overlap}:offset=${(total - overlap).toFixed(3)}[x${index}]`);
    total += part.length - overlap;
    previous = `x${index}`;
  });
  filters.push(`[${previous}]fade=t=in:st=0:d=0.6,fade=t=out:st=${(total - 0.9).toFixed(3)}:d=0.9[out]`);
  ffmpeg([...inputs, "-filter_complex", filters.join(";"), "-map", "[out]", "-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", path.join(OUT, "demo.mp4")]);
  console.log(`✓ demo.mp4 (${total.toFixed(1)} s, ${parts.length} parts)`);
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
  // A failed run keeps its parts in docs/media/.tmp for a look; the next run starts clean.
  if (!process.exitCode) rmSync(TMP, { recursive: true, force: true });
}
