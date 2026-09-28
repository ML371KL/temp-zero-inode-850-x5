// Снимки витрины X5: headless Chrome по CDP (встроенная панель
// «Браузер» Claude Desktop роняет приложение). Шесть экранов × 1280/375 px ×
// светлая/тёмная тема + проба ползунка λ. В JSON — горизонтальная прокрутка,
// переносы чисел, переполнения, наложения подписей SVG, ошибки консоли и CSP.
//
//   TAG=local BASE_URL=http://127.0.0.1:8872 OUT_ROOT=../x5-850-handoff/shots node ops/tools/shots.mjs
//   (локально: python ops/tools/devserver.py 8872 [выпуск.json]; образец — снимки Магнита 850oa)
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const TAG = process.env.TAG || "local";
const ROOT = process.env.OUT_ROOT || ".";
const OUT = path.join(ROOT, TAG);
mkdirSync(OUT, { recursive: true });
const PORT = +(process.env.CDP_PORT || 9481);
const BASE = process.env.BASE_URL || "http://127.0.0.1:8872";
const SCREENS = (process.env.SCREENS || "overview,market,model,report,debt,book").split(",");
const VIEWS = (process.env.VIEWS || "1280,375").split(",");
const THEMES = (process.env.THEMES || "light,dark").split(",");
const MAXH = +(process.env.MAXH || 9000);
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PROFILE = path.join(process.env.TEMP || ".", "chrome-profile-x5-850-" + TAG);

