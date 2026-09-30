// Поведение витрины web/app.js без браузера: скрипт грузится в node:vm с
// минимальной заглушкой DOM (адрес разбирает WHATWG URL, как браузер; fetch
// отдаёт выпуск; ResizeObserver рисует графики в заданной ширине). Выпуск —
// мок tests/fixtures/sample_payload.json, для частных случаев — его копии с
// правками. Запускает tests/test_web_node.py; печатает JSON с итогами, код
// выхода 1 — если хоть одна проверка не прошла.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const root = new URL("../", import.meta.url);
const APP = readFileSync(new URL("web/app.js", root), "utf8");
const SAMPLE_TEXT = readFileSync(new URL("tests/fixtures/sample_payload.json", root), "utf8");
const sample = () => JSON.parse(SAMPLE_TEXT);
const SCREEN_NAMES = ["overview", "market", "model", "report", "debt", "book"];

const checks = [];
const check = (name, cond, detail) => checks.push({ name, ok: !!cond, detail: cond ? undefined : detail });

/* ── заглушка DOM ── */

class Text_ {
  constructor(value) { this.nodeType = 3; this.data = String(value); this.parentNode = null; }
  get textContent() { return this.data; }
  set textContent(value) { this.data = String(value); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
}

class Element_ {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.parentNode = null;
    this.hidden = false;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; }
  hasAttribute(key) { return key in this.attrs; }
  removeAttribute(key) { delete this.attrs[key]; }
  append(...kids) {
    for (const kid of kids) {
      const node = kid && kid.nodeType ? kid : new Text_(kid);
      if (node.parentNode) node.parentNode.removeChild(node);
      node.parentNode = this;
      this.children.push(node);
    }
  }
  appendChild(kid) { this.append(kid); return kid; }
  prepend(...kids) { const rest = this.children; this.children = []; this.append(...kids); this.children.push(...rest); }
  replaceChildren(...kids) { for (const c of this.children) c.parentNode = null; this.children = []; this.append(...kids); }
  removeChild(kid) { this.children = this.children.filter((c) => c !== kid); kid.parentNode = null; return kid; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener() {}
  dispatch(type, event = {}) { for (const fn of this.listeners[type] || []) fn({ preventDefault() {}, target: this, ...event }); }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); if (value !== "") this.append(new Text_(value)); }
  get className() { return this.attrs.class || ""; }
  contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  closest() { return null; }
  querySelector(selector) { return this.ownerDocument ? this.ownerDocument.querySelector(selector, this) : null; }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; }
  scrollIntoView() {}
  focus() {}
  get clientWidth() { return 0; }
  get offsetWidth() { return 0; }
  get offsetHeight() { return 0; }
}

// Видимый текст узла (скрытые узлы — таблица-двойник до нажатия — не в счёт).
function visibleText(node) {
  if (!node) return "";
  if (node.nodeType === 3) return node.data;
  if (node.hidden) return "";
  return node.children.map(visibleText).join(node.tagName === "P" || node.tagName === "DIV" || node.tagName === "LI" ? " " : "");
}
const countTag = (node, tag) => (node && node.nodeType === 1
  ? (node.tagName === tag ? 1 : 0) + node.children.reduce((n, c) => n + countTag(c, tag), 0) : 0);
const squash = (s) => String(s).replace(/[  ]/g, " ").replace(/\s+/g, " ").trim();

/* ── окружение страницы ── */

function makePage({ href = "https://tzi-850-x5.pages.dev/", payload = SAMPLE_TEXT, width = 640 } = {}) {
  const url = new URL(href);
  const errors = [];
  const observed = [];
  const windowListeners = {};
  const docListeners = {};
  const document = {
    readyState: "loading",
    title: "X5 — справедливая стоимость",
    createElement: (tag) => { const n = new Element_(tag); n.ownerDocument = document; return n; },
    createElementNS: (ns, tag) => { const n = new Element_(tag); n.ownerDocument = document; return n; },
    createTextNode: (value) => new Text_(value),
    addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
  };
  const node = (tag, attrs = {}) => { const n = document.createElement(tag); Object.assign(n.attrs, attrs); return n; };
  const app = node("main", { id: "app" });
  app.append(node("p", { class: "boot" }));
  app.children[0].append("Загружаем выпуск модели…");
  const chip = node("a", { id: "release-chip" });
  const chipText = node("span", { class: "chip-text" });
  chip.append(chipText);
  const colophon = node("p", { id: "colophon-release" });
  const tip = node("div", { id: "tip" });
  const tabs = SCREEN_NAMES.map((name) => { const t = node("button", { class: "tab" }); t.dataset.screen = name; t.append(name); return t; });
  document.documentElement = node("html");
  document.body = node("body");
  document.querySelector = (selector, scope) => {
    if (scope === chip && selector === ".chip-text") return chipText;
    const byId = { "#app": app, "#release-chip": chip, "#colophon-release": colophon, "#tip": tip };
    if (selector in byId) return byId[selector];
    const m = /^\.tab\[data-screen="(\w+)"\]$/.exec(selector);
    if (m) return tabs.find((t) => t.dataset.screen === m[1]) || null;
    return null;
  };
  document.querySelectorAll = (selector) => (selector === ".tab" ? tabs : []);
  const history = {
    pushState(state, title, hash) { url.hash = hash; },
    replaceState(state, title, hash) { url.hash = hash; },
  };
  const location = { get hash() { return url.hash; }, get href() { return url.href; } };
  const window = {
    addEventListener(type, fn) { (windowListeners[type] ||= []).push(fn); },
    scrollTo() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };
  class ResizeObserver {
    constructor(fn) { this.fn = fn; }
    observe(host) { observed.push({ host, fn: this.fn }); }
    unobserve() {}
    disconnect() {}
  }
  const canvas = { getContext: () => ({ font: "", measureText: (s) => ({ width: String(s).length * 7 }) }) };
  const createElement = document.createElement;
  document.createElement = (tag) => (tag === "canvas" ? canvas : createElement(tag));
  const ctx = {
    window, document, location, history, console: { error: (e) => errors.push(String((e && e.stack) || e)), log() {}, warn() {} },
    fetch: async () => ({ ok: true, status: 200, text: async () => payload }),
    ResizeObserver, AbortController, setTimeout, clearTimeout, URL, Intl, Date, Math, JSON, Promise,
    getComputedStyle: () => ({ fontFamily: "sans-serif", getPropertyValue: () => "" }),
    requestAnimationFrame: (fn) => setTimeout(fn, 0), innerWidth: 1280, innerHeight: 800,
  };
  window.document = document;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(APP, ctx, { filename: "web/app.js" });
  const page = {
    ctx, app, url, errors, chipText,
    get: (expr) => vm.runInContext(expr, ctx),
    // Графики, поставленные на наблюдение, рисуются в ширине `width` (как ResizeObserver).
    paint(w = width) {
      const batch = observed.splice(0);
      for (const { host, fn } of batch) fn([{ target: host, contentRect: { width: w } }]);
      return batch.length;
    },
    // Старт страницы; исключение старта (в браузере — «Uncaught (in promise)») — в bootError.
    bootError: null,
    async boot() {
      try {
        for (const fn of docListeners.DOMContentLoaded || []) await fn();
      } catch (error) {
        page.bootError = String(error);
      }
      page.paint();
    },
    hashchange(hash) { url.hash = hash; for (const fn of windowListeners.hashchange || []) fn(); page.paint(); },
    text: () => squash(visibleText(app)),
  };
  return page;
}

