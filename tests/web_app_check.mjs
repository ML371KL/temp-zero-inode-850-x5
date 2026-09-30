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

// Видимый текст узла (скрытые узлы — таблица-двойник до нажатия — не в счёт); блоки
// отделяются пробелом, строчные элементы — нет.
const BLOCKS = new Set(["P", "DIV", "LI", "UL", "OL", "H1", "H2", "SECTION", "TR", "TD", "TH", "CAPTION", "TABLE"]);
function visibleText(node) {
  if (!node) return "";
  if (node.nodeType === 3) return node.data;
  if (node.hidden) return "";
  const inner = node.children.map(visibleText).join("");
  return BLOCKS.has(node.tagName) || /display:\s*block/.test(node.attrs.style || "") ? ` ${inner} ` : inner;
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

/* ── 2. «Ближайший отчёт»: отсчёт — до МСФО, закрывающего полугодие заголовка ── */

// Выпуск с датой оценки `v`, полугодием `period` и событиями `events` (остальное — мок).
// `closing` — поле `next_report.closing` выпуска; не задано — поля нет (выпуск до 1.1.3:
// витрина ищет закрывающее МСФО прежним правилом среди `events`).
function withReport(v, period, events, closing) {
  const d = sample();
  d.meta.valuation_date = v;
  d.next_report.period = period;
  d.next_report.events = events;
  if (closing === undefined) delete d.next_report.closing;
  else d.next_report.closing = closing;
  return d;
}
const ev = (date, kind, title, confirmed = true) => ({ date, kind, title, confirmed, note: null });
const H2_2026 = [
  ev("2026-10-16", "trading_update", "Операционные результаты X5 за 3 кв. 2026 г."),
  ev("2026-10-23", "cbr", "Заседание Совета директоров Банка России по ключевой ставке"),
  ev("2026-10-29", "ifrs", "Финансовые результаты X5 за 3 кв. 2026 г. (МСФО)"),
  ev("2026-11-13", "dividend", "Рекомендация Наблюдательного совета по дивидендам за 9 мес. 2026 г.", false),
  ev("2027-01-28", "trading_update", "Операционные результаты X5 за 4 кв. и 2026 г.", false),
  ev("2027-03-19", "cbr", "Заседание Совета директоров Банка России по ключевой ставке", false),
  ev("2027-03-19", "ifrs", "Финансовые результаты X5 за 2026 г. (МСФО) и ориентиры на 2027 г.", false),
];
const H1_2027 = [
  ev("2027-04-16", "trading_update", "Операционные результаты X5 за 1 кв. 2027 г.", false),
  ev("2027-04-29", "ifrs", "Финансовые результаты X5 за 1 кв. 2027 г. (МСФО)", false),
  ev("2027-07-16", "trading_update", "Операционные результаты X5 за 2 кв. 2027 г.", false),
  ev("2027-07-30", "ifrs", "Финансовые результаты X5 за 1П 2027 г. (МСФО)", false),
];
{
  const page = makePage();
  const pick = (d) => {
    page.ctx.__d = d;
    const r = page.get("reportEvents(globalThis.__d)");
    return { closing: r.closing && r.closing.date, earlier: r.earlier && r.earlier.date };
  };
  const cases = [
    ["2П 2026 на 29.09: отсчёт — годовое МСФО, раньше — МСФО за 3 кв.", withReport("2026-09-29", "2026H2", H2_2026), "2027-03-19", "2026-10-29"],
    ["порядок событий в выпуске не важен", withReport("2026-09-29", "2026H2", H2_2026.slice().reverse()), "2027-03-19", "2026-10-29"],
    ["прошедшие события не в счёт: 20.10 — раньше всё ещё МСФО за 3 кв.", withReport("2026-10-20", "2026H2", H2_2026), "2027-03-19", "2026-10-29"],
    ["после МСФО за 3 кв.: раньше — операционные результаты за 4 кв.", withReport("2026-10-30", "2026H2", H2_2026), "2027-03-19", "2027-01-28"],
    ["после всех промежуточных: отсчёт без строки «раньше»", withReport("2027-01-29", "2026H2", H2_2026), "2027-03-19", null],
    ["1П 2027: отсчёт — МСФО за 1П, раньше — МСФО за 1 кв.", withReport("2027-03-25", "2027H1", H1_2027), "2027-07-30", "2027-04-29"],
    ["МСФО в последний день полугодия полугодие не закрывает", withReport("2026-09-29", "2026H2",
      [ev("2026-10-29", "ifrs", "МСФО за 3 кв."), ev("2026-12-31", "ifrs", "МСФО 31.12")]), null, "2026-10-29"],
    ["закрывающего МСФО в календаре нет: отсчёта нет, раньше — МСФО за 3 кв.", withReport("2026-09-29", "2026H2", H2_2026.slice(0, 5)), null, "2026-10-29"],
    ["до закрывающего только заседание ЦБ: строки «раньше» нет", withReport("2027-02-01", "2026H2", H2_2026), "2027-03-19", null],
  ];
  for (const [name, d, closing, earlier] of cases) {
    let got, error = null;
    try { got = pick(d); } catch (e) { error = String(e); }
    check(`событие отчёта: ${name}`, !error && got.closing === closing && got.earlier === earlier, error || got);
  }

  const d = withReport("2026-09-29", "2026H2", H2_2026);
  page.ctx.__d = d;
  const teaser = squash(visibleText(page.get("reportTeaser(globalThis.__d)")));
  // Число отсчёта и подпись — соседние строчные элементы (зазор даёт CSS).
  check("«Оценка»: отсчёт до годового МСФО", /171 ?день до ≈ 19 марта 2027/.test(teaser)
    && teaser.includes("Финансовые результаты X5 за 2026 г. (МСФО) и ориентиры на 2027 г."), teaser.slice(0, 300));
  check("«Оценка»: отсчёта до МСФО за 3 кв. нет", !teaser.includes("до 29 октября") && !/\b30 дней до/.test(teaser), teaser.slice(0, 300));
  check("«Оценка»: МСФО за 3 кв. — отдельной строкой с пояснением",
    teaser.includes("Раньше: 29.10.2026 (через 30 дней) — Финансовые результаты X5 за 3 кв. 2026 г. (МСФО).")
    && teaser.includes("Внутри полугодия: журнал не закрывает, медиану само не двигает; в цену — только новой версией книги (правило A-P2u)."),
    teaser.slice(0, 600));
  check("«Оценка»: ожидание модели — за всё полугодие", teaser.includes("ожидание модели: скорр. маржа за всё 2П 2026")
    && teaser.includes("Если маржа за всё 2П 2026 выйдет"), teaser.slice(0, 900));

  const cal = squash(visibleText(page.get("reportCalendarCard(globalThis.__d)")));
  check("#report: отсчёт до годового МСФО, он закрывает 2П 2026", /171 ?день до ≈ 19 марта 2027/.test(cal)
    && cal.includes("Финансовые результаты X5 за 2026 г. (МСФО) и ориентиры на 2027 г. — закрывает 2П 2026"), cal.slice(0, 400));
  check("#report: МСФО за 3 кв. — строкой «раньше»", cal.includes("Раньше: 29.10.2026 (через 30 дней) — Финансовые результаты X5 за 3 кв. 2026 г. (МСФО)."), cal.slice(0, 600));
  check("#report: промежуточные отчёты в списке помечены", (cal.match(/внутри полугодия · через/g) || []).length === 3, cal);

  // Заголовки операционных результатов кончаются на «г.»: точка после них — одна.
  page.ctx.__d = withReport("2026-11-01", "2026H2", H2_2026);
  const teaserTu = squash(visibleText(page.get("reportTeaser(globalThis.__d)")));
  const calTu = squash(visibleText(page.get("reportCalendarCard(globalThis.__d)")));
  check("«Раньше» у операционных результатов — без второй точки (01.11.2026)",
    teaserTu.includes("Раньше: ≈ 28.01.2027 (через 88 дней) — Операционные результаты X5 за 4 кв. и 2026 г. Внутри полугодия")
    && calTu.includes("— Операционные результаты X5 за 4 кв. и 2026 г. Внутри полугодия") && !/г\.\./.test(teaserTu + calTu),
    teaserTu.slice(0, 500));
  // Как отчёт «Раньше» входит в цену — по виду отчёта: правило A-P2u — только у маржи 3 кв.
  check("«Раньше» у операционных результатов: только выручка, без правила A-P2u",
    teaserTu.includes("2026 г. Внутри полугодия, только выручка и сеть, без маржи: журнал не закрывает, медиану само не двигает; в цену — только новой версией книги.")
    && !/Раньше:[^]*A-P2u/.test(teaserTu), teaserTu.slice(0, 500));
  page.ctx.__d = withReport("2027-03-25", "2027H1", H1_2027);
  const teaserQ1 = squash(visibleText(page.get("reportTeaser(globalThis.__d)")));
  check("«Раньше» у МСФО за 1 кв.: правила A-P2u для 1-го квартала нет",
    teaserQ1.includes("— Финансовые результаты X5 за 1 кв. 2027 г. (МСФО). Внутри полугодия: журнал не закрывает, медиану само не двигает; в цену — только новой версией книги: правила A-P2u для 1-го квартала в книге нет.")
    && !teaserQ1.includes("(правило A-P2u)"), teaserQ1.slice(0, 500));

  const none = withReport("2026-09-29", "2026H2", H2_2026.slice(0, 5));
  page.ctx.__d = none;
  const teaserNone = squash(visibleText(page.get("reportTeaser(globalThis.__d)")));
  check("нет закрывающего МСФО: так и сказано, отсчёта нет", teaserNone.includes("Даты МСФО за 2П 2026 в календаре выпуска нет.")
    && !/дн(я|ей|ь) до/.test(teaserNone) && teaserNone.includes("Раньше: 29.10.2026"), teaserNone.slice(0, 400));
}

/* ── 2б. закрывающее МСФО из выпуска (`next_report.closing`, covers календаря) ── */

// События выпуска на дату оценки — как их отдаёт model/payload.py::report_events на
// календаре репозитория (tests/test_payload.py::test_closing_report_of_the_open_half).
const FY_2026 = { date: "2027-03-19", title: "Финансовые результаты X5 за 2026 г. (МСФО) и ориентиры на 2027 г.", confirmed: false };
const AFTER_FY = [
  ev("2027-04-16", "trading_update", "Операционные результаты X5 за 1 кв. 2027 г.", false),
  ev("2027-04-23", "cbr", "Заседание Совета директоров Банка России по ключевой ставке", false),
  { ...ev("2027-04-29", "ifrs", "Финансовые результаты X5 за 1 кв. 2027 г. (МСФО)", false), covers: null },
  ev("2027-05-19", "dividend", "Рекомендация Наблюдательного совета по дивидендам за 2026 г.", false),
  { ...ev("2027-08-13", "ifrs", "Финансовые результаты X5 за 2 кв. и 1П 2027 г. (МСФО)", false), covers: "2027H1" },
];
const upcoming = (v) => H2_2026.filter((e) => e.date >= v);
{
  const page = makePage();
  const pick = (d) => {
    page.ctx.__d = d;
    const r = page.get("reportEvents(globalThis.__d)");
    return { closing: r.closing && r.closing.date, earlier: r.earlier && r.earlier.date, published: r.published && r.published.date };
  };
  const open = { ...FY_2026, published: false };
  const out = { ...FY_2026, published: true };
  const cases = [
    ["29.09.2026: отсчёт — годовое МСФО, раньше — МСФО за 3 кв.", withReport("2026-09-29", "2026H2", upcoming("2026-09-29"), open), "2027-03-19", "2026-10-29", null],
    ["20.10.2026: раньше — всё ещё МСФО за 3 кв.", withReport("2026-10-20", "2026H2", upcoming("2026-10-20"), open), "2027-03-19", "2026-10-29", null],
    ["01.11.2026: раньше — операционные результаты за 4 кв.", withReport("2026-11-01", "2026H2", upcoming("2026-11-01"), open), "2027-03-19", "2027-01-28", null],
    ["20.03.2027: годовое МСФО вышло — ни отсчёта, ни «раньше»", withReport("2027-03-20", "2026H2", AFTER_FY, out), null, null, "2027-03-19"],
    ["закрывающего МСФО в календаре нет (closing: null): МСФО за 1 кв. ни закрывающим, ни «раньше» не становится",
      withReport("2027-03-20", "2026H2", AFTER_FY, null), null, null, null],
  ];
  for (const [name, d, closing, earlier, published] of cases) {
    let got, error = null;
    try { got = pick(d); } catch (e) { error = String(e); }
    check(`событие отчёта (closing выпуска): ${name}`, !error && got.closing === closing && got.earlier === earlier
      && got.published === published, error || got);
  }

  page.ctx.__d = withReport("2026-10-20", "2026H2", upcoming("2026-10-20"), open);
  const t1020 = squash(visibleText(page.get("reportTeaser(globalThis.__d)")));
  check("20.10.2026: 150 дней до годового МСФО, раньше — МСФО за 3 кв. с правилом A-P2u", /150 ?дней до ≈ 19 марта 2027/.test(t1020)
    && t1020.includes("Раньше: 29.10.2026 (через 9 дней) — Финансовые результаты X5 за 3 кв. 2026 г. (МСФО). Внутри полугодия")
    && t1020.includes("(правило A-P2u)."), t1020.slice(0, 500));

  // 20.03.2027: книга не переведена (2П 2026 открыто), годовое МСФО вышло 19.03.
  const late = withReport("2027-03-20", "2026H2", AFTER_FY, out);
  page.ctx.__d = late;
  const teaser = squash(visibleText(page.get("reportTeaser(globalThis.__d)")));
  const cal = squash(visibleText(page.get("reportCalendarCard(globalThis.__d)")));
  const NOTICE = "Отчёт за 2П 2026 вышел ≈ 19.03.2027; факт ещё не внесён в модель — внести по справочнику, раздел 10.3.";
  check("20.03.2027, «Оценка»: вместо отсчёта — отчёт вышел, факт не внесён", teaser.includes(NOTICE)
    && teaser.includes("отчёт вышел, факт в модель не внесён") && !/дн(я|ей|ь) до/.test(teaser), teaser.slice(0, 500));
  check("20.03.2027, «Оценка»: МСФО за 1 кв. 2027 не «закрывает», строки «Раньше» нет",
    !teaser.includes("закрывает") && !teaser.includes("Раньше:") && !teaser.includes("Внутри полугодия"), teaser.slice(0, 500));
  check("20.03.2027, #report: то же, без пометок «закрывает» и «внутри полугодия»", cal.includes(NOTICE)
    && !cal.includes("закрывает") && !cal.includes("внутри полугодия") && !cal.includes("Раньше:")
    && cal.includes("Финансовые результаты X5 за 1 кв. 2027 г. (МСФО)"), cal.slice(0, 700));

  // В день выхода (дата оценки = дата отчёта) событие ещё в списке — помечено «вышел», без «через 0 дней».
  const onDay = withReport("2027-03-19", "2026H2", [{ ...ev("2027-03-19", "ifrs", FY_2026.title, false), covers: "2026H2" }, ...AFTER_FY], out);
  page.ctx.__d = onDay;
  const calDay = squash(visibleText(page.get("reportCalendarCard(globalThis.__d)")));
  check("19.03.2027: закрывающее МСФО в списке — «закрывает 2П 2026 · вышел»", calDay.includes("закрывает 2П 2026 · вышел")
    && !calDay.includes("через 0 дней") && calDay.includes(NOTICE), calDay.slice(0, 700));

  // Плашка флага `report_fact` — в поясе, как у дивидендов.
  late.checks.flags = [{ name: "report_fact", title: "вышел отчёт за полугодие, факт не внесён", raised: true,
    detail: "Финансовые результаты X5 за 2026 г. (МСФО) и ориентиры на 2027 г. — 2027-03-19 по календарю; в книге 2П 2026 ещё открыто: внести факт — справочник, «Квартальный отчёт X5 и дивиденды»" }];
  page.ctx.__d = late;
  const belt = page.get("banners(globalThis.__d)")[0];
  const beltText = squash(visibleText(belt));
  check("плашка report_fact: заголовок и деталь с датой по-русски", beltText.includes("вышел отчёт за полугодие, факт не внесён.")
    && beltText.includes("— 19.03.2027 по календарю; в книге 2П 2026 ещё открыто"), beltText);
  check("плашка report_fact — вида «событие»", belt.children.some((b) => /banner-event/.test(b.attrs.class || "")),
    belt.children.map((b) => b.attrs.class));

  // Вся страница на таком выпуске: старт без ошибок, плашка и карточка на «Оценке».
  const full = makePage({ payload: JSON.stringify(late) });
  await full.boot();
  await settle();
  const text = full.text();
  check("20.03.2027: страница стартует, плашка и «отчёт вышел» на «Оценке»", !full.bootError && !full.errors.length
    && text.includes("вышел отчёт за полугодие, факт не внесён.") && text.includes(NOTICE), [full.bootError, full.errors.slice(0, 2), text.slice(0, 300)]);
}

/* ── 3. база мультипликатора EV / EBITDA — текущее и следующее полугодия, а не «12 месяцев» ── */

{
  const page = makePage();
  const d = sample();
  page.ctx.__d = d;
  const evText = squash(visibleText(page.get("evCard(globalThis.__d)")));
  check("EV / EBITDA рынка: база — полугодия модели с периодами", evText.includes(
    "рынок: EV / скорр. EBITDA модели текущего и следующего полугодий (2П 2026 + 1П 2027, 300 млрд ₽)"), evText.slice(-400));
  const layersText = squash(visibleText(page.get("layersCard(globalThis.__d)")));
  check("таблица слоёв: столбец EV / EBITDA 2П26 + 1П27", layersText.includes("EV / EBITDA 2П26 + 1П27"), layersText.slice(0, 400));
  d.meta.closed_periods = 1;
  check("с 1 января база — 1П 2027 + 2П 2027", page.get("ebitdaBase(globalThis.__d)") === "1П 2027 + 2П 2027",
    page.get("ebitdaBase(globalThis.__d)"));
  delete d.meta.closed_periods;
  const bare = squash(visibleText(page.get("evCard(globalThis.__d)")));
  check("без meta.closed_periods — подпись без периодов, но с базой словами", bare.includes(
    "рынок: EV / скорр. EBITDA модели текущего и следующего полугодий (300 млрд ₽)"), bare.slice(-400));
  check("«следующих 12 мес.» на витрине нет", !/следующих 12|EBITDA вперёд/.test(APP), "web/app.js");
}

/* ── 4. дивиденд модели на акцию — с базой: акции в обращении, до продажи казначейского пакета ── */

{
  const page = makePage();
  const d = sample();
  page.ctx.__d = d;
  const BASE = "на 245,98 млн акций в обращении, до продажи казначейского пакета";
  const card = squash(visibleText(page.get("dividendCard(globalThis.__d)")));
  check("«Следующая выплата … по модели» — с базой акций", card.includes(
    `по модели 190 ₽ на акцию (${BASE}; выплата в 1П 2027), ожидаемая отсечка`), card.slice(-400));
  const history = squash(visibleText(page.get("dividendHistoryCard(globalThis.__d)")));
  check("график «Модель: по году выплаты» — с базой акций", history.includes(`Модель: по году выплаты, ₽ на акцию ${BASE}`), history.slice(0, 400));
  d.meta.shares_mln = 250.5;
  check("база — из meta.shares_mln выпуска", squash(page.get("dpsBase(globalThis.__d)")).startsWith("на 250,50 млн акций"),
    page.get("dpsBase(globalThis.__d)"));
  delete d.meta.shares_mln;
  const bare = squash(visibleText(page.get("dividendCard(globalThis.__d)")));
  check("без meta.shares_mln — подпись без базы", bare.includes("по модели 190 ₽ на акцию (выплата в 1П 2027), ожидаемая отсечка")
    && !bare.includes("в обращении,"), bare.slice(-300));
}

/* ── 5. все экраны мока рисуются, графики — в ширине карточки и телефона ── */

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