const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${PROFILE}`, "--no-first-run", "--no-default-browser-check",
  "--disable-extensions", "--hide-scrollbars", "--force-color-profile=srgb", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function wsUrl() {
  for (let i = 0; i < 80; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); return (await r.json()).webSocketDebuggerUrl; }
    catch { await sleep(200); }
  }
  throw new Error("chrome did not start");
}

let ws, nextId = 1;
const pending = new Map();
const events = [];
function send(method, params = {}, sessionId) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return new Promise((res, rej) => pending.set(id, { res, rej }));
}

const MEASURE = `(() => {
  const de = document.documentElement;
  const t = (sel) => { const e = document.querySelector(sel); return e ? e.innerText.replace(/\\s+/g, " ").trim() : null; };
  let wrapped = 0; const wrappedSamples = [];
  for (const td of document.querySelectorAll("td.num")) {
    const range = document.createRange(); range.selectNodeContents(td);
    const lines = new Set([...range.getClientRects()].map(r => Math.round(r.top)));
    if (lines.size > 1) { wrapped++; if (wrappedSamples.length < 6) wrappedSamples.push(td.textContent.slice(0, 30)); }
  }
  const overflow = [];
  for (const e of document.querySelectorAll("#app *")) {
    if (e.closest(".scroll") || e instanceof SVGElement || e.closest("svg")) continue;
    const cs = getComputedStyle(e);
    // Таблица едет в .scroll с отрицательными полями карточки (−24/−16 px) и
    // стрелка шага потока (::after) — это не переполнение, а замысел.
    const slack = (e.querySelector(".scroll") ? 26 : 0) + (e.classList.contains("flow-step") ? 12 : 0);
    if (e.scrollWidth > e.clientWidth + 1 + slack && cs.overflowX === "visible" && e.clientWidth > 0 && cs.display !== "inline") {
      if (overflow.length < 10) overflow.push((e.className || e.tagName) + ": " + e.scrollWidth + ">" + e.clientWidth
        + " «" + e.innerText.replace(/\\s+/g, " ").slice(0, 60) + "…»");
    }
  }
  const cardOverflow = [...document.querySelectorAll("#app .card")].filter(c => c.getBoundingClientRect().right > innerWidth + 0.5).map(c => (c.querySelector("h2") || {}).textContent);
  const rects = [...document.querySelectorAll("svg text")].filter(x => x.getBoundingClientRect().width > 0)
    .map(x => ({ t: x.textContent.slice(0, 32), r: x.getBoundingClientRect(), svg: x.ownerSVGElement.getBoundingClientRect(), fs: parseFloat(getComputedStyle(x).fontSize) }));
  const overlaps = [];
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i].r, b = rects[j].r;
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) overlaps.push([rects[i].t, rects[j].t]);
  }
  const clipped = rects.filter(x => x.r.left < x.svg.left - 1 || x.r.right > x.svg.right + 1 || x.r.top < x.svg.top - 2 || x.r.bottom > x.svg.bottom + 2).map(x => x.t);
  const tiny = rects.filter(x => x.fs < 12.4).map(x => x.t + " " + x.fs);
  const smallText = [...document.querySelectorAll("#app *")].filter(e => e.children.length === 0 && e.textContent.trim() && !(e instanceof SVGElement) && parseFloat(getComputedStyle(e).fontSize) < 12.4).slice(0, 5).map(e => e.textContent.slice(0, 30));
  return {
    hash: location.hash,
    tab: ([...document.querySelectorAll(".tab")].find(x => x.getAttribute("aria-selected") === "true") || {}).textContent || null,
    theme: de.dataset.theme,
    cards: document.querySelectorAll("#app .card").length,
    broken: /Экран не отрисовался|График не отрисовался/.test(document.querySelector("#app").innerText),
    empties: [...document.querySelectorAll("#app .empty")].map(e => e.textContent.slice(0, 90)),
    innerWidth, docScrollWidth: de.scrollWidth, height: de.scrollHeight,
    wrappedNum: wrapped, wrappedSamples, overflow, cardOverflow,
    svgOverlaps: overlaps.slice(0, 12), svgOverlapCount: overlaps.length, svgClipped: clipped.slice(0, 12), tiny: tiny.slice(0, 6), smallText,
    headline: t("#fv-headline"), bands: t(".bands"), verdict: t("#fv-ev"), lede: t("#fv-lede"),
    banners: [...document.querySelectorAll(".banner")].map(b => b.innerText.replace(/\\s+/g, " ").slice(0, 160)),
    chip: t("#release-chip"),
  };
})()`;

const SLIDER = (value) => `(() => {
  const input = document.querySelector('#lambda');
  if (!input) return null;
  input.value = ${JSON.stringify(value)};
  input.dispatchEvent(new Event('input'));
  const t = (s) => { const e = document.querySelector(s); return e ? e.innerText.replace(/\\s+/g, ' ').trim() : null; };
  return { lam: input.value, headline: t('#fv-headline'), bands: t('.bands'), out: t('.lambda-out'), lede: t('#fv-lede'), verdict: t('#fv-ev') };
})()`;

async function main() {
  ws = new WebSocket(await wsUrl());
  await new Promise((r) => (ws.onopen = r));
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id); pending.delete(msg.id);
      msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
    } else if (msg.method) events.push(msg);
  };
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const S = (m, p) => send(m, p, sessionId);
  await S("Page.enable"); await S("Runtime.enable"); await S("Log.enable");
  const evalJs = async (expression) => {
    const r = await S("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("eval: " + JSON.stringify(r.exceptionDetails).slice(0, 600));
    return r.result.value;
  };
  const report = { _base: BASE, _taken_at: new Date().toISOString() };
  const views = { "1280": { width: 1280, height: 900, dpr: 1, mobile: false },
                  "375": { width: 375, height: 812, dpr: 2, mobile: true },
                  "768": { width: 768, height: 1024, dpr: 1, mobile: false } };
  for (const name of VIEWS) {
    const v = views[name];
    await S("Emulation.setDeviceMetricsOverride", { width: v.width, height: v.height, deviceScaleFactor: v.dpr, mobile: v.mobile });
    for (const theme of THEMES) {
      await S("Page.navigate", { url: "about:blank" });
      await sleep(150);
      await S("Page.navigate", { url: `${BASE}/#overview` });
      await sleep(1400);
      await evalJs(`document.documentElement.dataset.theme=${JSON.stringify(theme)}; 1`);
      for (const screen of SCREENS) {
        await evalJs(`location.hash=${JSON.stringify("#" + screen)}; 1`);
        await sleep(700);
        const key = `${name}-${theme}-${screen}`;
        report[key] = await evalJs(MEASURE);
        const height = Math.min(await evalJs("document.documentElement.scrollHeight"), MAXH);
        await S("Emulation.setDeviceMetricsOverride", { width: v.width, height, deviceScaleFactor: v.dpr, mobile: v.mobile });
        await sleep(350);
        const shot = await S("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: v.width, height, scale: 1 } });
        writeFileSync(path.join(OUT, `${key}.png`), Buffer.from(shot.data, "base64"));
        // Длинный экран телефона — ещё и кусками по SEG px: так снимок читается глазами.
        const SEG = +(process.env.SEG || 0);
        if (SEG && v.mobile) {
          for (let y = 0, i = 1; y < height; y += SEG, i++) {
            const part = await S("Page.captureScreenshot", { format: "png", clip: { x: 0, y, width: v.width, height: Math.min(SEG, height - y), scale: 1 } });
            writeFileSync(path.join(OUT, `${key}-${i}.png`), Buffer.from(part.data, "base64"));
          }
        }
        await S("Emulation.setDeviceMetricsOverride", { width: v.width, height: v.height, deviceScaleFactor: v.dpr, mobile: v.mobile });
        await sleep(200);
      }
      if (SCREENS.includes("overview") && theme === "light") {
        await evalJs(`location.hash="#overview"; 1`);
        await sleep(500);
        const slider = {};
        for (const value of ["0", "1", "0.5"]) slider[value] = await evalJs(SLIDER(value));
        report[`${name}-slider`] = slider;
        await evalJs(SLIDER("1"));
        await sleep(300);
        const shot = await S("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: v.width, height: Math.min(1500, v.height * 2), scale: 1 } });
        writeFileSync(path.join(OUT, `${name}-light-overview-lambda1.png`), Buffer.from(shot.data, "base64"));
        await evalJs(SLIDER("0.5"));
      }
    }
  }
  report._console = events
    .filter(e => e.method === "Runtime.exceptionThrown"
      || (e.method === "Log.entryAdded" && ["error", "warning"].includes(e.params.entry.level))
      || (e.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(e.params.type)))
    .map(e => JSON.stringify(e.params).slice(0, 400));
  report._csp = report._console.filter(s => /Content Security Policy|CSP/i.test(s));
  const shots = Object.entries(report).filter(([k]) => !k.startsWith("_") && !k.endsWith("-slider"));
  report._summary = {
    screens: shots.length,
    horizontalScroll: shots.filter(([, v]) => v.docScrollWidth > v.innerWidth + 1).map(([k, v]) => `${k}: ${v.docScrollWidth}`),
    wrappedNumbers: shots.filter(([, v]) => v.wrappedNum).map(([k, v]) => `${k}: ${v.wrappedNum} ${v.wrappedSamples.join("|")}`),
    overflow: shots.filter(([, v]) => v.overflow.length || v.cardOverflow.length).map(([k, v]) => `${k}: ${v.overflow.concat(v.cardOverflow).join(" ; ")}`),
    svgOverlaps: shots.filter(([, v]) => v.svgOverlapCount).map(([k, v]) => `${k}: ${v.svgOverlapCount} ${JSON.stringify(v.svgOverlaps.slice(0, 4))}`),
    svgClipped: shots.filter(([, v]) => v.svgClipped.length).map(([k, v]) => `${k}: ${v.svgClipped.join("|")}`),
    tiny: shots.filter(([, v]) => v.tiny.length || v.smallText.length).map(([k, v]) => `${k}: ${v.tiny.concat(v.smallText).join("|")}`),
    broken: shots.filter(([, v]) => v.broken).map(([k]) => k),
    console: report._console.length, csp: report._csp.length,
  };
  writeFileSync(path.join(ROOT, `report-${TAG}.json`), JSON.stringify(report, null, 1));
  console.log(JSON.stringify(report._summary, null, 1));
  if (report._console.length) console.log(report._console.slice(0, 8).join("\n"));
  await send("Target.closeTarget", { targetId });
  await send("Browser.close").catch(() => {});
}

main().catch((e) => { console.error("FAIL", e); process.exitCode = 1; })
  .finally(async () => { await sleep(300); try { chrome.kill(); } catch {} });