const rejections = [];
process.on("unhandledRejection", (reason) => rejections.push(String((reason && reason.stack) || reason)));
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

/* ── 1. адрес: битый или чужой хэш — «Оценка», без исключения ── */

{
  const page = makePage();
  const cases = [["#overview", "overview"], ["#book", "book"], ["#%62ook", "book"], ["", "overview"], ["#nope", "overview"],
    ["#overview?utm_source=tg", "overview"], ["#%E0%A4%A", "overview"], ["#%", "overview"], ["#100%", "overview"],
    ["#%FF", "overview"], ["#%D0", "overview"], ["#overview%", "overview"]];
  for (const [hash, want] of cases) {
    page.url.hash = hash;
    let got, error = null;
    try { got = page.get("screenFromHash()"); } catch (e) { error = String(e); }
    check(`screenFromHash ${JSON.stringify(hash)} → ${want}`, !error && got === want, error || got);
  }
}

for (const hash of ["#%E0%A4%A", "#%", "#100%", "#%D0"]) {
  const page = makePage({ href: `https://tzi-850-x5.pages.dev/${hash}` });
  const before = rejections.length;
  await page.boot();
  await settle();
  const text = page.text();
  check(`старт с ${hash}: «Оценка» отрисована`, page.get("CURRENT") === "overview" && !/Загружаем/.test(text) && /Оценка|медиан/i.test(text), text.slice(0, 160));
  check(`старт с ${hash}: адрес заменён на #overview`, page.url.hash === "#overview", page.url.hash);
  check(`старт с ${hash}: без исключений`, !page.bootError && rejections.length === before && !page.errors.length,
    [page.bootError, rejections.slice(before), page.errors.slice(0, 2)]);
}

{
  const page = makePage({ href: "https://tzi-850-x5.pages.dev/#book" });
  await page.boot();
  await settle();
  check("старт с #book — «Допущения»", !page.bootError && page.get("CURRENT") === "book", [page.bootError, page.get("CURRENT")]);
  let error = null;
  try { page.hashchange("#%E0%A4%A"); } catch (e) { error = String(e); }
  check("битый хэш после загрузки: без исключения", !error, error);
  check("битый хэш после загрузки: «Оценка» и адрес #overview", page.get("CURRENT") === "overview" && page.url.hash === "#overview", [page.get("CURRENT"), page.url.hash]);
  try { page.hashchange("#%"); } catch (e) { error = String(e); }
  check("битый хэш на «Оценке»: адрес исправлен, экран тот же", !error && page.url.hash === "#overview" && page.get("CURRENT") === "overview", [error, page.url.hash]);
  page.hashchange("#market");
  check("обычная ссылка после битого хэша — работает", page.get("CURRENT") === "market", page.get("CURRENT"));
  check("навигация без ошибок витрины", !page.errors.length, page.errors.slice(0, 2));
}

/* ── 2. все экраны мока рисуются, графики — в ширине карточки и телефона ── */

for (const width of [640, 343]) {
  const page = makePage({ width });
  await page.boot();
  await settle();
  for (const name of SCREEN_NAMES) {
    page.hashchange(`#${name}`);
    const text = page.text();
    check(`экран ${name} (${width} px): без ошибок`, page.get("CURRENT") === name && !page.errors.length
      && !/не отрисовал/.test(text), page.errors.slice(0, 2).concat(text.match(/.{0,80}не отрисовал.{0,80}/) || []));
    check(`экран ${name} (${width} px): графики нарисованы`, countTag(page.app, "SVG") > 0, countTag(page.app, "SVG"));
    page.errors.length = 0;
  }
}

const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ total: checks.length, failed }, null, 1));
process.exitCode = failed.length || rejections.length ? 1 : 0;
if (rejections.length) console.log(JSON.stringify({ unhandled: rejections.slice(0, 3) }, null, 1));
