/* X5 850 — витрина выпуска модели (контракт x5-v1, docs/PAYLOAD.md).
 *
 * Правила витрины (их держат tests/test_web_*.py):
 *  • Числа — только из выпуска `/api/model`. Своих формул у витрины нет.
 *    Единственный пересчёт — ползунок λ: центр прогона = низ + λ·(верх − низ)
 *    по `fair_value.draws_low` / `draws_high`, квантиль тип 7, шаг печати
 *    выпуска, половина — вверх (как `checks.round_to_step` ядра). При λ книги
 *    печатается выпуск как есть. Остальное — форматирование и арифметика
 *    отображения (дни до события, перевод долей в проценты).
 *  • Имя `d` в этом файле — только выпуск: тест сверяет каждое `d.<блок>` со
 *    списком REQUIRED_TOP_LEVEL из docs/PAYLOAD.md.
 *  • Блока нет в выпуске — карточка так и говорит; сломанный экран или график
 *    не роняет остальные (`render` и `paint` ловят исключения).
 *  • Графики рисуются в настоящих пикселях своей карточки (ResizeObserver):
 *    подпись 12,5 px — это 12,5 px и на телефоне.
 *  • Текст — в токенах текста; цвет ряда — только у отметок. У главных
 *    графиков — таблица-двойник (кнопка «Таблица»).
 *  • На экранах — названия, а не ключи: миры, режимы, слои, показатели и
 *    события называются словами из выпуска или словарей ниже.
 */
"use strict";

const API = "/api/model";
const STALE_HOURS = 96;
const SVG_NS = "http://www.w3.org/2000/svg";

let DATA = null;          // выпуск целиком
let LAMBDA = null;        // null — λ книги; число — положение ползунка
let CURRENT = "overview"; // открытый экран

/* ───────────────────────────── DOM ───────────────────────────── */

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  append(node, kids);
  return node;
}

function sv(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG_NS, tag);
  setAttrs(node, attrs);
  append(node, kids);
  return node;
}

function setAttrs(node, attrs) {
  if (!attrs) return;
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
    else if (key === "tip") setTip(node, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
}

function append(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
}

const $ = (selector, root = document) => root.querySelector(selector);
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const cls = (...names) => names.filter(Boolean).join(" ") || null;
const list = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

// Точка в конце фразы — если её там ещё нет («п.п.» уже кончается точкой).
function sentence(textContent) {
  const s = String(textContent || "").trim();
  return !s || /[.!?…]$/.test(s) ? s : s + ".";
}
function upperFirst(textContent) {
  const s = String(textContent || "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
// «Операционная касса» внутри фразы — со строчной; аббревиатуры — как есть.
function lowerFirst(textContent) {
  const s = String(textContent || "");
  return s.length > 1 && s[1] === s[1].toLowerCase() && s[1] !== s[1].toUpperCase() ? s[0].toLowerCase() + s.slice(1) : s;
}

// Форма слова при числе: 1 мир, 3 мира, 36 клеток.
function plural(n, [one, few, many]) {
  const a = Math.abs(Math.trunc(n)) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
}

/* ───────────────────────────── числа по-русски ───────────────────────────── */

// Разряды — узкий неразрывный пробел, дробь — запятая, минус — настоящий.
// Символы собираются из кодов: невидимые знаки в исходнике легко потерять.
const NBSP = String.fromCharCode(0x00a0);
const THIN = String.fromCharCode(0x202f);
const MINUS = String.fromCharCode(0x2212);
const SPACES = new RegExp(`[${NBSP}${THIN} ]`, "g");
const FORMATS = new Map();

function numberFormat(digits) {
  if (!FORMATS.has(digits)) {
    FORMATS.set(digits, new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: true }));
  }
  return FORMATS.get(digits);
}

// Время и дата сборки выпуска — по Москве, как время сделок ISS: иначе у зрителя
// в другом поясе «выпуск собран» и «сделка» разошлись бы на часы.
const MSK = "Europe/Moscow";
const TIME_FORMAT = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: MSK });
const DAY_MSK = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: MSK });
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
  "сентября", "октября", "ноября", "декабря"];
const MONTHS_SHORT = ["янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.",
  "сент.", "окт.", "нояб.", "дек."];
const MONTHS_AXIS = ["янв", "февр", "март", "апр", "май", "июнь", "июль", "авг", "сент", "окт", "нояб", "дек"];
const MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
  "сентябрь", "октябрь", "ноябрь", "декабрь"];

function parseDay(iso) {
  // Дата без времени — календарный день, а не полночь по Гринвичу: иначе
  // зритель к западу от Гринвича увидел бы вчерашнее число. Момент ISO со
  // временем — через Date.parse; свободный текст — не дата (null), чтобы
  // «≈ начало января» не превращалось молча в 1 января.
  if (typeof iso !== "string") return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso)) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : new Date(t);
}

// Календарный день момента ISO по Москве («2026-09-28T21:30:00Z» → «2026-09-29»).
function mskDay(iso) {
  const t = parseDay(iso);
  return t ? DAY_MSK.format(t) : null;
}

// Дней между двумя календарными датами (отсчёт до события от даты оценки).
function daysBetween(fromIso, toIso) {
  const a = parseDay(fromIso), b = parseDay(toIso);
  if (!a || !b) return null;
  const utc = (x) => Date.UTC(x.getFullYear(), x.getMonth(), x.getDate());
  return Math.round((utc(b) - utc(a)) / 864e5);
}

const fmt = {
  num(x, digits = 0) {
    if (!isNum(x)) return "—";
    const scale = 10 ** digits;
    const rounded = Math.round(x * scale) / scale;
    const out = numberFormat(digits).format(rounded === 0 ? 0 : rounded);
    return out.replace(/-/g, MINUS).replace(SPACES, THIN);
  },
  signed(x, digits = 0) {
    if (!isNum(x)) return "—";
    const out = fmt.num(x, digits);
    return x > 0 && out !== fmt.num(0, digits) ? "+" + out : out;
  },
  rub(x, digits = 0) { return isNum(x) ? fmt.num(x, digits) + THIN + "₽" : "—"; },
  signedRub(x, digits = 0) { return isNum(x) ? fmt.signed(x, digits) + THIN + "₽" : "—"; },
  bn(x, digits = 1) { return isNum(x) ? fmt.num(x, digits) + NBSP + "млрд" + NBSP + "₽" : "—"; },
  pct(share, digits = 1) { return isNum(share) ? fmt.num(share * 100, digits) + THIN + "%" : "—"; },
  signedPct(share, digits = 1) { return isNum(share) ? fmt.signed(share * 100, digits) + THIN + "%" : "—"; },
  pp(share, digits = 2) { return isNum(share) ? fmt.signed(share * 100, digits) + NBSP + "п.п." : "—"; },
  // Базисные пункты приходят из выпуска уже в б.п. (единица `bp`, `shift_bp`).
  bp(points, digits = 0) { return isNum(points) ? fmt.num(points, digits) + NBSP + "б.п." : "—"; },
  signedBp(points, digits = 0) { return isNum(points) ? fmt.signed(points, digits) + NBSP + "б.п." : "—"; },
  x(multiple, digits = 2) { return isNum(multiple) ? fmt.num(multiple, digits) + "×" : "—"; },
  // Переменная дня здесь не `d`: `d.` в этом файле — только выпуск.
  date(iso) {
    const day = parseDay(iso);
    if (!day) return "—";
    return `${String(day.getDate()).padStart(2, "0")}.${String(day.getMonth() + 1).padStart(2, "0")}.${day.getFullYear()}`;
  },
  dateLong(iso) {
    const day = parseDay(iso);
    return day ? `${day.getDate()}${NBSP}${MONTHS[day.getMonth()]} ${day.getFullYear()}` : "—";
  },
  dateShort(iso) {
    const day = parseDay(iso);
    return day ? `${day.getDate()}${NBSP}${MONTHS_SHORT[day.getMonth()]}` : "—";
  },
  monthYear(iso) {
    const day = parseDay(iso);
    return day ? `${MONTHS_NOM[day.getMonth()]} ${day.getFullYear()}` : "—";
  },
  // Время — через Intl, по Москве: часы зрителя читает только правило темы в index.html.
  time(iso) {
    const day = parseDay(iso);
    return day ? `${TIME_FORMAT.format(day)}${NBSP}МСК` : "—";
  },
  // Момент сборки: дата и время по Москве («28.09.2026 16:25 МСК»).
  stamp(iso) {
    return parseDay(iso) ? `${fmt.date(mskDay(iso))} ${fmt.time(iso)}` : "—";
  },
  days(n) { return isNum(n) ? `${fmt.num(n)}${NBSP}${plural(n, ["день", "дня", "дней"])}` : "—"; },
};

// Подписи процентной оси — с одним числом знаков на всю ось (5,0 %, 5,5 %, 6,0 %).
function pctTicks(marks) {
  const digits = Math.max(0, ...marks.map((t) => exactDigits(t * 100, 2)));
  return (t) => fmt.num(t * 100, digits) + THIN + "%";
}

// Десятичных знаков у числа книги — столько, сколько в нём есть (не больше max).
function exactDigits(v, max) {
  for (let k = 0; k < max; k++) {
    if (Math.abs(Math.round(v * 10 ** k) - v * 10 ** k) < 1e-6) return k;
  }
  return max;
}

// Значение суждения или показателя в его единице (`unit` выпуска). Сдвиг
// траектории (п.п.) при нуле — «как в книге»; доля — процентами; стоимость
// открытия — тыс. ₽ за м² (в выпуске млрд ₽ на тыс. м²).
function formatByUnit(value, unit, d, compact = false) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return compact ? weightsCompact(value) : weightsText(d, value);
  if (typeof value === "string") return ruText(value);
  if (!isNum(value)) return "—";
  const u = String(unit || "").trim();
  if (u === "pp" || u === "п.п." || u === "shift") {
    return value === 0 ? "без сдвига" : fmt.pp(value, Math.max(1, exactDigits(value * 100, 2)));
  }
  if (u === "pct" || u === "%" || u === "share") return fmt.pct(value, Math.min(2, exactDigits(value * 100, 2)));
  if (u === "bp" || u === "б.п.") return fmt.bp(value);
  if (u === "rub" || u === "₽") return fmt.rub(value, Math.min(1, exactDigits(value, 1)));
  if (u === "bn" || u === "млрд ₽") return fmt.bn(value, Math.min(1, exactDigits(value, 1)));
  if (u === "bn_per_m2") return fmt.num(value * 1e6 / 1000, Math.min(1, exactDigits(value * 1000, 1))) + NBSP + "тыс." + NBSP + "₽/м²";
  if (u === "times" || u === "×" || u === "x") return fmt.x(value, Math.min(2, Math.max(1, exactDigits(value, 2))));
  if (u === "years" || u === "лет") return fmt.num(value, 0) + NBSP + plural(value, ["год", "года", "лет"]);
  return fmt.num(value, exactDigits(value, 3));
}

// Веса миров словами: «Нормализация 35 % · Высокие ставки 45 % · …».
function weightsText(d, weights) {
  const w = obj(weights);
  return Object.keys(w).filter((k) => isNum(w[k]))
    .map((k) => `${worldName(d, k)} ${fmt.pct(w[k], 0)}`).join(" · ") || "—";
}

// Веса миров в ячейке таблицы: «35/45/20 %» (порядок — как у названий в подсказке).
function weightsCompact(weights) {
  const w = obj(weights);
  const vals = Object.keys(w).filter((k) => isNum(w[k])).map((k) => fmt.num(w[k] * 100, 0));
  return vals.length ? vals.join("/") + THIN + "%" : "—";
}
function weightsNames(d, weights) {
  return Object.keys(obj(weights)).map((k) => worldName(d, k)).join(" / ");
}

// Свободный текст выпуска — с десятичной запятой и датами по-русски. Даты ISO
// переводятся первыми, дата «ДД.ММ.ГГГГ» остаётся с точками.
function ruText(textContent) {
  return String(textContent === null || textContent === undefined ? "" : textContent)
    .replace(/(^|[^0-9])(\d{4})-(\d{2})-(\d{2})(?![0-9])/g, "$1$4.$3.$2")
    .replace(/(\d{2}\.\d{2}\.\d{4})|(\d)\.(\d)/g, (m, date, a, b) => (date ? date : `${a},${b}`))
    .replace(/(^|[\s(])-(?=\d)/g, `$1${MINUS}`)
    .replace(/(\d) (%|₽|п\.п\.|б\.п\.)/g, `$1${THIN}$2`);
}

// Полугодие словами: «2026H2» → «2П 2026»; год и «LT» — как есть.
function periodName(p) {
  const s = String(p === null || p === undefined ? "" : p);
  const m = /^(\d{4})H([12])$/.exec(s);
  if (m) return `${m[2]}П${NBSP}${m[1]}`;
  if (s === "LT") return "далее";
  return s;
}
function periodShort(p) {
  const m = /^(\d{4})H([12])$/.exec(String(p));
  return m ? `${m[2]}П${m[1].slice(2)}` : String(p);
}

/* ───────────────────────────── подсказки ───────────────────────────── */

// Одна подсказка на страницу. Содержимое — узлы с textContent, не HTML.
// Подсказка дополняет, но не прячет: её числа видны и подписью или в таблице.
const TIPS = new WeakMap();
let tipOwner = null;

function setTip(node, content) {
  TIPS.set(node, content);
  node.setAttribute("data-tip", "");
  if (!node.hasAttribute("tabindex")) node.setAttribute("tabindex", "0");
}

function tipTarget(node) {
  while (node && node !== document) {
    if (node.nodeType === 1 && TIPS.has(node)) return node;
    node = node.parentNode;
  }
  return null;
}

function tipBody(content) {
  if (typeof content === "string") return [el("div", {}, content)];
  const out = [];
  if (content.title) out.push(el("div", { class: "tip-title" }, content.title));
  for (const [k, v] of content.rows || []) {
    out.push(el("div", { class: "tip-row" }, el("span", { class: "k" }, k), el("span", { class: "v" }, v)));
  }
  if (content.note) out.push(el("div", { class: "tip-note" }, content.note));
  return out;
}

function showTip(owner, x, y) {
  const box = $("#tip");
  if (!box) return;
  if (tipOwner && tipOwner !== owner) tipOwner.classList.remove("is-hot");
  tipOwner = owner;
  owner.classList.add("is-hot");
  box.replaceChildren(...tipBody(TIPS.get(owner)));
  box.hidden = false;
  placeTip(x, y);
}

function placeTip(x, y) {
  const box = $("#tip");
  if (!box || box.hidden) return;
  const pad = 12;
  const w = box.offsetWidth, h = box.offsetHeight;
  let left = x + 14, top = y - h - 12;
  if (left + w > innerWidth - pad) left = Math.max(pad, x - w - 14);
  if (top < pad) top = Math.min(innerHeight - h - pad, y + 18);
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
}

function hideTip() {
  const box = $("#tip");
  if (box) box.hidden = true;
  if (tipOwner) tipOwner.classList.remove("is-hot");
  tipOwner = null;
}

function wireTips() {
  document.addEventListener("pointerover", (e) => {
    const owner = tipTarget(e.target);
    if (owner) showTip(owner, e.clientX, e.clientY);
  });
  document.addEventListener("pointermove", (e) => { if (tipOwner) placeTip(e.clientX, e.clientY); });
  document.addEventListener("pointerout", (e) => {
    const owner = tipTarget(e.target);
    if (owner && !owner.contains(e.relatedTarget)) hideTip();
  });
  document.addEventListener("focusin", (e) => {
    const owner = tipTarget(e.target);
    if (!owner) return;
    const r = owner.getBoundingClientRect();
    showTip(owner, r.left + r.width / 2, r.top);
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  window.addEventListener("scroll", hideTip, { passive: true });
}

/* ───────────────────────────── графики: основа ───────────────────────────── */

// График — функция ширины: draw(width) → <svg>. Контейнер наблюдается
// ResizeObserver и перерисовывается в пикселях своей ширины.
const DRAWS = new WeakMap();
const SIZE = typeof ResizeObserver === "function"
  ? new ResizeObserver((entries) => {
    for (const entry of entries) {
      const host = entry.target;
      const width = Math.floor(entry.contentRect.width);
      if (width > 0 && String(width) !== host.dataset.w) {
        host.dataset.w = width;
        paint(host);
      }
    }
  })
  : null;

function chart(draw, labelText) {
  const host = el("div", { class: "chart", role: "figure", "aria-label": labelText || null });
  DRAWS.set(host, draw);
  if (SIZE) SIZE.observe(host);
  else requestAnimationFrame(() => { host.dataset.w = host.clientWidth || 640; paint(host); });
  return host;
}

function paint(host) {
  const draw = DRAWS.get(host);
  const width = Number(host.dataset.w);
  if (!draw || !width) return;
  try {
    host.replaceChildren(draw(width));
  } catch (error) {
    console.error(error);
    host.replaceChildren(el("p", { class: "empty broken" }, `График не отрисовался: ${error.message}`));
  }
}

function repaint(host) { if (host && host.dataset.w) paint(host); }

function svgBox(width, height, labelText) {
  return sv("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": labelText || null, focusable: "false" });
}

function scale(d0, d1, r0, r1) {
  const k = (r1 - r0) / ((d1 - d0) || 1);
  const f = (v) => r0 + (v - d0) * k;
  f.d = [d0, d1];
  f.r = [r0, r1];
  return f;
}

function niceStep(span, count) {
  const raw = Math.abs(span) / Math.max(1, count);
  if (!raw) return 1;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const r = raw / mag;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * mag;
}

function ticks(d0, d1, count) {
  const step = niceStep(d1 - d0, count);
  const out = [];
  for (let v = Math.ceil(d0 / step - 1e-9) * step; v <= d1 + step * 1e-6; v += step) {
    out.push(Number(v.toPrecision(12)));
  }
  return out;
}

// Ширина подписи — по тому же шрифту, которым её нарисует SVG.
let MEASURE = null;
let FAMILY = null;
function textWidth(content, size = 12.5, weight = 500) {
  if (!MEASURE) {
    MEASURE = document.createElement("canvas").getContext("2d");
    FAMILY = getComputedStyle(document.body).fontFamily;
  }
  MEASURE.font = `${weight} ${size}px ${FAMILY}`;
  return MEASURE.measureText(String(content)).width;
}

function text(x, y, content, attrs = {}) {
  return sv("text", { x, y, ...attrs }, content);
}

function line(x1, y1, x2, y2, attrs = {}) {
  return sv("line", { x1, y1, x2, y2, ...attrs });
}

// Разводит подписи по ярусам, чтобы они не наезжали. Возвращает число ярусов.
function stackLabels(items, gap = 8) {
  const rows = [];
  for (const item of items.sort((a, b) => a.x0 - b.x0)) {
    let row = rows.findIndex((right) => item.x0 >= right + gap);
    if (row < 0) { row = rows.length; rows.push(-Infinity); }
    rows[row] = item.x1;
    item.row = row;
  }
  return rows.length;
}

// Подпись с «ореолом» цвета карточки: читается поверх линий и отметок.
function label(x, y, content, attrs = {}) {
  return text(x, y, content, { ...attrs, class: cls("halo", attrs.class || "label") });
}

// Подпись по центру точки, прижатая к краям графика.
function placed(x, content, W, size = 12.5, weight = 520) {
  const w = textWidth(content, size, weight);
  const x0 = Math.min(Math.max(2, x - w / 2), W - w - 2);
  return { x, text: content, w, x0, x1: x0 + w };
}

/* ───────────────────────────── компоненты ───────────────────────────── */

function card(opts, ...body) {
  const { title, sub, link, tools, span = 12, extra, id } = opts || {};
  const head = (title || sub || tools || link)
    ? el("div", { class: "card-head" },
      el("div", {}, title ? el("h2", {}, title) : null, sub ? el("p", { class: "sub" }, sub) : null),
      tools || null,
      link ? el("a", { class: "card-link", href: `#${link[0]}` }, link[1]) : null)
    : null;
  return el("section", { class: cls("card", `span-${span}`, extra), id: id || null }, head, ...body);
}

function empty(message) {
  return el("p", { class: "empty" }, message);
}

function missing(what) {
  return empty(`В этом выпуске нет блока «${what}».`);
}

function badge(textContent, kind) {
  return el("span", { class: cls("badge", kind && `badge-${kind}`) }, textContent);
}

function detailsBlock(summary, body) {
  return el("details", {}, el("summary", {}, summary), el("div", { class: "detail-text" }, body));
}

// Таблица данных. Число не переносится никогда (td.num — nowrap); широкая
// таблица едет внутри карточки, первая колонка на телефоне закреплена.
// Свободный текст (источник, объяснение) — второй строкой, раскрывается.
function dataTable(columns, rows, opts = {}) {
  const cols = columns.filter(Boolean);
  const head = el("thead", {}, el("tr", {},
    cols.map((c) => el("th", { class: c.num ? "num" : null, scope: "col" }, c.title))));
  const body = el("tbody");
  for (const row of rows) {
    const detail = opts.detail ? opts.detail(row) : null;
    const tr = el("tr", { class: cls(opts.rowClass && opts.rowClass(row), detail && "has-detail") });
    for (const c of cols) {
      tr.append(el("td", { class: cls(c.num && "num", c.cls) }, c.value(row)));
    }
    body.append(tr);
    if (detail) body.append(el("tr", { class: "detail" }, el("td", { colspan: cols.length }, detail)));
  }
  return el("div", { class: "scroll" },
    el("table", { class: cls("data", opts.cls) }, opts.caption ? el("caption", {}, opts.caption) : null, head, body));
}

// График с «таблицей-двойником»: кнопка в шапке карточки меняет вид. Таблица
// строится лениво — из тех же чисел выпуска, что и график.
function withTable(chartNode, makeTable) {
  const box = el("div", { class: "fig" }, chartNode);
  let tableNode = null;
  const button = el("button", { class: "view-toggle", type: "button", "aria-pressed": "false" }, "Таблица");
  const refresh = () => {
    const open = button.getAttribute("aria-pressed") === "true";
    if (tableNode) tableNode.remove();
    tableNode = open ? el("div", { class: "fig-table" }, makeTable()) : null;
    if (tableNode) box.append(tableNode);
  };
  button.addEventListener("click", () => {
    const open = button.getAttribute("aria-pressed") === "true";
    if (!open) {
      tableNode = tableNode || el("div", { class: "fig-table" }, makeTable());
      chartNode.hidden = true;
      box.append(tableNode);
      button.textContent = "График";
      button.setAttribute("aria-pressed", "true");
    } else {
      chartNode.hidden = false;
      if (tableNode) tableNode.remove();
      button.textContent = "Таблица";
      button.setAttribute("aria-pressed", "false");
    }
  });
  return { box, button, refresh };
}

// `opts.text` — в плитке слово, а не число: переносится внутри своей колонки.
function kpi(value, labelText, opts = {}) {
  return el("div", { class: "kpi", tip: opts.tip || null },
    el("div", { class: cls("kpi-value", opts.text && "is-text") }, value, opts.unit ? el("span", { class: "unit" }, " " + opts.unit) : null),
    el("div", { class: "kpi-label" }, labelText));
}

function legend(items) {
  return el("div", { class: "legend" }, items.map(([key, name]) =>
    el("span", {}, el("i", { class: cls("key", key) }), name)));
}

function screenHead(eyebrow, title, lede) {
  return el("header", { class: "screen-head" },
    eyebrow ? el("span", { class: "eyebrow" }, eyebrow) : null,
    el("h1", {}, title),
    lede ? el("p", {}, lede) : null);
}

// Ряд экрана с подписью-«бровью» над сеткой карточек.
function section(title, ...cards) {
  return el("div", { class: "section" },
    title ? el("span", { class: "eyebrow row-label" }, title) : null,
    el("div", { class: "grid" }, ...cards));
}

// Выбор одного из нескольких: aria-pressed у кнопок.
function chooser(options, current, onPick, labelText) {
  const group = el("div", { class: "chooser", role: "group", "aria-label": labelText });
  for (const opt of options) {
    const button = el("button", { type: "button", "aria-pressed": String(opt.value === current) },
      opt.label, opt.hint ? el("span", { class: "w" }, opt.hint) : null);
    button.addEventListener("click", () => {
      for (const b of group.children) b.setAttribute("aria-pressed", String(b === button));
      onPick(opt.value);
    });
    group.append(button);
  }
  return group;
}

/* ───────────────────────────── словари названий ───────────────────────────── */

const LAYER_NAMES = {
  analytical: "свой макро-взгляд",
  market_implied: "веса, вменённые рынком",
  macro_neutral: "рыночные ставки как есть",
};
const LAYER_ORDER = ["macro_neutral", "market_implied", "analytical"];
const CAPEX_NAMES = { low: "низкий", base: "базовый", high: "высокий" };
const REGIME_NAMES = { stress: "стресс", floor: "дно", partial: "частичный возврат", full: "полный возврат" };
const DEMAND_NAMES = { bear: "слабый спрос", base: "базовый спрос", bull: "сильный спрос" };
const TARGET_NAMES = {
  "x5.adj_margin": "Скорр. маржа EBITDA",
  "x5.revenue_growth": "Рост выручки г/г",
};
const KIND_NAMES = {
  trading_update: "операционные результаты",
  ifrs: "финансовые результаты",
  dividend: "дивиденды",
  cbr: "ключевая ставка ЦБ",
};
const INPUT_STATUS = { ok: ["в норме", "good"], stale: ["устарел", "warn"], fallback: ["запасное значение", "warn"] };

function worldKeys(d) {
  const order = list(obj(d.grid).world_order);
  const keys = order.length ? order : Object.keys(obj(d.worlds));
  return keys.filter((k) => obj(d.worlds)[k] && typeof d.worlds[k] === "object" && d.worlds[k].zero_curve);
}
function worldName(d, key) {
  const w = d && d.worlds ? obj(d.worlds[key]) : {};
  return w.name || key;
}
function regimeKeys(d) {
  const order = list(obj(d.grid).regime_order);
  const keys = order.length ? order : ["stress", "floor", "partial", "full"];
  return keys.filter((k) => obj(d.regimes)[k]);
}
function regimeName(d, key) {
  const r = d && d.regimes ? obj(d.regimes[key]) : {};
  return r.title || REGIME_NAMES[key] || key;
}
function capexKeys(d) {
  const order = list(obj(d.grid).capex_order);
  return order.length ? order : ["low", "base", "high"];
}
function layerName(d, key) {
  const layer = obj(obj(d.layers)[key]);
  return layer.title || LAYER_NAMES[key] || key;
}
function targetName(target) {
  return TARGET_NAMES[target] || "Показатель журнала";
}
function targetValue(target, value) {
  if (!isNum(value)) return "—";
  return /growth/.test(String(target)) ? fmt.signedPct(value, 1) : fmt.pct(value, 2);
}

/* ───────────────────────────── λ: заголовок при другом весе взгляда ───────────────────────────── */

// Квантиль тип 7 (линейная интерполяция), как в ядре.
function quantile7(sorted, q) {
  const n = sorted.length;
  if (!n) return NaN;
  const h = (n - 1) * q;
  const lo = Math.floor(h);
  const hi = Math.min(lo + 1, n - 1);
  return sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]);
}

// Округление печати — как в ядре (`checks.round_to_step`): половина вверх.
function roundHalfUp(x) {
  return Math.floor(x + 0.5);
}

function hasDraws(d) {
  const fv = obj(d.fair_value);
  return Array.isArray(fv.draws_low) && Array.isArray(fv.draws_high) && fv.draws_low.length > 1
    && fv.draws_low.length === fv.draws_high.length && !!d.headline;
}

function bookLambda(d) {
  const fv = obj(d.fair_value), head = obj(d.headline);
  return isNum(fv.lambda) ? fv.lambda : isNum(head.lambda) ? head.lambda : 0.5;
}
function lambdaStep(d) {
  const step = obj(d.fair_value).lambda_step;
  return isNum(step) && step > 0 ? step : 0.05;
}
function lambdaNow(d) { return LAMBDA === null ? bookLambda(d) : LAMBDA; }
function atBookLambda(d) { return LAMBDA === null || Math.abs(LAMBDA - bookLambda(d)) < 1e-9; }
function marketPrice(d) {
  const head = obj(d.headline);
  return isNum(head.market_price) ? head.market_price : obj(d.market).price;
}
function printStep(d) {
  const step = obj(d.headline).print_step;
  return isNum(step) && step > 0 ? step : 50;
}

// Центры прогонов при λ: низ + λ·(верх − низ) каждого прогона.
function centresAt(d, lam) {
  const lo = d.fair_value.draws_low, hi = d.fair_value.draws_high;
  const out = new Array(lo.length);
  for (let i = 0; i < lo.length; i++) out[i] = lo[i] + lam * (hi[i] - lo[i]);
  return out;
}

// Медиана, полосы, среднее и P(ниже рынка) при λ. При λ книги — числа выпуска
// без пересчёта; иначе — правило книги на прогонах выпуска.
function headlineAt(d, lam) {
  const head = obj(d.headline);
  if (lam === null || Math.abs(lam - bookLambda(d)) < 1e-9) {
    return { central: head.central, printed_central: head.printed_central, band: list(head.band), printed_band: list(head.printed_band),
      inner: list(head.inner), printed_inner: list(head.printed_inner), mean: head.mean, p_below: head.p_central_below_market, release: true };
  }
  const c = centresAt(d, lam).sort((a, b) => a - b);
  const q = (p) => quantile7(c, p);
  const step = printStep(d);
  const print = (v) => roundHalfUp(v / step) * step;
  const market = marketPrice(d);
  const central = q(0.5);
  const band = [q(0.1), q(0.9)];
  const inner = [q(0.25), q(0.75)];
  let below = 0, sum = 0;
  for (const v of c) { sum += v; if (v < market) below += 1; }
  return { central, printed_central: print(central), band, printed_band: band.map(print), inner, printed_inner: inner.map(print),
    mean: sum / c.length, p_below: below / c.length, release: false };
}

// Точка при центральных значениях всех суждений: низ + λ·(верх − низ).
function pointAt(d, lam) {
  const fv = obj(d.fair_value);
  if (lam === null || Math.abs(lam - bookLambda(d)) < 1e-9) return fv.central;
  return fv.low + lam * (fv.high - fv.low);
}
function printedPoint(d, lam) {
  const fv = obj(d.fair_value);
  if (lam === null || Math.abs(lam - bookLambda(d)) < 1e-9) return obj(fv.printed).central;
  return roundHalfUp(pointAt(d, lam) / printStep(d)) * printStep(d);
}

/* ───────────────────────────── распределение (герой) ───────────────────────────── */

// Плотность центров прогонов по суждениям книги (ядро Гаусса с отражением у
// нуля), под ней полосы 80 % и 50 %, «ящик» P10–P25–P50–P75–P90, линия
// рынка и ромб точки при центральных значениях. Шкала — до 99,5-го
// перцентиля прогонов: редкий хвост не сжимает картинку.
function distributionChart(d) {
  return chart((W) => {
    const lam = lambdaNow(d);
    const centres = centresAt(d, lam).sort((a, b) => a - b);
    const hl = headlineAt(d, LAMBDA);
    const point = pointAt(d, LAMBDA);
    const market = marketPrice(d);
    const narrow = W < 520;
    const H = narrow ? 238 : 286;
    const m = { l: 10, r: 12, t: 40, b: 58 };
    const base = H - m.b;
    const hi = Math.max(quantile7(centres, 0.995), market * 1.12, hl.band[1] * 1.08);
    const unit = niceStep(hi, 8);
    const xmax = Math.ceil(hi / unit) * unit;
    const x = scale(0, xmax, m.l, W - m.r);

    // Ширина ядра — правило Сильвермана; это сглаживание картинки, а не число.
    const n = centres.length;
    const mean = centres.reduce((s, v) => s + v, 0) / n;
    const sd = Math.sqrt(centres.reduce((s, v) => s + (v - mean) ** 2, 0) / Math.max(1, n - 1));
    const iqr = quantile7(centres, 0.75) - quantile7(centres, 0.25);
    const bw = Math.max(xmax / 200, 0.9 * Math.min(sd, iqr / 1.34 || sd) * n ** -0.2);
    // Ядро считается по мелкой гистограмме: ползунок перерисовывает график на
    // каждое движение, и на телефоне это заметно.
    const cells = 480;
    const width = xmax / cells;
    const counts = new Float64Array(cells);
    for (const c of centres) if (c >= 0 && c < xmax) counts[Math.floor(c / width)] += 1;
    const density = (v) => {
      let sum = 0;
      for (let j = 0; j < cells; j++) {
        if (!counts[j]) continue;
        const c = (j + 0.5) * width;
        const a = (v - c) / bw;
        const b = (v + c) / bw;
        if (a > -5 && a < 5) sum += counts[j] * Math.exp(-0.5 * a * a);
        if (b > -5 && b < 5) sum += counts[j] * Math.exp(-0.5 * b * b);
      }
      return sum;
    };
    const samples = Math.max(90, Math.min(260, Math.round((W - m.l - m.r) / 2.5)));
    const grid = [];
    for (let i = 0; i <= samples; i++) {
      const v = (xmax * i) / samples;
      grid.push([v, density(v)]);
    }
    const ymax = Math.max(...grid.map((p) => p[1])) * 1.1 || 1;
    const y = scale(0, ymax, base, m.t);
    const areaOn = (a, b) => {
      const pts = [[a, density(a)], ...grid.filter((p) => p[0] > a && p[0] < b), [b, density(b)]];
      return `M${x(a)},${base} ` + pts.map(([v, f]) => `L${x(v).toFixed(1)},${y(f).toFixed(1)}`).join(" ")
        + ` L${x(b)},${base} Z`;
    };
    const top = "M" + grid.map(([v, f]) => `${x(v).toFixed(1)},${y(f).toFixed(1)}`).join(" L");

    const svg = svgBox(W, H, "Распределение справедливой цены по суждениям книги");
    svg.append(
      sv("path", { d: areaOn(0, xmax), fill: "var(--model-wash-1)" }),
      sv("path", { d: areaOn(hl.band[0], hl.band[1]), fill: "var(--model-wash-2)" }),
      sv("path", { d: areaOn(hl.inner[0], hl.inner[1]), fill: "var(--model-wash-3)" }),
      sv("path", { d: top, fill: "none", stroke: "var(--model)", "stroke-width": 2, "stroke-linejoin": "round" }),
      line(m.l, base, W - m.r, base, { class: "axisline" }));

    // «Ящик»: усы P10–P90, ящик P25–P75, медиана.
    const ys = base + 20;
    svg.append(
      line(x(hl.band[0]), ys, x(hl.band[1]), ys, { stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.band[0]), ys - 5, x(hl.band[0]), ys + 5, { stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.band[1]), ys - 5, x(hl.band[1]), ys + 5, { stroke: "var(--model)", "stroke-width": 1.5 }),
      sv("rect", { x: x(hl.inner[0]), y: ys - 6, width: Math.max(2, x(hl.inner[1]) - x(hl.inner[0])),
        height: 12, rx: 3, fill: "var(--model-wash-3)", stroke: "var(--model)", "stroke-width": 1.5 }),
      line(x(hl.central), ys - 8, x(hl.central), ys + 8, { stroke: "var(--ink)", "stroke-width": 2.5 }));

    svg.append(
      line(x(hl.central), m.t - 4, x(hl.central), base, { stroke: "var(--ink)", "stroke-width": 1.5 }),
      line(x(market), m.t - 4, x(market), ys + 10, { stroke: "var(--market)", "stroke-width": 2 }));

    const px = x(point);
    svg.append(sv("path", { d: `M${px},${base - 6} L${px + 6},${base} L${px},${base + 6} L${px - 6},${base} Z`,
      fill: "var(--surface)", stroke: "var(--ink)", "stroke-width": 1.6 }));

    // Подписи сверху: медиана и рынок, ярусами без наездов; каждая — с внешней стороны своей
    // линии (рынок ниже медианы — его подпись слева, подпись медианы справа), чтобы подпись не
    // оказалась у чужой линии.
    const marketLeft = market < hl.central;
    const tops = [
      { x: x(hl.central), text: `медиана ${fmt.rub(hl.printed_central)}`, pref: marketLeft ? "right" : "left" },
      { x: x(market), text: `рынок ${fmt.rub(market)}`, pref: marketLeft ? "left" : "right" },
    ].map((t) => {
      const w = textWidth(t.text, 13, 640);
      let x0 = t.pref === "left" ? t.x - w - 6 : t.x + 6;
      if (x0 < 2) x0 = t.x + 6;
      if (x0 + w > W - 2) x0 = t.x - w - 6;
      return { ...t, w, x0, x1: x0 + w };
    });
    const rows = stackLabels(tops, 10);
    for (const t of tops) svg.append(label(t.x0, 14 + t.row * 16 + (rows === 1 ? 6 : 0), t.text, { class: "label-strong" }));
    const pointText = `точка ${fmt.rub(printedPoint(d, LAMBDA))}`;
    const pw = textWidth(pointText, 12.5, 520);
    let plx = px + 9;
    if (plx + pw > W - 2) plx = px - pw - 9;
    svg.append(label(plx, base - 9, pointText, { class: "label" }));

    // Ось цены: единица — у последней подписи.
    const marks = ticks(0, xmax, narrow ? 4 : 7);
    marks.forEach((t, i) => {
      const tx = x(t);
      const last = i === marks.length - 1;
      const anchor = t === 0 ? "start" : tx > W - 40 ? "end" : "middle";
      svg.append(line(tx, ys + 14, tx, ys + 18, { class: "axisline" }),
        text(anchor === "end" ? W - 1 : tx, H - 6, t === 0 ? "0" : fmt.num(t) + (last ? THIN + "₽" : ""), { class: "tick", "text-anchor": anchor }));
    });

    const hot = (x0, w, tipContent) => sv("rect", { class: "hit", x: x0, y: m.t - 8, width: w,
      height: ys + 12 - (m.t - 8), tip: tipContent });
    svg.append(
      hot(x(hl.inner[0]), Math.max(6, x(hl.inner[1]) - x(hl.inner[0])), {
        title: "Полоса 50 % (P25–P75)", rows: [["от", fmt.rub(hl.printed_inner[0])], ["до", fmt.rub(hl.printed_inner[1])]] }),
      hot(x(hl.central) - 8, 16, { title: "Медиана по суждениям книги",
        rows: [["печать", fmt.rub(hl.printed_central)], ["точно", fmt.rub(hl.central)]] }),
      hot(x(market) - 8, 16, { title: "Рыночная цена", rows: [["X5", fmt.rub(market)]],
        note: obj(d.market).price_date ? `на ${fmt.date(d.market.price_date)}` : null }),
      hot(px - 8, 16, { title: "Точка при центральных значениях",
        rows: [["печать", fmt.rub(printedPoint(d, LAMBDA))], ["точно", fmt.rub(point)]] }));
    return svg;
  }, "Распределение справедливой цены: медиана, полосы 80 и 50 процентов, рынок и точка");
}

/* ───────────────────────────── ряд «диапазон книги → что нужно рынку» ───────────────────────────── */

// Значение оси обратного DCF в её единице: сдвиг — в п.п., доля — в %,
// стоимость метра — тыс. ₽/м², бета — числом. `signed` — отклонение от книги.
function reverseValue(row, value, bookSide = false, signed = false) {
  if (!isNum(value)) return "—";
  const u = String(row.unit || "").trim();
  const plus = signed && value > 0 ? "+" : "";
  if (u === "п.п." || u === "pp") {
    return bookSide && value === 0 ? "без сдвига" : fmt.pp(value, bookSide ? Math.max(1, exactDigits(value * 100, 2)) : 2);
  }
  if (u === "bn_per_m2") return plus + formatByUnit(value, u, null);
  if (u === "%" || u === "pct") return plus + fmt.pct(value, bookSide ? exactDigits(value * 100, 2) : 2);
  return plus + fmt.num(value, bookSide ? exactDigits(value, 3) : 2);
}

// Имя суждения на экране: единицу «млрд ₽ на тыс. м²» витрина печатает в самом
// значении (тыс. ₽/м²), а ключ мира в имени заменяет названием мира.
function axisLabel(d, name) {
  return String(name || "")
    .replace(/,\s*млрд\s*₽\s*на\s*тыс\.\s*м²/g, "")
    .replace(/(мира|миров) ([NHM])(?![A-Za-z0-9])/g, (m, w, k) => (d && obj(d.worlds)[k] ? `${w} «${worldName(d, k)}»` : m));
}

function rangeRowChart(row) {
  return chart((W) => {
    const H = 34;
    const [r0, r1] = list(row.range);
    const vals = [r0, r1, row.book].filter(isNum);
    if (isNum(row.solved)) vals.push(row.solved);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 0.01;
    lo -= pad; hi += pad;
    const x = scale(lo, hi, 8, W - 8);
    const svg = svgBox(W, H);
    const cy = H / 2;
    svg.append(line(8, cy, W - 8, cy, { class: "gridline" }));
    if (isNum(r0) && isNum(r1)) {
      svg.append(line(x(r0), cy, x(r1), cy, { stroke: "var(--model-wash-3)", "stroke-width": 8, "stroke-linecap": "round" }));
    }
    if (isNum(row.book)) svg.append(line(x(row.book), cy - 8, x(row.book), cy + 8, { stroke: "var(--ink)", "stroke-width": 2 }));
    if (isNum(row.solved)) {
      const vx = Math.max(8, Math.min(W - 8, x(row.solved)));
      svg.append(sv("circle", { cx: vx, cy, r: 6, fill: "var(--market)", stroke: "var(--surface)", "stroke-width": 2 }));
    }
    svg.append(sv("rect", { class: "hit", x: 0, y: 0, width: W, height: H, tip: {
      title: row.name,
      rows: [["нужно рынку", isNum(row.solved) ? reverseValue(row, row.solved) : "недостижимо"],
        ["в книге", reverseValue(row, row.book, true)],
        ["диапазон книги", `${reverseValue(row, r0, true)} … ${reverseValue(row, r1, true)}`]] } }));
    return svg;
  }, `${row.name}: диапазон книги и значение, при котором медиана равна рынку`);
}

/* ───────────────────────────── гантели: модель против рынка ───────────────────────────── */

function dumbbellChart(rows, opts = {}) {
  return chart((W) => {
    const rowH = 46;
    const H = rows.length * rowH + 24;
    const values = rows.flatMap((r) => [r.a, r.b]).filter(isNum);
    const lo = Math.min(...values), hi = Math.max(...values);
    const pad = (hi - lo) * 0.18 || 10;
    const x = scale(lo - pad, hi + pad, 8, W - 8);
    const svg = svgBox(W, H, opts.label);
    for (const t of ticks(lo - pad, hi + pad, W < 420 ? 3 : 6)) {
      const half = textWidth(fmt.num(t), 12.5, 400) / 2;
      svg.append(line(x(t), 0, x(t), H - 20, { class: "gridline" }));
      if (x(t) - half >= 0 && x(t) + half <= W) svg.append(text(x(t), H - 4, fmt.num(t), { class: "tick", "text-anchor": "middle" }));
    }
    rows.forEach((r, i) => {
      const cy = i * rowH + 30;
      svg.append(label(8, cy - 13, r.name, { class: "label" }));
      if (isNum(r.a) && isNum(r.b)) svg.append(line(x(r.a), cy, x(r.b), cy, { stroke: "var(--axis)", "stroke-width": 2 }));
      for (const [v, color, name] of [[r.a, "var(--model)", r.aLabel], [r.b, "var(--market)", r.bLabel]]) {
        if (!isNum(v)) continue;
        svg.append(sv("circle", { cx: x(v), cy, r: 6, fill: color, stroke: "var(--surface)", "stroke-width": 2,
          tip: { title: `${r.name}: ${name}`, rows: [[name, fmt.bn(v, 1)]] } }));
      }
      if (isNum(r.a) && isNum(r.b)) {
        const left = Math.min(r.a, r.b), right = Math.max(r.a, r.b);
        const lt = fmt.num(left, 0), rt = fmt.num(right, 0);
        const lx = x(left) - 10, rx = x(right) + 10;
        if (lx - textWidth(lt) >= 0) svg.append(label(lx, cy + 4, lt, { class: "label", "text-anchor": "end" }));
        if (rx + textWidth(rt) <= W) svg.append(label(rx, cy + 4, rt, { class: "label" }));
      }
    });
    return svg;
  }, opts.label);
}

/* ───────────────────────────── столбцы (с накоплением) ───────────────────────────── */

// items: [{key, label, value} | {key, label, parts: [{value, color, name}]}].
// opts.refs: [{value, text, color}] — горизонтальные опорные линии.
function columnsChart(items, opts = {}) {
  return chart((W) => {
    const H = opts.height || 220;
    const m = { l: 8, r: 8, t: 22, b: 26 };
    const total = (it) => (it.parts ? it.parts.reduce((s, p) => s + (isNum(p.value) ? Math.max(0, p.value) : 0), 0) : it.value);
    const values = items.map(total).filter(isNum);
    const refs = (opts.refs || []).filter((r) => isNum(r.value));
    const vmax = Math.max(...values, ...refs.map((r) => r.value), 1e-9) * 1.14;
    const y = scale(0, vmax, H - m.b, m.t);
    const band = (W - m.l - m.r) / Math.max(1, items.length);
    const bw = Math.min(opts.barWidth || 30, band * 0.62);
    const widest = Math.max(...items.map((it) => textWidth(it.label || String(it.key), 12.5, 400)));
    const every = Math.max(1, Math.ceil((widest + 8) / band));
    const svg = svgBox(W, H, opts.label);
    for (const t of ticks(0, vmax, 4)) svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }));
    const bar = (cx, y0, y1, color, tipContent, roundTop) => {
      const h = y0 - y1;
      if (h <= 0.2) return null;
      const r = roundTop ? Math.min(4, h / 2) : 0;
      return sv("path", {
        d: `M${cx - bw / 2},${y0} V${y1 + r} Q${cx - bw / 2},${y1} ${cx - bw / 2 + r},${y1} H${cx + bw / 2 - r} Q${cx + bw / 2},${y1} ${cx + bw / 2},${y1 + r} V${y0} Z`,
        fill: color, tip: tipContent });
    };
    items.forEach((it, i) => {
      const cx = m.l + band * (i + 0.5);
      const sum = total(it);
      if (it.parts) {
        let run = 0;
        const shown = it.parts.filter((p) => isNum(p.value) && p.value > 0);
        shown.forEach((p, j) => {
          const node = bar(cx, y(run), y(run + p.value), p.color, { title: it.tipTitle || String(it.key),
            rows: [...it.parts.filter((q) => isNum(q.value)).map((q) => [q.name, opts.fmt ? opts.fmt(q.value) : fmt.num(q.value, 1)]), ...(it.extra || [])] }, j === shown.length - 1);
          if (node) svg.append(node);
          run += p.value;
        });
      } else if (isNum(it.value) && it.value > 0) {
        const node = bar(cx, y(0), y(it.value), it.color || opts.color || "var(--model)",
          { title: it.tipTitle || String(it.key), rows: [[opts.valueName || "значение", opts.fmt ? opts.fmt(it.value) : fmt.num(it.value, 1)]] }, true);
        if (node) svg.append(node);
      }
      if (isNum(sum) && sum > 0) {
        const short = opts.short ? opts.short(sum) : fmt.num(sum, 0);
        if (short && textWidth(short, 12.5, 520) < band - 2) svg.append(label(cx, y(sum) - 6, short, { "text-anchor": "middle" }));
      }
      if ((items.length - 1 - i) % every === 0) {
        svg.append(text(cx, H - 8, it.label || String(it.key), { class: "tick", "text-anchor": "middle" }));
      }
    });
    svg.append(line(m.l, H - m.b, W - m.r, H - m.b, { class: "axisline" }));
    for (const ref of refs) {
      const ry = y(ref.value);
      svg.append(line(m.l, ry, W - m.r, ry, { stroke: ref.color || "var(--market)", "stroke-width": 1.5, "stroke-dasharray": ref.dash || null }),
        label(W - m.r, ry - 6, ref.text, { "text-anchor": "end", class: "label" }));
    }
    return svg;
  }, opts.label);
}

/* ───────────────────────────── линии ───────────────────────────── */

// Линии на числовой или порядковой оси X. series: [{name, color, points:
// [[x, y]], dots, dashed, width, line, r, area: [[x, lo, hi]]}].
// opts.xType: "number" | "band"; opts.vlines: [{x, text, color}] —
// вертикальные отметки; opts.hrefs: [{value, text, color}].
function linesChart(series, opts = {}) {
  return chart((W) => {
    const H = opts.height || 240;
    const m = { l: opts.left || 46, r: opts.right || 16, t: opts.top || 18, b: 28 };
    const all = series.flatMap((s) => s.points).filter((p) => isNum(p[1]));
    const areas = series.flatMap((s) => (s.area || []).flatMap((p) => [p[1], p[2]]));
    const refs = (opts.hrefs || []).filter((r) => isNum(r.value));
    const bandKeys = opts.xType === "band" ? opts.categories : null;
    const map = opts.xMap || ((v) => v);
    let x;
    if (bandKeys) {
      const step = (W - m.l - m.r) / bandKeys.length;
      x = (k) => m.l + step * (bandKeys.indexOf(k) + 0.5);
    } else {
      const xs = all.map((p) => p[0]);
      const x0 = opts.xMin ?? Math.min(...xs), x1 = opts.xMax ?? Math.max(...xs);
      const base = scale(map(x0), map(x1), m.l, W - m.r);
      x = (v) => base(map(v));
      x.d = [x0, x1];
    }
    const yvals = all.map((p) => p[1]).concat(areas, refs.map((r) => r.value)).filter(isNum);
    let y0 = opts.yMin ?? Math.min(...yvals);
    let y1 = opts.yMax ?? Math.max(...yvals);
    const pad = (y1 - y0) * 0.1 || Math.abs(y1) * 0.1 || 1;
    if (opts.yMin === undefined) y0 -= pad;
    if (opts.yMax === undefined) y1 += pad;
    // Подписи вертикалей стоят ярусами у верхнего края; при двух и больше ярусах верх
    // шкалы опускается под них — иначе линии серий у верхнего края идут по нижней подписи.
    const vRows = stackLabels((opts.vlines || []).filter((v) => v.text && isNum(x(v.x))).map((v) => {
      const vx = x(v.x), w = textWidth(v.text, 12.5, 520);
      const x0 = vx + 5 + w > W - m.r ? vx - 5 - w : vx + 5;
      return { x0, x1: x0 + w };
    }), 8);
    const y = scale(y0, y1, H - m.b, m.t + Math.max(0, vRows - 1) * 16);
    const svg = svgBox(W, H, opts.label);
    const yMarks = ticks(y0, y1, opts.yTicks || 4);
    // Знаков у подписей оси — сколько нужно делениям (5,93 % и 1,15×, а не 5,9 % дважды).
    const yDigits = Math.max(0, ...yMarks.map((t) => exactDigits(t, 3)));
    const yLabel = opts.yPct ? pctTicks(yMarks) : opts.yFmt ? ((t) => opts.yFmt(t, yDigits)) : ((t) => fmt.num(t));
    for (const t of yMarks) {
      svg.append(line(m.l, y(t), W - m.r, y(t), { class: "gridline" }),
        text(m.l - 8, y(t) + 4, yLabel(t), { class: "tick", "text-anchor": "end" }));
    }
    if (bandKeys) {
      // Подписи прореживаются от ПОСЛЕДНЕЙ: крайняя справа видна всегда.
      const step = (W - m.l - m.r) / bandKeys.length;
      const widest = Math.max(...bandKeys.map((k) => textWidth(opts.xFmt ? opts.xFmt(k) : k, 12.5, 400)));
      const every = Math.max(1, Math.ceil((widest + 10) / step));
      bandKeys.forEach((k, i) => {
        if ((bandKeys.length - 1 - i) % every === 0) {
          svg.append(text(x(k), H - 8, opts.xFmt ? opts.xFmt(k) : k, { class: "tick", "text-anchor": "middle" }));
        }
      });
    } else {
      // Подписи прореживаются справа налево: подпись встаёт, только если не
      // задевает уже поставленную (деления бывают неравномерными).
      const marks = opts.xTicks || ticks(x.d[0], x.d[1], W < 420 ? 4 : 7);
      let left = Infinity;
      for (const t of marks.slice().sort((a, b) => b - a)) {
        const s = opts.xFmt ? opts.xFmt(t) : fmt.num(t);
        const tx = x(t);
        const w = textWidth(s, 12.5, 400);
        const anchor = tx - w / 2 < 0 ? "start" : tx + w / 2 > W ? "end" : "middle";
        const x0 = anchor === "start" ? Math.max(0, tx - 2) : anchor === "end" ? W - 1 - w : tx - w / 2;
        if (x0 + w + 8 > left) continue;
        left = x0;
        svg.append(text(anchor === "end" ? W - 1 : anchor === "start" ? Math.max(0, tx - 2) : tx, H - 8, s, { class: "tick", "text-anchor": anchor }));
      }
    }
    svg.append(line(m.l, H - m.b, W - m.r, H - m.b, { class: "axisline" }));
    // Подписи опорных линий: близкие по высоте расходятся — нижняя уходит под линию.
    // `below` — подпись под линией (над ней рисуются другие ряды).
    // `short` — подпись для узкого графика (телефон), `below` — под линией.
    const refText = (r) => (W < 480 && r.short ? r.short : r.text);
    const refLabels = refs.filter((r) => r.text).map((r) => ({ r, y: y(r.value) + (r.below ? 16 : -6) })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < refLabels.length; i++) {
      if (refLabels[i].y - refLabels[i - 1].y < 15 && (refLabels[i].r.side || "right") === (refLabels[i - 1].r.side || "right")) refLabels[i].y += 20;
    }
    for (const ref of refs) {
      svg.append(line(m.l, y(ref.value), W - m.r, y(ref.value), { stroke: ref.color, "stroke-width": 1.5, "stroke-dasharray": ref.dash || null }));
    }
    const vItems = [];
    for (const v of opts.vlines || []) {
      const vx = x(v.x);
      if (!isNum(vx)) continue;
      svg.append(line(vx, m.t - 6, vx, H - m.b, { stroke: v.color || "var(--ink-2)", "stroke-width": 1.2, "stroke-dasharray": v.dash || "3 3",
        tip: v.tip || null }));
      if (v.text) {
        const w = textWidth(v.text, 12.5, 520);
        const x0 = vx + 5 + w > W - m.r ? vx - 5 - w : vx + 5;
        vItems.push({ text: v.text, x0, x1: x0 + w });
      }
    }
    stackLabels(vItems, 8);
    // Подписи опорных линий и вертикалей раскладываются вместе: подпись опорной
    // линии, задевающая подпись вертикали, уходит под свою линию.
    const hits = (ly, x0, x1) => vItems.some((t) => Math.abs(m.t + 6 + t.row * 16 - ly) < 15 && t.x0 < x1 + 8 && t.x1 > x0 - 8);
    for (const item of refLabels) {
      const w = textWidth(refText(item.r), 12.5, 520);
      const x1 = item.r.side === "left" ? m.l + 4 + w : W - m.r;
      const x0 = x1 - w;
      if (hits(item.y, x0, x1)) {
        const below = y(item.r.value) + 16;
        item.y = hits(below, x0, x1) || below > H - m.b - 2 ? item.y : below;
      }
      // Всё ещё задевает — ярусы вертикалей от задетого и ниже сдвигаются вниз целиком.
      while (hits(item.y, x0, x1)) {
        const hit = Math.min(...vItems.filter((t) => Math.abs(m.t + 6 + t.row * 16 - item.y) < 15 && t.x0 < x1 + 8 && t.x1 > x0 - 8).map((t) => t.row));
        for (const t of vItems) if (t.row >= hit) t.row += 1;
      }
    }
    for (const { r, y: ly } of refLabels) {
      svg.append(label(r.side === "left" ? m.l + 4 : W - m.r, ly, refText(r), { "text-anchor": r.side === "left" ? "start" : "end", class: "label" }));
    }
    for (const t of vItems) svg.append(label(t.x0, m.t + 6 + t.row * 16, t.text, { class: "label" }));
    // Точки за пределами оси X не рисуются: линия не уходит на соседнюю карточку.
    const inside = (v) => bandKeys || (v >= x.d[0] - 1e-9 && v <= x.d[1] + 1e-9);
    for (const s of series) {
      const pts = s.points.filter((p) => isNum(p[1]) && isNum(x(p[0])) && inside(p[0]));
      if (s.area && s.area.length > 1) {
        const up = s.area.map((p) => `${x(p[0]).toFixed(1)},${y(p[2]).toFixed(1)}`);
        const down = s.area.slice().reverse().map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`);
        svg.append(sv("path", { d: `M${up.join(" L")} L${down.join(" L")} Z`, fill: s.areaFill || "var(--model-wash-2)" }));
      }
      if (pts.length > 1 && s.line !== false) {
        svg.append(sv("path", { d: "M" + pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" L"),
          fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-linejoin": "round", "stroke-linecap": "round",
          "stroke-dasharray": s.dashed ? "5 4" : null }));
      }
      if (s.dots) {
        for (const p of pts) {
          svg.append(sv("circle", { cx: x(p[0]), cy: y(p[1]), r: s.r || 4.5, fill: s.hollow ? "var(--surface)" : s.color,
            stroke: s.hollow ? s.color : "var(--surface)", "stroke-width": 2,
            tip: { title: `${s.name}: ${opts.xFmt ? opts.xFmt(p[0]) : p[0]}`, rows: [[s.name, opts.tipFmt ? opts.tipFmt(p[1]) : fmt.num(p[1], 2)]] } }));
        }
      }
    }
    return svg;
  }, opts.label);
}

/* ───────────────────────────── HTML-полосы (водопад, торнадо, вклады) ───────────────────────────── */

// Горизонтальные полосы в HTML: длинные русские подписи переносятся сами, а
// полоса — процент ширины дорожки.
function barTrack(segments, domain, opts = {}) {
  const [d0, d1] = domain;
  const pos = (v) => (100 * (v - d0)) / ((d1 - d0) || 1);
  const track = el("div", { class: "bt-track" });
  if (isNum(opts.center)) track.append(el("i", { class: "bt-center", style: `left:${pos(opts.center)}%` }));
  for (const seg of segments) {
    const a = Math.min(seg.from, seg.to), b = Math.max(seg.from, seg.to);
    track.append(el("i", { class: cls("bt-bar", seg.cls), style: `left:${pos(a)}%;width:${Math.max(0.4, pos(b) - pos(a))}%`,
      tip: seg.tip || null }));
  }
  for (const mark of opts.marks || []) {
    track.append(el("i", { class: cls("bt-mark", mark.cls), style: `left:${pos(mark.at)}%`, tip: mark.tip || null }));
  }
  return track;
}

// Полоса доли: имя, дорожка, число.
function hbar(name, share, max, opts = {}) {
  return el("div", { class: "hbar", tip: opts.tip || null },
    el("span", { class: "hbar-name" }, name),
    el("span", { class: "hbar-track" }, el("i", { class: "hbar-fill", style: `width:${(100 * share) / (max || 1)}%${opts.color ? `;background:${opts.color}` : ""}` })),
    el("span", { class: "hbar-val" }, opts.value || fmt.pct(share, share < 0.1 ? 1 : 0)));
}

// Линия ряда в плитке: история выпуска, ось Y — от минимума до максимума.
function sparkline(item, valueText) {
  const pts = list(item.history).filter((p) => p && isNum(p.value));
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p.value);
  // Минимум и максимум — по полному ряду выпуска (линия — тонкая выборка).
  const lo = Math.min(...vals, ...(isNum(obj(item.min).value) ? [item.min.value] : []));
  const hi = Math.max(...vals, ...(isNum(obj(item.max).value) ? [item.max.value] : []));
  const plot = chart((W) => {
    const H = 40;
    const x = scale(0, pts.length - 1, 3, W - 5);
    const y = hi > lo ? scale(lo, hi, H - 5, 5) : () => H / 2;
    const svg = svgBox(W, H, `История ряда «${item.title}»`);
    svg.append(sv("polyline", { points: pts.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" "),
      fill: "none", stroke: "var(--model)", "stroke-width": 1.6, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = pts[pts.length - 1];
    svg.append(sv("circle", { cx: x(pts.length - 1), cy: y(last.value), r: 3, fill: "var(--model)",
      tip: { title: item.title, rows: [[fmt.date(pts[0].date), valueText(pts[0].value)], [fmt.date(last.date), valueText(last.value)]] } }));
    return svg;
  }, `История ряда «${item.title}»`);
  return el("div", { class: "spark" }, plot,
    el("span", { class: "muted small" }, `с ${fmt.date(pts[0].date)}: мин ${valueText(lo)} · макс ${valueText(hi)}`));
}

/* ───────────────────────────── экран «Оценка» ───────────────────────────── */

function screenOverview(d) {
  const root = el("div", { class: "screen" });
  root.append(hasDraws(d) ? hero(d) : heroWithoutBand(d));
  root.append(section("Почему такая оценка", pricedTeaser(d), bandDrivers(d)));
  root.append(section("Что дальше", reportTeaser(d), dividendsTeaser(d)));
  root.append(section("Что изменилось", changesCard(d, 6), eventsCard(d, 6)));
  return root;
}

function hero(d) {
  const head = obj(d.headline);
  const fv = obj(d.fair_value);
  const market = marketPrice(d);
  const copy = el("div", { class: "hero-copy", id: "fv-hero" });
  const tiles = el("div", { class: "tiles", id: "fv-tiles" });
  const plot = distributionChart(d);
  const table = () => {
    const hl = headlineAt(d, LAMBDA);
    const rows = [
      ["P10 — нижний край полосы 80 %", fmt.rub(hl.band[0]), fmt.rub(hl.printed_band[0])],
      ["P25 — нижний край полосы 50 %", fmt.rub(hl.inner[0]), fmt.rub(hl.printed_inner[0])],
      ["Медиана", fmt.rub(hl.central), fmt.rub(hl.printed_central)],
      ["P75 — верхний край полосы 50 %", fmt.rub(hl.inner[1]), fmt.rub(hl.printed_inner[1])],
      ["P90 — верхний край полосы 80 %", fmt.rub(hl.band[1]), fmt.rub(hl.printed_band[1])],
      ["Точка при центральных значениях", fmt.rub(pointAt(d, LAMBDA)), fmt.rub(printedPoint(d, LAMBDA))],
      ["Среднее прогонов", fmt.rub(hl.mean), "—"],
      ["Рыночная цена", fmt.rub(market), "—"],
      ["P(справедливая цена ниже рынка)", fmt.pct(hl.p_below, 2), "—"],
    ];
    return dataTable([
      { title: "Величина", value: (r) => r[0], cls: "name" },
      { title: "Точно", num: true, value: (r) => r[1] },
      { title: "Печать", num: true, value: (r) => r[2] },
    ], rows, { caption: hl.release ? `Числа выпуска, λ = ${fmt.num(bookLambda(d), 2)}`
      : `λ = ${fmt.num(LAMBDA, 2)}: пересчёт из ${fmt.num(fv.draws_low.length)} прогонов выпуска` });
  };
  const fig = withTable(plot, table);

  const out = el("output", { class: "lambda-out", for: "lambda" });
  const reset = el("button", { class: "lambda-reset", type: "button", hidden: true }, "вернуть λ книги");
  const slider = el("input", { id: "lambda", type: "range", min: "0", max: "1", step: String(lambdaStep(d)),
    value: String(bookLambda(d)), "aria-label": "Вес своего взгляда на инфляцию и ставки, λ" });
  const lambdaBox = el("div", { class: "lambda" },
    el("div", { class: "lambda-top" },
      el("span", { class: "lambda-title" }, "Взгляд на инфляцию и ставки"), out),
    el("div", { class: "lambda-ends" },
      el("span", {}, el("i", { class: "key key-market" }), "рыночные ставки как есть"),
      el("span", {}, "свой макро-взгляд", el("i", { class: "key key-model" }))),
    slider,
    el("p", { class: "lambda-note" },
      `Точка при рыночных ставках — ${fmt.rub(obj(fv.printed).low)}, при своём макро-взгляде — ${fmt.rub(obj(fv.printed).high)}. `
      + "Ползунок пересчитывает медиану, полосы и точку из прогонов выпуска.", " ", reset));

  const chartCard = el("section", { class: "card hero-chart" },
    el("div", { class: "card-head" },
      el("div", {},
        el("h2", {}, "Распределение справедливой цены"),
        el("p", { class: "sub" }, `${fmt.num(head.draws)} прогонов по ${fmt.num(obj(d.uncertainty).axes_count)} суждениям книги в их диапазонах`)),
      fig.button),
    fig.box,
    legend([["key-b80", "80 % прогонов"], ["key-b50", "50 %"], ["key-line key-ink", "медиана"],
      ["key-line key-market", "рынок"], ["key-diamond", "точка при центральных значениях"]]),
    lambdaBox);

  const update = () => {
    const lam = LAMBDA;
    const hl = headlineAt(d, lam);
    const book = atBookLambda(d);
    copy.replaceChildren(
      el("span", { class: "eyebrow" }, `Справедливая стоимость акции X5 · ${fmt.date(obj(d.meta).valuation_date)}`),
      el("div", { class: "hero-figure", id: "fv-headline" },
        el("span", { class: "hero-approx" }, "≈"),
        el("span", { class: "hero-value", id: "kpi-central" }, fmt.num(hl.printed_central)),
        el("span", { class: "hero-unit" }, "₽")),
      el("p", { class: "hero-caption" }, `медиана по суждениям книги ${obj(d.meta).book_version || ""}`,
        el("span", { class: "muted" }, ` · точно ${fmt.rub(hl.central)}${book ? "" : ` · при λ = ${fmt.num(lam, 2)}`}`)),
      el("div", { class: "bands" },
        el("div", { class: "band-row" }, el("i", { class: "band-key b80" }),
          el("span", { class: "what" }, "полоса 80 %"),
          el("span", { class: "range" }, `${fmt.num(hl.printed_band[0])}–${fmt.num(hl.printed_band[1])}${THIN}₽`)),
        el("div", { class: "band-row" }, el("i", { class: "band-key b50" }),
          el("span", { class: "what" }, "полоса 50 %"),
          el("span", { class: "range" }, `${fmt.num(hl.printed_inner[0])}–${fmt.num(hl.printed_inner[1])}${THIN}₽`))),
      el("div", { class: "verdict", id: "fv-ev" },
        el("div", { class: "verdict-top" },
          el("span", { class: "verdict-num" }, pBelowText(hl.p_below)),
          el("span", { class: "verdict-title" }, `вероятность, что справедливая цена ниже рыночной — ${fmt.rub(market)}`)),
        el("div", { class: "meter", role: "img", "aria-label": `P(ниже рынка) ${fmt.pct(hl.p_below, 2)}` },
          el("i", { style: `width:${Math.max(0, Math.min(100, (hl.p_below || 0) * 100))}%` })),
        el("span", { class: "verdict-note" }, verdictNote(d, hl, market, book ? null : lam))));
    tiles.replaceChildren(...heroTiles(d, lam));
    out.value = book ? `λ = ${fmt.num(bookLambda(d), 2)} · книга` : `λ = ${fmt.num(lam, 2)} · точка ${fmt.rub(printedPoint(d, lam))}`;
    reset.hidden = book;
  };
  slider.addEventListener("input", () => {
    const v = Number(slider.value);
    LAMBDA = Math.abs(v - bookLambda(d)) < 1e-9 ? null : v;
    update();
    repaint(plot);
    fig.refresh();
  });
  reset.addEventListener("click", () => {
    LAMBDA = null;
    slider.value = String(bookLambda(d));
    update();
    repaint(plot);
    fig.refresh();
    slider.focus();
  });
  if (LAMBDA !== null) slider.value = String(LAMBDA);
  update();
  return el("div", {}, el("div", { class: "hero" }, copy, chartCard), tiles);
}

// P(ниже рынка) крупно: целыми процентами, а ненулевая доля меньше 1 % — с двумя
// знаками (1 прогон из 2 000 — «0,05 %», а не «0 %»).
function pBelowText(p) {
  return fmt.pct(p, isNum(p) && p > 0 && p < 0.01 ? 2 : 0);
}

// Вывод под вероятностью — из чисел выпуска: где рынок относительно полос.
function verdictNote(d, hl, market, lam) {
  const where = market < hl.band[0] ? "рынок ниже полосы 80 %"
    : market > hl.band[1] ? "рынок выше полосы 80 %"
      : market < hl.inner[0] ? "рынок в нижней части полосы 80 %, ниже полосы 50 %"
        : market > hl.inner[1] ? "рынок в верхней части полосы 80 %, выше полосы 50 %"
          : "рынок внутри полосы 50 %";
  const gap = isNum(hl.central) && isNum(market) && market > 0 ? hl.central / market - 1 : null;
  return `${upperFirst(where)}; медиана ${gap >= 0 ? "выше" : "ниже"} рынка на ${fmt.pct(Math.abs(gap), 0)}`
    + ` · ${fmt.num(obj(d.headline).draws)} прогонов${lam === null ? "" : ` · при λ = ${fmt.num(lam, 2)}`}`;
}

function heroTiles(d, lam) {
  const out = [];
  const fv = obj(d.fair_value);
  const ce = obj(fv.center_ev);
  const mk = obj(d.market);
  const book = atBookLambda(d);
  const atBook = book ? "" : ` (при λ книги ${fmt.num(bookLambda(d), 2)})`;
  if (isNum(ce.gap_median)) {
    out.push(el("section", { class: "card tile" },
      el("span", { class: "tile-label" }, "Стоимость бизнеса: модель против рынка"),
      el("span", { class: "tile-value" }, fmt.signedPct(ce.gap_median, 1)),
      el("p", { class: "tile-note" },
        `EV медианы ${fmt.bn(ce.v0_median, 0)} против рыночной стоимости бизнеса ${fmt.bn(ce.v_star, 0)}${atBook}. `
        + `У точки — ${fmt.signedPct(ce.gap_point, 1)}.`)));
    out.push(el("section", { class: "card tile" },
      el("span", { class: "tile-label" }, "Цена 1 % стоимости бизнеса"),
      el("span", { class: "tile-value" }, fmt.num(ce.rub_per_1pct_ev_median), el("span", { class: "unit" }, "₽ на акцию")),
      el("p", { class: "tile-note" },
        `Капитал — ${fmt.pct(fv.equity_share_of_ev, 0)} EV точки модели`
        + (isNum(mk.equity_share_of_ev) ? `; у рынка — ${fmt.pct(mk.equity_share_of_ev, 0)}: требования ${fmt.bn(mk.claims, 0)} `
          + `при рыночной стоимости бизнеса ${fmt.bn(mk.market_ev, 0)}.` : "."))));
  }
  const point = pointAt(d, lam);
  const median = headlineAt(d, LAMBDA).central;
  const diff = isNum(point) && isNum(median) ? point - median : null;
  out.push(el("section", { class: "card tile" },
    el("span", { class: "tile-label" }, "Точка при центральных значениях всех суждений"),
    el("span", { class: "tile-value" }, fmt.num(printedPoint(d, lam)), el("span", { class: "unit" }, "₽")),
    el("p", { class: "tile-note", id: "fv-lede" },
      `Все суждения книги в центре своих диапазонов: точно ${fmt.rub(point)}`
      + (isNum(diff) ? `, ${diff >= 0 ? "выше" : "ниже"} медианы на ${fmt.rub(Math.abs(diff))}.` : "."))));
  const view = obj(fv.rates_view);
  if (isNum(view.rub)) {
    // Число плитки — «верх − низ» выпуска; доля пути и вклад в точку — при текущем λ.
    const lamNow = lam === null || lam === undefined ? bookLambda(d) : lam;
    out.push(el("section", { class: "card tile" },
      el("span", { class: "tile-label" }, "Вклад взгляда на инфляцию и ставки"),
      el("span", { class: "tile-value" }, fmt.signed(view.rub), el("span", { class: "unit" }, "₽")),
      el("p", { class: "tile-note" },
        `Рыночные ставки как есть — ${fmt.rub(obj(fv.printed).low)}, свой макро-взгляд — ${fmt.rub(obj(fv.printed).high)}; `
        + `точка берёт ${fmt.pct(lamNow, 0)} пути от первого ко второму — ${fmt.signedRub(lamNow * view.rub)}`
        + (book ? "." : ` (при λ книги ${fmt.num(bookLambda(d), 2)} — ${fmt.signedRub(bookLambda(d) * view.rub)}).`))));
  }
  return out;
}

// Выпуск без прогонов по суждениям: крупно — точка, справа — ось ставок.
function heroWithoutBand(d) {
  const fv = obj(d.fair_value);
  const market = marketPrice(d);
  const plot = chart((W) => {
    const H = 120;
    const hi = Math.max(fv.high, fv.low, market) * 1.25;
    const x = scale(0, hi, 10, W - 12);
    const svg = svgBox(W, H, "Ось ставок, точка и рынок");
    const cy = 56;
    svg.append(line(x(fv.low), cy, x(fv.high), cy, { stroke: "var(--model-wash-3)", "stroke-width": 14, "stroke-linecap": "round" }),
      line(x(fv.central), cy - 14, x(fv.central), cy + 14, { stroke: "var(--ink)", "stroke-width": 2.5 }),
      line(x(market), cy - 22, x(market), cy + 22, { stroke: "var(--market)", "stroke-width": 2 }));
    const items = [placed(x(fv.central), `точка ${fmt.rub(obj(fv.printed).central)}`, W, 13, 640), placed(x(market), `рынок ${fmt.rub(market)}`, W, 13, 640)];
    stackLabels(items, 10);
    for (const t of items) svg.append(label(t.x0, t.row === 0 ? 18 : H - 30, t.text, { class: "label-strong" }));
    for (const t of ticks(0, hi, W < 420 ? 4 : 6)) svg.append(text(x(t), H - 4, fmt.num(t), { class: "tick", "text-anchor": t === 0 ? "start" : "middle" }));
    return svg;
  }, "Ось ставок, точка и рынок");
  const tiles = el("div", { class: "tiles", id: "fv-tiles" }, ...heroTiles(d, null));
  const hero = el("div", { class: "hero" },
    el("div", { class: "hero-copy", id: "fv-hero" },
      el("span", { class: "eyebrow" }, `Справедливая стоимость акции X5 · ${fmt.date(obj(d.meta).valuation_date)}`),
      el("div", { class: "hero-figure", id: "fv-headline" },
        el("span", { class: "hero-approx" }, "≈"),
        el("span", { class: "hero-value", id: "kpi-central" }, fmt.num(obj(fv.printed).central)),
        el("span", { class: "hero-unit" }, "₽")),
      el("p", { class: "hero-caption" }, "точка при центральных значениях всех суждений",
        el("span", { class: "muted" }, ` · точно ${fmt.rub(fv.central)}`)),
      el("div", { class: "bands" },
        el("div", { class: "band-row" }, el("i", { class: "band-key b80" }),
          el("span", { class: "what" }, "рыночные ставки — свой взгляд"),
          el("span", { class: "range" }, `${fmt.num(obj(fv.printed).low)}–${fmt.num(obj(fv.printed).high)}${THIN}₽`))),
      el("div", { class: "verdict" },
        el("div", { class: "verdict-top" },
          el("span", { class: "verdict-num" }, fmt.rub(market)),
          el("span", { class: "verdict-title" }, "рыночная цена")))),
    el("section", { class: "card hero-chart" },
      el("div", { class: "card-head" }, el("div", {}, el("h2", {}, "Точка и рынок на оси ставок"))),
      plot,
      legend([["key-b50", "рыночные ставки — свой взгляд"], ["key-line key-ink", "точка"], ["key-line key-market", "рынок"]])));
  return el("div", {}, hero, tiles);
}

/* ── «Что заложено в цену» (кратко) ── */

function reverseRows(d) {
  return list(obj(d.reverse_dcf).rows).map((r) => ({ ...r, name: axisLabel(d, r.name) }));
}
function judgementRows(d) {
  return list(obj(d.judgements).rows).map((r) => ({ ...r, name: axisLabel(d, r.name) }));
}
function inRange(r) { return r.status !== "unreachable" && isNum(r.solved) && !!r.in_range; }

// Заголовок «Что в цене» — из решённых значений: какое суждение внутри
// диапазона книги ближе всего к книге и какую долю пути к краю оно занимает.
function pricedHeadline(rows) {
  if (!rows.length) return "Что заложено в рыночную цену";
  const share = (r) => {
    const [a, b] = list(r.range);
    const end = r.solved >= r.book ? b : a;
    return end === r.book ? 0 : (r.solved - r.book) / (end - r.book);
  };
  const inside = rows.filter(inRange).map((r) => ({ r, s: share(r) })).sort((a, b) => a.s - b.s);
  if (!inside.length) return "Ни одно суждение в пределах книги не объясняет рыночную цену";
  return `Рыночную цену объясняет ${lowerFirst(shortAxis(inside[0].r.name))} — ${fmt.pct(inside[0].s, 0)} пути от книги к краю диапазона`;
}

function shortAxis(name) {
  return String(name || "").replace(/\s*\(.*\)\s*/g, " ").replace(/,\s*(млрд|%).*$/, "").trim();
}

function reverseBadge(r) {
  return r.status === "unreachable" || !isNum(r.solved) ? badge("недостижимо", "out")
    : r.in_range ? badge("в диапазоне книги", "good") : badge("вне диапазона", "out");
}

function pricedTeaser(d) {
  const rows = reverseRows(d);
  if (!rows.length) return card({ title: "Что заложено в цену", span: 6, link: ["market", "Подробно"] }, missing("обратный DCF"));
  const inside = rows.filter(inRange);
  const outside = rows.filter((r) => !inRange(r));
  return card({ title: "Что заложено в цену", span: 6, link: ["market", "Все оси"],
    sub: `Значение одного суждения, при котором медиана равна рыночной цене ${fmt.rub(obj(d.reverse_dcf).target)}; остальные — как в книге.` },
  inside.length
    ? el("div", { class: "rows" }, inside.map((r) => el("div", { class: "rrow" },
      el("div", { class: "rrow-name" }, r.name),
      rangeRowChart(r),
      el("div", { class: "rrow-need" }, el("span", { class: "v" }, reverseValue(r, r.solved)),
        el("span", { class: "b" }, `в книге ${reverseValue(r, r.book, true)}`)))))
    : empty("Ни одно суждение в пределах своего диапазона не даёт рыночную цену."),
  outside.length ? el("p", { class: "card-foot" }, "Вне диапазона книги: ",
    outside.map((r) => `${lowerFirst(shortAxis(r.name))} — ${isNum(r.solved) && r.status !== "unreachable" ? reverseValue(r, r.solved) : "недостижимо"}`).join("; "), ".") : null);
}

/* ── «Что определяет полосу» ── */

function judgementByName(d) {
  return Object.fromEntries(judgementRows(d).map((j) => [j.name, j]));
}

function bandDrivers(d) {
  const rows = list(obj(d.uncertainty).contributions).filter((r) => isNum(r.share)).map((r) => ({ ...r, axis: axisLabel(d, r.axis) })).sort((a, b) => b.share - a.share);
  if (!rows.length) return card({ title: "Что определяет полосу", span: 6 }, missing("вклад суждений в полосу"));
  const top = rows.slice(0, 6);
  const rest = rows.slice(6);
  const max = Math.max(...rows.map((r) => r.share));
  const byName = judgementByName(d);
  const bar = (r) => {
    const j = byName[r.axis];
    return hbar(r.axis, r.share, max, { tip: { title: r.axis, rows: [["доля разброса", fmt.pct(r.share, 1)],
      ...(j ? [["диапазон книги", `${formatByUnit(j.low, j.unit, d, true)} … ${formatByUnit(j.high, j.unit, d, true)}`]] : [])] } });
  };
  return card({ title: "Что определяет полосу", span: 6, link: ["book", "Все суждения"],
    sub: `Доля разброса медианы, которую даёт каждое суждение (по ранговой корреляции, ${fmt.num(obj(d.uncertainty).draws)} прогонов).` },
  el("div", { class: "hbars" }, top.map(bar)),
  rest.length ? el("div", { class: "card-foot" }, detailsBlock(`Ещё ${rest.length} ${plural(rest.length, ["суждение", "суждения", "суждений"])}`, el("div", { class: "hbars" }, rest.map(bar)))) : null);
}

/* ── «Ближайший отчёт» (кратко) ── */

// Ближайшее событие отчётности открытого полугодия (или любое ближайшее).
function nextReportEvent(d) {
  const nr = obj(d.next_report);
  const from = obj(d.meta).valuation_date;
  const events = list(nr.events).filter((e) => (daysBetween(from, e.date) ?? -1) >= 0)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));
  return events.find((e) => e.kind === "ifrs") || events.find((e) => e.kind === "trading_update") || events[0] || null;
}

function neutralSentence(d) {
  const nr = obj(d.next_report);
  const n = obj(nr.neutral);
  if (!isNum(n.median)) return "";
  const slope = isNum(nr.rub_per_01pp) ? `; каждые 0,1${NBSP}п.п. маржи — около ${fmt.rub(nr.rub_per_01pp)} медианы` : "";
  return `Нейтральная маржа ${periodName(nr.period)} — ${fmt.pct(n.median, 2)}: при таком факте медиана не изменится, выше — вырастет, ниже — снизится${slope}.`;
}

function reportTeaser(d) {
  const nr = obj(d.next_report);
  if (!nr.period) return card({ title: "Ближайший отчёт", span: 6, link: ["report", "Подробно"] }, missing("ближайший отчёт"));
  const ev = nextReportEvent(d);
  const days = ev ? daysBetween(obj(d.meta).valuation_date, ev.date) : null;
  const exp = obj(nr.expectation);
  const g = obj(nr.guidance);
  const bench = list(nr.benchmarks)[0];
  return card({ title: `Ближайший отчёт: ${periodName(nr.period)}`, span: 6, link: ["report", "Подробно"],
    sub: ev ? `${ev.title}${ev.confirmed ? "" : " · ожидаемая дата"}` : "" },
  ev ? el("div", { class: "countdown" },
    el("span", { class: "big" }, fmt.num(days)),
    el("span", { class: "ink-2" }, `${plural(days, ["день", "дня", "дней"])} до ${ev.confirmed ? "" : "≈" + NBSP}${fmt.dateLong(ev.date)}`)) : null,
  el("div", { class: "kpis", style: "margin-top:14px" },
    isNum(exp.margin) ? kpi(fmt.pct(exp.margin, 2), `ожидание модели: скорр. маржа ${periodName(nr.period)}`) : null,
    isNum(g.margin_min) ? kpi(`≥${NBSP}${fmt.pct(g.margin_min, 1)}`, "прогноз компании на год") : null,
    bench && isNum(bench.margin) ? kpi(fmt.pct(bench.margin, 2), `наивный эталон: ${lowerFirst(bench.name)}`) : null,
    isNum(obj(nr.neutral).median) ? kpi(fmt.pct(nr.neutral.median, 2), "нейтральная маржа: медиана не меняется") : null),
  list(nr.table).length ? el("div", { style: "margin-top:14px" },
    el("span", { class: "tile-label" }, `Если маржа ${periodName(nr.period)} выйдет …, медиана станет:`),
    impactChart(d, true)) : null,
  el("p", { class: "card-foot" }, neutralSentence(d)));
}

/* ── «Дивиденды» (кратко) ── */

const DIV_STATUS = { declared: ["объявлен", "warn"], recommended: ["рекомендован", "model"], paid: ["выплачен", "good"],
  paying: ["выплачивается", "warn"], unclaimed: ["не востребован", "warn"] };

// Ожидаемая отсечка: дата ISO — месяцем и годом; без даты — текст оценки как есть.
function nextRecordText(next) {
  return parseDay(next.record_date_est) ? fmt.monthYear(next.record_date_est) : (next.record_date_note ? ruText(next.record_date_note) : "—");
}
// Отсечка строки реестра; у невостребованного остатка отсечки нет.
function registerCutoff(r) {
  return r.status === "unclaimed" ? "—" : fmt.date(r.ex_date || r.record_date);
}

function dividendsTeaser(d) {
  const dv = obj(d.dividends);
  const reg = list(dv.register);
  const next = obj(dv.next_expected);
  const cal = list(obj(d.calendar).events).filter((e) => e.kind === "dividend");
  if (!reg.length && !next.label) return card({ title: "Дивиденды", span: 6 }, missing("дивиденды"));
  const recent = reg.slice().sort((a, b) => String(b.ex_date || b.record_date).localeCompare(String(a.ex_date || a.record_date))).slice(0, 3);
  return card({ title: "Дивиденды", span: 6, link: ["debt", "Политика и история"],
    sub: dv.policy && dv.policy.text ? sentence(dv.policy.text) : "" },
  el("div", { class: "kpis" },
    isNum(dv.yield_ltm) ? kpi(fmt.pct(dv.yield_ltm, 1), "дивидендная доходность за 12 месяцев") : null,
    next.label ? kpi(isNum(next.dps_model) ? fmt.rub(next.dps_model) : "—",
      `${lowerFirst(next.label)} по модели${next.pay_period ? ` (выплата ${periodName(next.pay_period)})` : ""}`) : null,
    next.record_date_est || next.record_date_note ? kpi(nextRecordText(next), "ожидаемая отсечка") : null),
  recent.length ? el("div", { style: "margin-top:14px" }, dataTable([
    { title: "Выплата", value: (r) => el("span", {}, r.label, el("span", { class: "cell-badge" }, divStatus(r))), cls: "name" },
    { title: "На акцию", num: true, value: (r) => fmt.rub(r.dps, isNum(r.dps) && r.dps % 1 ? 2 : 0) },
    { title: "Отсечка", num: true, value: registerCutoff },
  ], recent, { cls: "compact" })) : null,
  cal.length ? el("p", { class: "card-foot" }, sentence("В календаре: " + cal.slice(0, 3).map((e) => `${fmt.date(e.date)} — ${lowerFirst(e.title)}`).join("; "))) : null);
}

function divStatus(r) {
  const [name, kind] = DIV_STATUS[r.status] || [r.status || "—", null];
  const share = isNum(r.paid_share) && r.paid_share < 0.99 && (r.status === "paid" || r.status === "paying") ? ` · ${fmt.pct(r.paid_share, 0)}` : "";
  return badge(name + share, kind);
}

/* ── «Что изменилось» ── */

const CHANGE_NAMES = {
  valuation_date: "дата оценки (перекат)", roll: "дата оценки (перекат)", rollover: "дата оценки (перекат)",
  market_price: "цена рынка", price: "цена рынка", residual: "книга, факты и код", book: "книга", facts: "факты", code: "код",
};

function changesCard(d, span) {
  const ch = obj(obj(d.changes).vs_previous);
  const rows = list(ch.rows).filter((r) => isNum(r.rub));
  return card({ title: "Что изменилось с прошлого выпуска", span: span || 6,
    sub: ch.previous_generated_at ? `точка при центральных значениях против выпуска ${fmt.date(mskDay(ch.previous_generated_at))}` : "" },
  rows.length ? el("ul", { class: "list" },
    rows.map((r) => el("li", {},
      el("span", { class: "t" }, upperFirst(CHANGE_NAMES[r.component] || r.component)),
      el("span", { class: "v" }, fmt.signedRub(r.rub, 1)))),
    isNum(ch.total_rub) ? el("li", { class: "total" }, el("span", { class: "t" }, el("strong", {}, "Итого, точка")),
      el("span", { class: "v" }, fmt.signedRub(ch.total_rub, 1))) : null)
    : empty("Прошлого выпуска для сравнения нет."));
}

/* ── события ── */

function eventsCard(d, limit, span) {
  const from = obj(d.meta).valuation_date;
  const events = list(obj(d.calendar).events)
    .map((e) => ({ ...e, days: daysBetween(from, e.date) }))
    .filter((e) => e.days === null || e.days >= 0)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)))
    .slice(0, limit || 99);
  if (!events.length) return card({ title: "События", span: span || 6 }, empty("Ближайших событий в выпуске нет."));
  return card({ title: "События", span: span || 6, sub: `отсчёт — от даты оценки ${fmt.date(from)}` },
    el("ol", { class: "timeline" }, events.map((e) => el("li", {
      class: cls(e.kind === "dividend" && "is-key", (e.kind === "ifrs" || e.kind === "trading_update") && "is-fact") },
    el("span", { class: "tl-date" }, e.confirmed ? fmt.dateShort(e.date) : `≈${NBSP}${fmt.dateShort(e.date)}`,
      el("span", { class: "muted" }, isNum(e.days) ? `через ${fmt.days(e.days)}` : "")),
    el("span", { class: "tl-rail" }, el("i")),
    el("span", { class: "tl-body" }, el("strong", {}, e.title),
      el("span", { class: "muted" }, [KIND_NAMES[e.kind] || "", e.confirmed ? "дата объявлена" : "ожидаемая дата"].filter(Boolean).join(" · ")),
      e.note ? detailsBlock("подробнее", ruText(e.note)) : null)))));
}

/* ───────────────────────────── экран «Что в цене» ───────────────────────────── */

function screenMarket(d) {
  const rows = reverseRows(d);
  const mk = obj(d.market);
  const head = obj(d.headline);
  // Разрыв медианы с рынком — от точной медианы, как на «Оценке».
  const px = marketPrice(d);
  const gap = isNum(head.central) && isNum(px) && px > 0 ? head.central / px - 1 : null;
  const root = el("div", { class: "screen" },
    screenHead("Что заложено в цену", pricedHeadline(rows),
      `Рынок платит ${fmt.rub(mk.price)} за акцию (${fmt.date(mk.price_date)}), медиана модели — ${fmt.rub(head.printed_central)}`
      + (isNum(gap) ? ` (${fmt.signedPct(gap, 0)} к рынку)` : "") + ". Ниже — какое значение одного суждения делает медиану равной рынку, "
      + "как модель и рынок расходятся в стоимости бизнеса, цели инвестдомов и аналоги на одной базе."));
  root.append(el("div", { class: "grid" }, reverseDcfCard(d)));
  root.append(el("div", { class: "grid section" }, evCard(d), brokersCard(d)));
  root.append(el("div", { class: "grid section" }, priceCard(d)));
  root.append(el("div", { class: "grid section" }, peersCard(d)));
  root.append(el("div", { class: "grid section" }, tornadoCard(d)));
  return root;
}

function reverseDcfCard(d) {
  const block = obj(d.reverse_dcf);
  const rows = reverseRows(d);
  if (!rows.length) return card({ title: "Обратный DCF по суждениям книги" }, missing("обратный DCF"));
  const reached = (r) => r.status !== "unreachable" && isNum(r.solved);
  const table = () => dataTable([
    { title: "Суждение", value: (r) => r.name, cls: "name" },
    { title: "Нужно рынку", num: true, value: (r) => (reached(r) ? reverseValue(r, r.solved) : "недостижимо") },
    { title: "В книге", num: true, value: (r) => reverseValue(r, r.book, true) },
    { title: "Отклонение", num: true, value: (r) => (reached(r) && isNum(r.delta) ? reverseValue(r, r.delta, false, true) : "—") },
    { title: "Диапазон книги", num: true, value: (r) => `${reverseValue(r, list(r.range)[0], true)} … ${reverseValue(r, list(r.range)[1], true)}` },
    { title: "Для точки", num: true, value: (r) => (isNum(r.point_solved) ? reverseValue(r, r.point_solved) : "—") },
    { title: "", value: (r) => reverseBadge(r) },
  ], rows);
  const rowsBox = el("div", { class: "rows" }, rows.map((r) => el("div", { class: "rrow" },
    el("div", { class: "rrow-name" }, r.name,
      el("span", { class: "hint" }, `в книге ${reverseValue(r, r.book, true)} · диапазон ${reverseValue(r, list(r.range)[0], true)} … ${reverseValue(r, list(r.range)[1], true)}`)),
    rangeRowChart(r),
    el("div", { class: "rrow-need" },
      reached(r)
        ? [el("span", { class: "v" }, reverseValue(r, r.solved)), " ", reverseBadge(r)]
        : [el("span", { class: "v muted" }, "недостижимо"), el("span", { class: "b" }, "рыночная цена не достигается на всём отрезке поиска")]))));
  const fig = withTable(rowsBox, table);
  return card({ title: "Обратный DCF по суждениям книги", tools: fig.button,
    sub: `Значение одного суждения, при котором медиана равна рынку (${fmt.rub(block.target)}); остальные суждения — как в книге.` },
  legend([["key-b50", "диапазон книги"], ["key-line key-ink", "значение книги"], ["key-dot key-market", "нужно рынку"]]), fig.box);
}

function evCard(d) {
  const ce = obj(obj(d.fair_value).center_ev);
  const L = obj(d.layers);
  const mk = obj(d.market);
  if (!isNum(ce.v_star)) return card({ title: "Стоимость бизнеса: модель против рынка", span: 7 }, missing("EV против рынка"));
  const rows = [
    { name: "медиана", a: ce.v0_median, b: ce.v_star, gap: ce.gap_median },
    { name: "точка при центральных значениях", a: ce.v0_point, b: ce.v_star, gap: ce.gap_point },
    ...["analytical", "market_implied", "macro_neutral"].filter((k) => L[k] && isNum(L[k].v0))
      .map((k) => ({ name: `слой «${layerName(d, k)}»`, a: L[k].v0, b: ce.v_star, gap: L[k].v0 / ce.v_star - 1 })),
  ].map((r) => ({ ...r, aLabel: "EV модели", bLabel: "рыночная" }));
  const plot = dumbbellChart(rows, { label: "EV модели и рыночная стоимость бизнеса, млрд ₽" });
  const fig = withTable(plot, () => dataTable([
    { title: "Что сравнивается", value: (r) => upperFirst(r.name), cls: "name" },
    { title: "EV модели, млрд ₽", num: true, value: (r) => fmt.num(r.a, 1) },
    { title: "Рыночная, млрд ₽", num: true, value: (r) => fmt.num(r.b, 1) },
    { title: "Разрыв", num: true, value: (r) => fmt.signedPct(r.gap, 1) },
  ], rows));
  return card({ title: "Стоимость бизнеса: модель против рынка", span: 7, tools: fig.button,
    sub: `Рыночная стоимость бизнеса — ${fmt.bn(ce.v_star, 0)}: при ней модель даёт рыночную цену ${fmt.rub(mk.price)}. EV медианы — ${fmt.bn(ce.v0_median, 0)} (${fmt.signedPct(ce.gap_median, 1)}).` },
  legend([["key-dot key-model", "EV модели"], ["key-dot key-market", "рыночная стоимость бизнеса"]]),
  fig.box,
  el("div", { class: "kpis", style: "margin-top:14px" },
    // Пара мультипликаторов — на одной базе: скорр. EBITDA следующих 12 месяцев.
    isNum(ce.ev_ebitda_ntm_market) ? kpi(fmt.x(ce.ev_ebitda_ntm_market, 2),
      `рынок: EV / скорр. EBITDA следующих 12 мес. (${fmt.num(ce.ebitda_ntm, 0)} млрд ₽)`) : null,
    isNum(ce.ev_ebitda_ntm_median) ? kpi(fmt.x(ce.ev_ebitda_ntm_median, 2), "модель, медиана: EV / та же EBITDA") : null,
    kpi(fmt.rub(ce.rub_per_1pct_ev_median), "цена 1 % EV на акцию"),
    kpi(fmt.bn(mk.claims, 0), "требования на дату оценки")));
}

function brokersCard(d) {
  const b = obj(obj(d.market).brokers);
  const rows = list(b.rows).filter((r) => isNum(r.target));
  if (!rows.length) return card({ title: "Цели инвестдомов", span: 5 }, empty("Целей инвестдомов в выпуске нет."));
  const mk = obj(d.market);
  const model = obj(d.headline).printed_central;
  const targets = rows.map((r) => r.target);
  const lo = Math.min(...targets), hi = Math.max(...targets);
  const plot = chart((W) => {
    const vals = [lo, hi, b.median, mk.price, model].filter(isNum);
    const top = Math.max(...vals) * 1.06;
    const bottom = Math.max(0, Math.min(...vals) * 0.8);
    const x = scale(bottom, top, 10, W - 12);
    const cy = 16;
    const items = [placed(x(b.median), `медиана целей ${fmt.rub(b.median)}`, W),
      placed(x(mk.price), `рынок ${fmt.rub(mk.price)}`, W), placed(x(model), `модель ${fmt.rub(model)}`, W)]
      .filter((t) => isNum(t.x));
    const rowsN = stackLabels(items, 10);
    const H = cy + 24 + rowsN * 17 + 22;
    const svg = svgBox(W, H, "Цели инвестдомов, рыночная цена и медиана модели");
    svg.append(line(x(lo), cy, x(hi), cy, { stroke: "var(--axis)", "stroke-width": 8, "stroke-linecap": "round" }));
    for (const r of rows) {
      // Цель, выставленная до отчёта, — полой точкой.
      const before = r.after_report === false;
      svg.append(sv("circle", { cx: x(r.target), cy, r: 4.5, fill: before ? "var(--surface)" : "var(--third)",
        stroke: before ? "var(--third)" : "var(--surface)", "stroke-width": before ? 2 : 1.5,
        tip: { title: r.broker, rows: [["цель", fmt.rub(r.target)], ["дата", fmt.date(r.date)], ...(r.horizon ? [["горизонт", r.horizon]] : []),
          ...(r.rating ? [["взгляд", r.rating]] : [])], note: before ? "выставлена до отчёта" : r.in_median ? "входит в медиану" : null } }));
    }
    if (isNum(b.median)) svg.append(line(x(b.median), cy - 11, x(b.median), cy + 11, { stroke: "var(--ink)", "stroke-width": 2 }));
    for (const [v, color] of [[mk.price, "var(--market)"], [model, "var(--model)"]]) {
      if (isNum(v)) svg.append(sv("circle", { cx: x(v), cy, r: 6.5, fill: color, stroke: "var(--surface)", "stroke-width": 2 }));
    }
    for (const t of items) svg.append(label(t.x0, cy + 32 + t.row * 17, t.text));
    for (const t of ticks(bottom, top, W < 420 ? 3 : 5)) {
      const s = fmt.num(t);
      const w = textWidth(s, 12.5, 400);
      if (x(t) - w / 2 >= 0 && x(t) + w / 2 <= W) svg.append(text(x(t), H - 3, s, { class: "tick", "text-anchor": "middle" }));
    }
    return svg;
  }, "Цели инвестдомов, рыночная цена и медиана модели");
  const fig = withTable(plot, () => dataTable([
    { title: "Инвестдом", value: (r) => el("span", {}, r.broker, r.in_median ? el("span", { class: "hint" }, "в медиане") : null), cls: "name" },
    { title: "Дата", num: true, value: (r) => fmt.date(r.date) },
    { title: "Цель", num: true, value: (r) => fmt.rub(r.target) },
    { title: "Горизонт", value: (r) => r.horizon || "—", cls: "txt" },
    { title: "Взгляд", value: (r) => r.rating || "—", cls: "txt" },
  ], rows, { detail: (r) => (r.src ? detailsBlock("Источник", ruText(r.src)) : null) }));
  const up = isNum(b.median) && isNum(mk.price) ? b.median / mk.price - 1 : null;
  const after = rows.filter((r) => r.after_report === true).length;
  const before = rows.filter((r) => r.after_report === false).length;
  const n = (k) => `${fmt.num(k)} ${plural(k, ["цель", "цели", "целей"])}`;
  const composition = b.after_report && after + before === rows.length
    ? `${n(rows.length)}: ${fmt.num(after)} после отчёта за ${b.after_report}${before ? `, ${fmt.num(before)} — до него` : ""}`
    : `${n(rows.length)}${b.after_report ? `; отчёт — ${b.after_report}` : ""}`;
  const medianWhat = isNum(b.median_n) ? `медиана ${fmt.num(b.median_n)} ${plural(b.median_n, ["цели", "целей", "целей"])} на 12 мес. после отчёта` : "медиана целей";
  return card({ title: "Цели инвестдомов", span: 5, tools: fig.button, sub: `${composition}; ${medianWhat}` },
  legend([["key-dot key-third", "цель"], ...(before ? [["key-dot key-hollow key-third", "цель до отчёта"]] : []), ["key-line key-ink", "медиана целей"],
    ["key-dot key-market", "рынок"], ["key-dot key-model", "модель"]]),
  fig.box,
  el("div", { class: "kpis", style: "margin-top:10px" },
    kpi(fmt.rub(b.median), medianWhat),
    kpi(`${fmt.num(lo)}–${fmt.num(hi)}${THIN}₽`, "размах всех целей"),
    isNum(up) ? kpi(fmt.signedPct(up, 0), "медиана к рынку") : null));
}

function priceCard(d) {
  const mk = obj(d.market);
  const pts = list(mk.price_history).filter((p) => isNum(p.close) && parseDay(p.date));
  if (pts.length < 2) return card({ title: "Цена акции за 12 месяцев" }, empty("Истории цены в выпуске нет."));
  const t = (iso) => parseDay(iso).getTime();
  const median = obj(d.headline).printed_central;
  const exd = list(mk.ex_dividend).filter((e) => parseDay(e.date) && isNum(e.dps));
  const first = t(pts[0].date), last = t(pts[pts.length - 1].date);
  const monthTicks = [];
  const start = parseDay(pts[0].date);
  for (let k = 1; k <= 13; k++) {
    const m = new Date(start.getFullYear(), start.getMonth() + k, 1).getTime();
    if (m > first && m < last) monthTicks.push(m);
  }
  const monthLabel = (ms) => {
    const day = new Date(ms);
    return day.getMonth() === 0 ? String(day.getFullYear()) : MONTHS_AXIS[day.getMonth()];
  };
  const plot = linesChart([
    { name: "цена закрытия", color: "var(--market)", width: 1.8, points: pts.map((p) => [t(p.date), p.close]) },
  ], { height: 250, xMin: first, xMax: last, xTicks: monthTicks, xFmt: monthLabel, left: 52,
    yFmt: (v) => fmt.num(v), tipFmt: (v) => fmt.rub(v),
    hrefs: isNum(median) ? [{ value: median, text: `медиана модели ${fmt.rub(median)}`, color: "var(--model)", dash: "5 4" }] : [],
    vlines: exd.filter((e) => t(e.date) >= first && t(e.date) <= last).map((e) => ({ x: t(e.date), text: `дивиденд ${fmt.rub(e.dps)}`,
      color: "var(--ink-2)", tip: { title: "Отсечка дивиденда", rows: [["дата", fmt.date(e.date)], ["на акцию", fmt.rub(e.dps)]] } })),
    label: "Цена акции X5 за 12 месяцев" });
  const fig = withTable(plot, () => dataTable([
    { title: "Дата", value: (p) => fmt.date(p.date), cls: "name" },
    { title: "Закрытие", num: true, value: (p) => fmt.rub(p.close, 1) },
    { title: "Дивиденд", num: true, value: (p) => { const e = exd.find((q) => q.date === p.date); return e ? fmt.rub(e.dps) : ""; } },
  ], pts.slice().reverse()));
  // Минимум и максимум — по полному ряду выпуска (график — тонкая выборка).
  const closes = pts.map((p) => p.close);
  const lowPx = isNum(obj(mk.price_min).close) ? mk.price_min.close : Math.min(...closes);
  const highPx = isNum(obj(mk.price_max).close) ? mk.price_max.close : Math.max(...closes);
  return card({ title: "Цена акции за 12 месяцев", tools: fig.button,
    sub: `дневные закрытия (${mk.price_source || "Мосбиржа"}); вертикали — отсечки дивидендов` },
  legend([["key-line key-market", "цена закрытия"], ["key-line key-model", "медиана модели"], ["key-line key-dash", "отсечка дивиденда"]]),
  fig.box,
  el("div", { class: "kpis", style: "margin-top:12px" },
    kpi(fmt.rub(mk.price), `последняя цена · ${fmt.date(mk.price_date)}${mk.price_time ? ` ${mk.price_time}${NBSP}МСК` : ""}`),
    kpi(`${fmt.num(lowPx)}–${fmt.num(highPx)}${THIN}₽`, "минимум и максимум закрытия за период"),
    isNum(mk.dividend_yield_ltm) ? kpi(fmt.pct(mk.dividend_yield_ltm, 1), "дивиденды за 12 месяцев к цене") : null,
    isNum(mk.pe_ltm) ? kpi(fmt.x(mk.pe_ltm, 1), "P/E за 12 месяцев") : null));
}

function peersCard(d) {
  const peers = obj(obj(d.market).peers);
  const rows = list(peers.rows);
  if (!rows.length) return card({ title: "Аналоги на одной базе" }, empty("Аналогов в выпуске нет."));
  const asOf = peers.as_of;
  const withDiv = rows.some((r) => isNum(r.dividends_after_balance) && r.dividends_after_balance > 0);
  return card({ title: "Аналоги на одной базе",
    sub: `EV = капитализация + чистый долг${withDiv ? " + дивиденды, объявленные до даты баланса и выплаченные после" : ""}; `
      + `EBITDA до МСФО 16 за 12 месяцев${asOf ? `; балансы на ${fmt.date(asOf)}` : ""}` },
    dataTable([
      { title: "Компания", value: (r) => el("span", {}, r.name || r.ticker, r.ticker && r.name && r.ticker !== r.name ? el("span", { class: "hint" }, r.ticker) : null), cls: "name" },
      { title: "EV / EBITDA", num: true, value: (r) => (isNum(r.ev_ebitda) ? el("strong", {}, fmt.x(r.ev_ebitda, 2)) : "—") },
      { title: "P/E", num: true, value: (r) => (isNum(r.pe) ? fmt.x(r.pe, 1) : "—") },
      { title: "EV, млрд ₽", num: true, value: (r) => fmt.num(r.ev, 1) },
      { title: "Капитализация", num: true, value: (r) => fmt.num(r.market_cap, 1) },
      { title: "Чистый долг", num: true, value: (r) => fmt.num(r.net_debt, 1) },
      withDiv ? { title: "Дивиденды к выплате", num: true, value: (r) => (isNum(r.dividends_after_balance) && r.dividends_after_balance > 0 ? fmt.num(r.dividends_after_balance, 1) : "—") } : null,
      { title: "EBITDA", num: true, value: (r) => fmt.num(r.ebitda_ltm, 1) },
      { title: "Цена", num: true, value: (r) => (isNum(r.price) ? `${fmt.num(r.price, r.price < 10 ? 4 : r.price < 100 ? 2 : 1)}${THIN}₽ · ${fmt.dateShort(r.price_date)}` : "—") },
    ], rows, {
      rowClass: (r, i) => (r === rows[0] ? "is-pick" : null),
      detail: (r) => (r.basis ? detailsBlock("Как посчитано", ruText(r.basis)) : null),
    }));
}

function tornadoCard(d) {
  const rows = judgementRows(d).filter((r) => isNum(r.price_low) && isNum(r.price_high))
    .sort((a, b) => (b.swing || 0) - (a.swing || 0)).slice(0, 8);
  if (!rows.length) return card({ title: "Цена по одному суждению" }, missing("суждения по цене ошибки"));
  const center = obj(d.fair_value).central;
  const all = rows.flatMap((r) => [r.price_low, r.price_high]).concat(center);
  const stepR = niceStep(Math.max(...all), 4);
  // Отметка края (14 px) не должна стоять на самой границе дорожки: запас в 2 % шага.
  const dom = [Math.max(0, Math.floor((Math.min(...all) - stepR * 0.02) / stepR) * stepR), Math.ceil((Math.max(...all) + stepR * 0.02) / stepR) * stepR];
  const box = el("div", { class: "tornado" }, rows.map((r) => {
    const a = Math.min(r.price_low, r.price_high), b = Math.max(r.price_low, r.price_high);
    return el("div", { class: "tn-row" },
      el("div", { class: "tn-name" }, r.name),
      el("div", { class: "tn-bar" }, barTrack([
        { from: a, to: Math.min(center, b), cls: "is-down" },
        { from: Math.max(center, a), to: b, cls: "is-up" },
      ].filter((s) => s.to > s.from), dom, { center, marks: [
        { at: r.price_low, cls: "end", tip: { title: `${r.name}: нижний край`, rows: [["значение", formatByUnit(r.low, r.unit, d)], ["точка", fmt.rub(r.price_low)]] } },
        { at: r.price_high, cls: "end", tip: { title: `${r.name}: верхний край`, rows: [["значение", formatByUnit(r.high, r.unit, d)], ["точка", fmt.rub(r.price_high)]] } },
      ] })),
      el("div", { class: "tn-vals" }, `${fmt.num(r.price_low)} … ${fmt.num(r.price_high)}${THIN}₽`,
        el("span", { class: "hint" }, `${formatByUnit(r.low, r.unit, d, true)} / ${formatByUnit(r.high, r.unit, d, true)}`)));
  }));
  return card({ title: "Цена по одному суждению при остальных в центре",
    sub: `Главные суждения по одному на краях своих диапазонов; вертикаль — точка ${fmt.rub(center)}. Слева — точка на нижнем крае суждения, справа — на верхнем.` },
  legend([["key-neg", "ниже точки"], ["key-model", "выше точки"]]),
  box,
  el("div", { class: "tn-row tn-axis-row", "aria-hidden": "true" }, el("span"),
    el("div", { class: "tn-axis" }, el("span", {}, fmt.rub(dom[0])), el("span", {}, fmt.rub(dom[1]))), el("span")));
}

/* ───────────────────────────── экран «Расчёт» ───────────────────────────── */

function screenModel(d) {
  const cells = list(obj(d.grid).cells);
  const root = el("div", { class: "screen" },
    screenHead("Расчёт", "Как из допущений получается цена",
      `Стоимость бизнеса считается первой — сеткой из ${fmt.num(cells.length)} сценариев с явными вероятностями; `
      + "цена акции — последним шагом: капитал за вычетом требований и дисконта за управление, на одну акцию."));
  root.append(el("div", { class: "grid" }, flowCard(d)));
  root.append(el("div", { class: "grid section" }, layersCard(d)));
  root.append(el("div", { class: "grid section" }, regimesCard(d)));
  root.append(el("div", { class: "grid section" }, historyCard(d)));
  root.append(el("div", { class: "grid section" }, worldsCard(d)));
  root.append(el("div", { class: "grid section" }, gridCard(d)));
  root.append(el("div", { class: "grid section" }, capexCard(d)));
  return root;
}

function flowCard(d) {
  const fv = obj(d.fair_value);
  const head = obj(d.headline);
  const meta = obj(d.meta);
  const cells = list(obj(d.grid).cells);
  const nW = new Set(cells.map((c) => c.world)).size;
  const nR = new Set(cells.map((c) => c.regime)).size;
  const nC = new Set(cells.map((c) => c.capex)).size;
  const v0 = Object.values(obj(d.layers)).map((l) => obj(l).v0).filter(isNum);
  const an = obj(obj(d.layers).analytical);
  const steps = [
    ["Сетка сценариев", `${fmt.num(cells.length)} ${plural(cells.length, ["клетка", "клетки", "клеток"])}`,
      `${nW} ${plural(nW, ["мир", "мира", "миров"])} ставок × ${nR} ${plural(nR, ["режим", "режима", "режимов"])} маржи × `
      + `${nC} ${plural(nC, ["уровень", "уровня", "уровней"])} capex; полугодия ${periodName(meta.first_period)} — ${periodName(meta.last_period)}, база ${meta.basis || "—"}.`],
    ["Слои", v0.length ? `V0 ${fmt.num(Math.min(...v0), 0)}–${fmt.num(Math.max(...v0), 0)}` : "—",
      "Ожидаемая стоимость бизнеса при трёх наборах весов миров: рыночные ставки как есть, веса, вменённые рынком, свой макро-взгляд; млрд ₽."],
    ["Мост в цену", isNum(an.d) ? `D ${fmt.num(an.d, 0)}` : "—",
      bridgeParts(d).treasury > 0
        ? `Капитал = V0 − требования на дату оценки; цена = (капитал + выручка от продажи казначейского пакета) × (1 − ${fmt.pct(meta.governance_discount, 0)}) на ${fmt.num(meta.shares_mln, 1)} + ${fmt.num(bridgeParts(d).treasury, 1)} млн акций.`
        : `Капитал = V0 − требования на дату оценки; цена = капитал × (1 − ${fmt.pct(meta.governance_discount, 0)}) на ${fmt.num(meta.shares_mln, 1)} млн акций в обращении.`],
    ["Точка", `${fmt.num(fv.low)} → ${fmt.num(fv.central)} → ${fmt.num(fv.high)}`,
      `Все суждения в центре: рыночные ставки → λ = ${fmt.num(bookLambda(d), 2)} → свой взгляд; ₽ на акцию.`],
    ["Полоса по суждениям", isNum(head.central) ? `медиана ${fmt.num(head.central)}` : "—",
      `${fmt.num(head.draws)} прогонов по ${fmt.num(obj(d.uncertainty).axes_count)} суждениям в их диапазонах; печать шагом ${fmt.num(printStep(d))} ₽ → ${fmt.rub(head.printed_central)}.`],
  ];
  return card({ title: "Пять шагов от допущений к цене" },
    el("div", { class: "flow" }, steps.map(([t, big, p]) => el("div", { class: "flow-step" },
      el("h3", {}, t), el("div", { class: "big" }, big), el("p", {}, p)))));
}

function layersCard(d) {
  const L = obj(d.layers);
  const order = LAYER_ORDER.filter((k) => L[k]);
  if (!order.length) return card({ title: "Слои" }, missing("слои"));
  const fv = obj(d.fair_value);
  return card({ title: "Слои: стоимость бизнеса, требования и цена",
    sub: "Одни и те же клетки сетки при трёх наборах весов миров; млрд ₽, цена — ₽ на акцию." },
  dataTable([
    { title: "Слой", value: (k) => el("span", {}, upperFirst(layerName(d, k)), el("span", { class: "hint" }, `веса ${weightsCompact(L[k].world_weights)}`)), cls: "name" },
    { title: "V0", num: true, value: (k) => fmt.num(L[k].v0, 1) },
    { title: "Требования D", num: true, value: (k) => fmt.num(L[k].d, 1) },
    { title: "Капитал", num: true, value: (k) => fmt.num(L[k].equity, 1) },
    { title: "Цена, ₽", num: true, value: (k) => el("strong", {}, fmt.num(L[k].price)) },
    { title: "PV потока", num: true, value: (k) => fmt.num(L[k].pv_fcff, 1) },
    { title: "PV щита", num: true, value: (k) => fmt.num(L[k].pv_shield, 1) },
    { title: "PV терминала", num: true, value: (k) => fmt.num(L[k].pv_terminal, 1) },
    ...(order.some((k) => isNum(L[k].pv_financing)) ? [{ title: "Вычеты", num: true,
      value: (k) => (isNum(L[k].pv_financing) ? fmt.num(-L[k].pv_financing, 1) : "—") }] : []),
    { title: "Доля терминала", num: true, value: (k) => fmt.pct(L[k].terminal_share, 0) },
    { title: "EV / EBITDA вперёд", num: true, value: (k) => fmt.x(L[k].ev_ebitda_fwd, 2) },
    { title: "V0 / D", num: true, value: (k) => fmt.x(L[k].v0_to_d, 2) },
  ], order, { rowClass: (k) => (k === "analytical" ? "is-pick" : null),
    caption: `Веса миров по порядку: ${weightsNames(d, obj(L[order[0]]).world_weights)}.` }),
  el("p", { class: "card-foot" }, `Точка при λ = ${fmt.num(bookLambda(d), 2)}: ${fmt.rub(fv.central)} — между ${fmt.rub(fv.low)} (рыночные ставки как есть) и ${fmt.rub(fv.high)} (свой макро-взгляд).`,
    order.some((k) => isNum(L[k].pv_financing)) ? " V0 = PV потока + PV щита + PV терминала − вычеты финансирования: издержки размещения долга, проценты сверх справедливого спреда и кэрри финансовой подушки (PV)." : ""));
}

const REGIME_COLORS = { stress: "var(--neg)", floor: "var(--axis)", partial: "var(--third)", full: "var(--model)" };
const REGIME_KEYS = { stress: "key-neg", floor: "key-axis", partial: "key-third", full: "key-model" };

// Положение периода на оси лет: год занимает [год, год + 1), годовое число —
// в середине, полугодия — в четвертях; «LT» — через год после последнего.
function periodX(p, lastYear) {
  const s = String(p);
  const h = /^(\d{4})H([12])$/.exec(s);
  if (h) return Number(h[1]) + (h[2] === "1" ? 0.25 : 0.75);
  if (/^\d{4}$/.test(s)) return Number(s) + 0.5;
  if (s === "LT" && isNum(lastYear)) return lastYear + 1.5;
  return null;
}

function regimesCard(d) {
  const R = obj(d.regimes);
  const keys = regimeKeys(d);
  if (!keys.length) return card({ title: "Режимы маржи" }, missing("режимы маржи"));
  const post = (k) => (isNum(R[k].posterior) ? R[k].posterior : R[k].prior);
  const bar = el("div", { class: "stackbar" }, keys.map((k) => el("i", {
    style: `flex:${post(k) || 0} 1 0;background:${REGIME_COLORS[k] || "var(--axis)"}`,
    tip: { title: regimeName(d, k), rows: [["вероятность", fmt.pct(post(k), 1)]] } })));
  const annual = list(R.annual_history).filter((r) => isNum(r.adj_margin));
  const halves = list(R.history).filter((r) => isNum(r.adj_margin));
  const anchor = halves[halves.length - 1];
  // Цель режима рисуется до первого полугодия, где она вышла на долгосрочный
  // уровень; дальше ровная — её заменяет точка «далее». Ось — по годам этих точек.
  const trimmed = (k) => {
    const pts = list(R[k].target).filter((t) => t.period !== "LT" && isNum(periodX(t.period)) && isNum(t.value));
    const at = pts.findIndex((t) => isNum(R[k].lt) && Math.abs(t.value - R[k].lt) < 1e-12);
    return at >= 0 ? pts.slice(0, at + 1) : pts;
  };
  const drawnYears = keys.flatMap((k) => trimmed(k).map((t) => Math.floor(periodX(t.period))));
  const lastYear = Math.max(...drawnYears, ...annual.map((r) => r.year), anchor ? Math.floor(periodX(anchor.period)) : 0);
  const series = [
    { name: "факт: скорр. маржа за год", color: "var(--ink)", width: 1.8, dots: true, r: 3, points: annual.map((r) => [r.year + 0.5, r.adj_margin]) },
  ];
  if (anchor) series.push({ name: `факт ${periodName(anchor.period)}`, color: "var(--ink)", dots: true, hollow: true, line: false, r: 4, points: [[periodX(anchor.period), anchor.adj_margin]] });
  for (const k of keys) {
    const pts = trimmed(k).map((t) => [periodX(t.period), t.value]);
    if (isNum(R[k].lt)) pts.push([lastYear + 1.5, R[k].lt]);
    series.push({ name: regimeName(d, k), color: REGIME_COLORS[k] || "var(--axis)", width: 1.8, dots: false, points: pts });
    const lt = pts[pts.length - 1];
    if (lt) series.push({ name: `${regimeName(d, k)}, далее`, color: REGIME_COLORS[k] || "var(--axis)", dots: true, line: false, r: 3.5, points: [lt] });
  }
  const minX = annual.length ? annual[0].year : 2011;
  const xTicks = [];
  for (let yr = minX; yr <= lastYear; yr += (lastYear - minX > 12 ? 3 : 2)) xTicks.push(yr + 0.5);
  xTicks.push(lastYear + 1.5);
  const plot = linesChart(series, { height: 260, xMin: minX, xMax: lastYear + 2, xTicks, xFmt: (v) => (v > lastYear + 1 ? "далее" : String(Math.floor(v))),
    yPct: true, tipFmt: (v) => fmt.pct(v, 2),
    hrefs: isNum(R.expected_lt) ? [{ value: R.expected_lt, text: `ожидаемая долгосрочная ${fmt.pct(R.expected_lt, 2)}`, short: `ожидаемая ${fmt.pct(R.expected_lt, 2)}`, color: "var(--model)", dash: "2 4", side: "left", below: true }] : [],
    label: "Маржа X5: история и цели режимов" });
  // Таблица — все периоды выпуска по времени, «далее» — последней строкой.
  const order = (p) => (p === "LT" ? Infinity : periodX(p) ?? 1e9);
  const years = [...new Set([...annual.map((r) => String(r.year)), ...keys.flatMap((k) => list(R[k].target).map((t) => String(t.period)))])]
    .sort((a, b) => order(a) - order(b));
  const fig = withTable(plot, () => dataTable([
    { title: "Период", value: (p) => periodName(p), cls: "name" },
    { title: "Факт", num: true, value: (p) => { const r = annual.find((a) => String(a.year) === p) || halves.find((h) => h.period === p); return r ? fmt.pct(r.adj_margin, 2) : ""; } },
    ...keys.map((k) => ({ title: upperFirst(regimeName(d, k)), num: true, value: (p) => { const t = list(R[k].target).find((q) => String(q.period) === p); return t ? fmt.pct(t.value, 2) : ""; } })),
  ], years));
  const up = obj(R.update);
  return card({ title: "Режимы маржи и история маржи X5", tools: fig.button,
    sub: "Скорректированная маржа EBITDA до МСФО 16; цели режимов — от якоря до долгосрочного уровня. Вероятности режимов одинаковы во всех мирах и слоях." },
  bar,
  el("div", { class: "legend" }, keys.map((k) => el("span", {}, el("i", { class: "key", style: `background:${REGIME_COLORS[k]}` }),
    `${regimeName(d, k)} ${fmt.pct(post(k), 1)}`))),
  el("div", { class: "split wide-left", style: "margin-top:16px" },
    el("div", {}, legend([["key-line key-ink", "факт X5"], ...keys.map((k) => [`key-line ${REGIME_KEYS[k] || ""}`, regimeName(d, k)])]), fig.box),
    el("div", {}, dataTable([
      { title: "Режим", value: (k) => upperFirst(regimeName(d, k)), cls: "name" },
      { title: "Маржа далее", num: true, value: (k) => fmt.pct(R[k].lt, 2) },
      { title: "Книга", num: true, value: (k) => fmt.pct(R[k].prior, 1) },
      { title: "После фактов", num: true, value: (k) => el("strong", {}, fmt.pct(post(k), 1)) },
      { title: "Спрос", value: (k) => DEMAND_NAMES[R[k].demand] || R[k].demand || "—", cls: "txt" },
    ], keys, { cls: "compact" }),
    el("p", { class: "card-foot" },
      isNum(R.expected_lt) ? `Ожидаемая долгосрочная маржа — ${fmt.pct(R.expected_lt, 2)}. ` : "",
      isNum(up.sigma_pp) ? `Факт отчёта двигает вероятности режимов: разброс ${fmt.num(up.sigma_pp * 100, 1)}${NBSP}п.п., затухание отклонения ${fmt.num(up.rho, 2)} за полугодие, сдвиг вероятности не больше ${fmt.num((up.cap_pp || 0) * 100, 0)}${NBSP}п.п.; ` : "",
      `наблюдений внесено: ${fmt.num(list(up.observations).length)}.`))));
}

function worldsCard(d) {
  const W_ = obj(d.worlds);
  const keys = worldKeys(d);
  if (!keys.length) return card({ title: "Миры ставок" }, missing("миры ставок"));
  const colors = ["var(--model)", "var(--third)", "var(--market)"];
  const keyCls = ["key-model", "key-third", "key-market"];
  const tenor = (k) => (k === "LT" ? 25 : Number(k));
  const series = keys.map((k, i) => ({
    name: worldName(d, k), color: colors[i % 3], dots: true,
    points: Object.entries(obj(W_[k].zero_curve)).map(([t, v]) => [tenor(t), v]).filter((p) => isNum(p[0]) && isNum(p[1])).sort((a, b) => a[0] - b[0]),
  }));
  const liveCurve = obj(obj(d.live).curve);
  const nodes = obj(liveCurve.nodes);
  if (Object.keys(nodes).length) {
    series.push({ name: `кривая ОФЗ ${fmt.date(liveCurve.as_of)}`, color: "var(--ink)", width: 1.5, dots: true, r: 3,
      points: Object.entries(nodes).map(([t, v]) => [Number(t), v]).filter((p) => isNum(p[1])).sort((a, b) => a[0] - b[0]) });
  }
  const curve = linesChart(series, { height: 240, yPct: true, tipFmt: (v) => fmt.pct(v, 2),
    xTicks: [1, 3, 5, 10, 25], xFmt: (t) => (t === 25 ? "далее" : `${fmt.num(t)} г.`), xMin: 0, xMax: 26, xMap: Math.sqrt,
    label: "Бескупонные кривые миров ставок" });
  const rateSeries = keys.map((k, i) => ({ name: worldName(d, k), color: colors[i % 3], dots: true, r: 3,
    points: list(W_[k].key_rate).map((r) => [r.year, r.value]).filter((p) => isNum(p[1])) }));
  const years = [...new Set(rateSeries.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
  const rates = linesChart(rateSeries, { height: 240, xType: "band", categories: years, xFmt: (y) => String(y),
    yPct: true, tipFmt: (v) => fmt.pct(v, 2), label: "Ключевая ставка по мирам, среднее за год" });
  const tenors = Object.keys(obj(W_[keys[0]].zero_curve));
  const both = el("div", { class: "split" },
    el("div", {}, el("span", { class: "tile-label" }, "Бескупонная кривая"), curve),
    el("div", {}, el("span", { class: "tile-label" }, "Ключевая ставка, среднее за год"), rates));
  const fig = withTable(both, () => dataTable([
    { title: "Мир", value: (k) => worldName(d, k), cls: "name" },
    ...tenors.map((t) => ({ title: t === "LT" ? "кривая далее" : `кривая ${t} г.`, num: true, value: (k) => fmt.pct(obj(W_[k].zero_curve)[t], 2) })),
    ...years.map((y) => ({ title: `ключевая ${y}`, num: true, value: (k) => { const r = list(W_[k].key_rate).find((q) => q.year === y); return r ? fmt.pct(r.value, 2) : "—"; } })),
  ], keys));
  const weight = (k, layer) => obj(W_[k].weights)[layer];
  return card({ title: "Миры ставок", tools: fig.button,
    sub: "Мир — согласованная макротраектория: ключевая ставка, инфляция и кривая, по которой дисконтируется поток. Веса миров задают слои." },
  legend(series.map((s, i) => [i < keys.length ? `key-line ${keyCls[i % 3]}` : "key-line key-ink", s.name])),
  fig.box,
  el("div", { style: "margin-top:16px" }, dataTable([
    { title: "Мир", value: (k) => worldName(d, k), cls: "name" },
    { title: "Свой вес", num: true, value: (k) => fmt.pct(weight(k, "analytical"), 0) },
    { title: "Вменённый рынком", num: true, value: (k) => fmt.pct(weight(k, "market_implied"), 0) },
    { title: "Инфляция далее", num: true, value: (k) => fmt.pct(W_[k].lt_inflation, 1) },
    { title: "Ставка дисконта далее", num: true, value: (k) => fmt.pct(W_[k].r_terminal, 1) },
    { title: "Реальная", num: true, value: (k) => fmt.pct(W_[k].real_terminal, 1) },
    { title: "V0, млрд ₽", num: true, value: (k) => fmt.num(W_[k].v0, 0) },
    { title: "Цена мира", num: true, value: (k) => el("strong", {}, fmt.rub(W_[k].price)) },
  ], keys)),
  el("p", { class: "card-foot" }, `Цена мира — оценка при весе этого мира 100 %. ${worldsSourceText(d)}`));
}

function worldsSourceText(d) {
  const src = obj(d.worlds).source;
  if (!src) return "";
  if (typeof src === "string") return sentence(`Происхождение миров: ${ruText(src)}`);
  const s = obj(src);
  const book = s.book ? String(s.book).replace("magnit-850oa book-", "книга Магнита ") : "";
  return sentence(`Миры общие с моделью Магнита${book ? ` (${book}` : ""}${s.curve_date ? `, кривая ${fmt.date(s.curve_date)}` : ""}${book ? ")" : ""}`);
}

// Пороги цвета теплокарты — «круглые» шаги по размаху цен сетки.
function heatEdges(prices) {
  const pos = prices.filter((p) => isNum(p) && p > 0);
  if (!pos.length) return [];
  const lo = Math.min(...pos), hi = Math.max(...pos);
  const step = niceStep((hi - lo) / 6, 1);
  const start = Math.floor(lo / step) * step + step;
  return Array.from({ length: 6 }, (_, i) => start + i * step);
}
function heatClass(price, edges) {
  if (!isNum(price) || price <= 0) return "h0";
  let i = 0;
  while (i < edges.length && price >= edges[i]) i++;
  return `h${i + 1}`;
}

function gridCard(d) {
  const G = obj(d.grid);
  const cells = list(G.cells);
  if (!cells.length) return card({ title: "Сетка сценариев" }, missing("сетка"));
  const worlds = worldKeys(d).length ? worldKeys(d) : [...new Set(cells.map((c) => c.world))];
  const regimes = regimeKeys(d).length ? regimeKeys(d) : [...new Set(cells.map((c) => c.regime))];
  const capex = capexKeys(d);
  const edges = heatEdges(cells.map((c) => c.price));
  const panels = worlds.map((w) => el("div", {},
    el("div", { class: "heat-title" }, el("strong", {}, worldName(d, w)),
      el("span", {}, isNum(obj(obj(obj(d.worlds)[w]).weights).analytical) ? `свой вес ${fmt.pct(d.worlds[w].weights.analytical, 0)}` : "")),
    el("table", { class: "heat" },
      el("thead", {}, el("tr", {}, el("th", { class: "row-h" }, "режим / capex"), capex.map((x) => el("th", {}, CAPEX_NAMES[x] || x)))),
      el("tbody", {}, regimes.map((r) => el("tr", {},
        el("th", { class: "row-h" }, regimeName(d, r)),
        capex.map((x) => {
          const c = cells.find((g) => g.world === w && g.regime === r && g.capex === x);
          if (!c) return el("td", {}, "—");
          return el("td", { class: heatClass(c.price, edges), tip: { title: `${worldName(d, w)} · ${regimeName(d, r)} · capex ${CAPEX_NAMES[x] || x}`,
            rows: [["цена", fmt.rub(c.price)], ["вероятность (свой взгляд)", fmt.pct(c.p_analytical, 2)],
              ["вменённая рынком", fmt.pct(c.p_market_implied, 2)], ["рыночные ставки", fmt.pct(c.p_neutral, 2)],
              ["EV", fmt.bn(c.ev, 0)], ["требования", fmt.bn(c.d, 0)], ["капитал", fmt.bn(c.equity, 0)],
              ["EV / EBITDA вперёд", fmt.x(c.ev_ebitda_fwd, 2)], ["доля терминала", fmt.pct(c.terminal_share, 0)],
              ["маржа далее", fmt.pct(c.margin_lt, 2)], ["макс. ЧД / EBITDA", fmt.x(c.max_leverage, 2)]],
            note: isNum(c.equity) && c.equity <= 0 ? "капитал клетки не положителен" : null } },
          isNum(c.equity) && c.equity <= 0 ? el("span", { class: "flag", "aria-label": "капитал не положителен" }, "∅") : null,
          el("span", { class: "p" }, fmt.num(c.price)),
          el("span", { class: "w" }, fmt.pct(c.p_analytical, 1)));
        })))))));
  const scaleBar = el("div", { class: "scale" }, el("span", {}, "цена клетки, ₽:"),
    ...[["var(--seq-0)", `до ${fmt.num(edges[0])}`], ...edges.slice(1).map((e, i) => [`var(--seq-${i + 1})`, fmt.num(edges[i])]), [`var(--seq-6)`, `${fmt.num(edges[edges.length - 1])}+`]]
      .flatMap(([c, t]) => [el("i", { style: `background:${c}` }), t]));
  return card({ title: `Сетка: ${fmt.num(cells.length)} сценарных клеток`,
    sub: "Каждая клетка — полный расчёт при своём мире ставок, режиме маржи и уровне capex. В клетке — цена и вероятность в слое «свой макро-взгляд»." },
  el("div", { class: "heat-wrap" }, panels), scaleBar);
}

const CAPEX_COLORS = { low: "var(--third)", base: "var(--model)", high: "var(--market)" };

function capexCard(d) {
  const C = obj(d.capex_levels);
  const levels = capexKeys(d).filter((k) => C[k]);
  const hist = list(C.history).filter((r) => isNum(r.capex_pct) || isNum(r.da_pct));
  if (!levels.length && !hist.length) return card({ title: "Capex" }, missing("уровни capex"));
  const series = [
    { name: "capex / выручка, факт", color: "var(--ink)", width: 1.8, dots: true, r: 3, points: hist.map((r) => [r.year + 0.5, r.capex_pct]) },
    { name: "D&A и обесценение / выручка, факт", color: "var(--ink-2)", width: 1.4, dashed: true, points: hist.map((r) => [r.year + 0.5, r.da_pct]) },
    ...levels.map((k) => ({ name: `поддерживающий, ${CAPEX_NAMES[k] || k}`, color: CAPEX_COLORS[k] || "var(--axis)", width: 1.8, dots: true, r: 2.5,
      points: list(C[k].maintenance).map((r) => [Number(r.year) + 0.5, r.value]).filter((p) => isNum(p[0]) && isNum(p[1])) })),
  ];
  const xs = series.flatMap((s) => s.points.map((p) => p[0]));
  const x0 = Math.floor(Math.min(...xs)), x1 = Math.ceil(Math.max(...xs));
  const xTicks = [];
  for (let yr = x0; yr <= x1; yr += (x1 - x0 > 12 ? 3 : 2)) xTicks.push(yr + 0.5);
  const plot = linesChart(series, { height: 240, xMin: x0, xMax: x1, xTicks, xFmt: (v) => String(Math.floor(v)),
    yPct: true, tipFmt: (v) => fmt.pct(v, 2), label: "Capex, амортизация и обесценение к выручке: история и поддерживающий capex по уровням" });
  const years = [...new Set(xs.map((v) => Math.floor(v)))].sort((a, b) => a - b);
  const fig = withTable(plot, () => dataTable([
    { title: "Год", value: (y) => String(y), cls: "name" },
    { title: "Capex, факт", num: true, value: (y) => { const r = hist.find((h) => h.year === y); return r ? fmt.pct(r.capex_pct, 2) : ""; } },
    { title: "D&A и обесценение, факт", num: true, value: (y) => { const r = hist.find((h) => h.year === y); return r ? fmt.pct(r.da_pct, 2) : ""; } },
    ...levels.map((k) => ({ title: upperFirst(CAPEX_NAMES[k] || k), num: true,
      value: (y) => { const r = list(C[k].maintenance).find((m) => Number(m.year) === y); return r ? fmt.pct(r.value, 2) : ""; } })),
  ], years));
  const regimes = regimeKeys(d);
  return card({ title: "Capex: история и уровни поддерживающих вложений", tools: fig.button,
    sub: "Доли выручки. Поддерживающий capex — без открытий и инфраструктуры роста; открытия считаются от прироста площади по стоимости квадратного метра." },
  legend([["key-line key-ink", "capex, факт"], ["key-line key-dash", "D&A и обесценение, факт"], ...levels.map((k) => [`key-line ${k === "low" ? "key-third" : k === "high" ? "key-market" : "key-model"}`, `поддерживающий: ${CAPEX_NAMES[k] || k}`])]),
  el("div", { class: "split wide-left" },
    el("div", {}, fig.box),
    el("div", {},
      dataTable([
        { title: "Режим", value: (r) => upperFirst(regimeName(d, r)), cls: "name" },
        ...levels.map((k) => ({ title: upperFirst(CAPEX_NAMES[k] || k), num: true, value: (r) => fmt.pct(obj(C[k].p_given_regime)[r], 0) })),
      ], regimes, { cls: "compact", caption: "Вероятность уровня capex в режиме маржи" }),
      el("div", { class: "kpis", style: "margin-top:14px" },
        ...levels.map((k) => kpi(fmt.pct(C[k].lt, 1), `поддерживающий далее, ${CAPEX_NAMES[k] || k}`)),
        isNum(C.price_per_m2) ? kpi(formatByUnit(C.price_per_m2, "bn_per_m2"), "стоимость открытия") : null,
        isNum(C.infra_per_m2) ? kpi(formatByUnit(C.infra_per_m2, "bn_per_m2"), "инфраструктура роста") : null,
        isNum(C.maintenance_area_share) ? kpi(fmt.pct(C.maintenance_area_share, 0), "доля поддерживающего capex, идущая за площадью") : null))));
}

// Части столбика в сумме дают выручку года: форматы и прочее. Цифровые бизнесы
// уже внутри форматов (экспресс-доставка) — в стопку не кладутся, только в подсказку.
const FORMAT_NAMES = { pyaterochka: "Пятёрочка", perekrestok: "Перекрёсток", chizhik: "Чижик", karusel: "Карусель", other: "прочее" };
const FORMAT_COLORS = { pyaterochka: "var(--model)", perekrestok: "var(--third)", chizhik: "var(--market)", karusel: "var(--seq-1)", other: "var(--axis)" };
const FORMAT_KEYS = { pyaterochka: "key-model", perekrestok: "key-third", chizhik: "key-market", karusel: "key-seq", other: "key-axis" };

function historyCard(d) {
  const H = obj(d.history);
  const annual = list(H.annual).filter((r) => isNum(r.year));
  if (!annual.length) return card({ title: "История X5" }, missing("история"));
  const formats = list(H.formats).filter((r) => isNum(r.year));
  const fKeys = Object.keys(FORMAT_NAMES).filter((k) => formats.some((r) => isNum(r[k])));
  const revenue = formats.length && fKeys.length
    ? columnsChart(formats.map((r) => ({ key: r.year, label: String(r.year), tipTitle: `Выручка ${r.year}`,
      parts: fKeys.map((k) => ({ value: r[k], color: FORMAT_COLORS[k], name: FORMAT_NAMES[k] })),
      extra: isNum(r.digital) && r.digital > 0 ? [["цифровые бизнесы (внутри форматов)", fmt.bn(r.digital, 0)]] : [] })),
    { height: 230, fmt: (v) => fmt.bn(v, 0), short: (v) => fmt.num(v, 0), label: "Выручка X5 по форматам, млрд ₽" })
    : columnsChart(annual.map((r) => ({ key: r.year, label: String(r.year), value: r.revenue, tipTitle: `Выручка ${r.year}` })),
      { height: 230, color: "var(--axis)", valueName: "млрд ₽", fmt: (v) => fmt.bn(v, 0), short: (v) => fmt.num(v, 0), label: "Выручка X5, млрд ₽" });
  const lflRows = annual.filter((r) => isNum(r.lfl));
  const lfl = linesChart([
    { name: "LFL-продажи", color: "var(--ink)", width: 2, dots: true, r: 3, points: lflRows.map((r) => [String(r.year), r.lfl]) },
    { name: "LFL-трафик", color: "var(--third)", width: 1.6, dots: true, r: 2.5, points: lflRows.map((r) => [String(r.year), r.lfl_traffic]) },
    { name: "LFL-чек", color: "var(--model)", width: 1.6, dots: true, r: 2.5, points: lflRows.map((r) => [String(r.year), r.lfl_ticket]) },
  ], { xType: "band", categories: lflRows.map((r) => String(r.year)), height: 230, yPct: true, tipFmt: (v) => fmt.pct(v, 1),
    label: "Сопоставимые продажи: трафик и чек" });
  const both = el("div", { class: "split" },
    el("div", {}, el("span", { class: "tile-label" }, formats.length ? "Выручка по форматам, млрд ₽" : "Выручка, млрд ₽"),
      formats.length ? legend(fKeys.map((k) => [FORMAT_KEYS[k], FORMAT_NAMES[k]])) : null, revenue),
    el("div", {}, el("span", { class: "tile-label" }, "Сопоставимые продажи (LFL)"),
      legend([["key-line key-ink", "продажи"], ["key-line key-third", "трафик"], ["key-line key-model", "чек"]]), lflRows.length > 1 ? lfl : empty("LFL в выпуске нет.")));
  const fig = withTable(both, () => dataTable([
    { title: "Год", value: (r) => String(r.year), cls: "name" },
    { title: "Выручка", num: true, value: (r) => fmt.num(r.revenue, 0) },
    { title: "Рост", num: true, value: (r) => fmt.pct(r.growth, 1) },
    { title: "Скорр. маржа", num: true, value: (r) => fmt.pct(r.adj_margin, 2) },
    { title: "Отчётная маржа", num: true, value: (r) => fmt.pct(r.rep_margin, 2) },
    { title: "Capex / выручка", num: true, value: (r) => fmt.pct(r.capex_pct, 2) },
    { title: "D&A и обесценение / выручка", num: true, value: (r) => fmt.pct(r.da_pct, 2) },
    { title: "ЧД / EBITDA", num: true, value: (r) => fmt.x(r.leverage, 2) },
    { title: "LFL", num: true, value: (r) => fmt.pct(r.lfl, 1) },
    { title: "Трафик", num: true, value: (r) => fmt.pct(r.lfl_traffic, 1) },
    { title: "Чек", num: true, value: (r) => fmt.pct(r.lfl_ticket, 1) },
    { title: "Площадь, тыс. м²", num: true, value: (r) => fmt.num(r.area_end, 0) },
    { title: "Магазинов", num: true, value: (r) => fmt.num(r.stores_end, 0) },
  ], annual.slice().reverse()));
  const last = annual[annual.length - 1];
  return card({ title: "История X5: выручка, форматы и сопоставимые продажи", tools: fig.button,
    sub: last ? `${last.year}: выручка ${fmt.bn(last.revenue, 0)} (${fmt.signedPct(last.growth, 1)}), площадь ${fmt.num(last.area_end, 0)} тыс. м², магазинов ${fmt.num(last.stores_end, 0)}` : "" },
  fig.box);
}

/* ───────────────────────────── экран «Ближайший отчёт» ───────────────────────────── */

const BENCH_NAMES = {
  same_half_last_year: "то же полугодие год назад",
  mean_two_halves: "среднее двух последних полугодий",
  mean_of_two_halves: "среднее двух последних полугодий",
  last_half: "как прошлое полугодие",
  previous_half: "как прошлое полугодие",
};
const benchName = (name) => BENCH_NAMES[name] || String(name || "");

function screenReport(d) {
  const nr = obj(d.next_report);
  const exp = obj(nr.expectation);
  const g = obj(nr.guidance);
  const n = obj(nr.neutral);
  const facts = [];
  if (isNum(exp.margin)) facts.push(`Модель ждёт скорректированную маржу ${fmt.pct(exp.margin, 2)}`
    + (isNum(exp.revenue_growth) ? ` и рост выручки ${fmt.signedPct(exp.revenue_growth, 1)} год к году` : ""));
  if (isNum(g.required_h2_margin)) facts.push(`чтобы выполнить прогноз на год, компании нужна маржа ${fmt.pct(g.required_h2_margin, 2)}`);
  if (isNum(n.median)) facts.push(`при марже ${fmt.pct(n.median, 2)} медиана не изменится`);
  const root = el("div", { class: "screen" },
    screenHead("Ближайший отчёт", nr.period ? `${periodName(nr.period)}: что даст отчёт` : "Что даст ближайший отчёт",
      facts.length ? sentence(upperFirst(facts.join("; "))) : ""));
  root.append(el("div", { class: "grid" }, reportCalendarCard(d), expectationCard(d)));
  root.append(el("div", { class: "grid section" }, impactCard(d)));
  root.append(el("div", { class: "grid section" }, journalCard(d)));
  root.append(el("div", { class: "grid section" }, eventsCard(d, 99, 12)));
  return root;
}

function reportCalendarCard(d) {
  const nr = obj(d.next_report);
  const from = obj(d.meta).valuation_date;
  const events = list(nr.events).slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const ev = nextReportEvent(d);
  const days = ev ? daysBetween(from, ev.date) : null;
  return card({ title: "Календарь отчёта", span: 5, sub: nr.period ? `${periodName(nr.period)} · отсчёт от даты оценки ${fmt.date(from)}` : "" },
    ev ? el("div", { class: "countdown" },
      el("span", { class: "big" }, fmt.num(days)),
      el("span", { class: "ink-2" }, `${plural(days, ["день", "дня", "дней"])} до ${ev.confirmed ? "" : "≈" + NBSP}${fmt.dateLong(ev.date)}`)) : null,
    ev ? el("p", { class: "ink-2", style: "margin-top:4px" }, el("strong", {}, ev.title)) : null,
    events.length ? el("ul", { class: "list", style: "margin-top:12px" }, events.map((e) => {
      const left = daysBetween(from, e.date);
      return el("li", {},
        el("span", { class: "t" }, e.title, el("span", { class: "muted" },
          [KIND_NAMES[e.kind], isNum(left) ? (left >= 0 ? `через ${fmt.days(left)}` : "прошло") : ""].filter(Boolean).join(" · "))),
        el("span", { class: "v" }, `${e.confirmed ? "" : "≈" + NBSP}${fmt.date(e.date)}`));
    })) : empty("Событий отчёта в выпуске нет."),
    events.some((e) => e.note) ? el("div", { class: "card-foot" }, detailsBlock("Примечания", el("div", {},
      events.filter((e) => e.note).map((e) => el("p", {}, `${fmt.date(e.date)} — ${ruText(e.note)}`))))) : null);
}

// Отметки на одной шкале: модель, режимы, эталоны, прогноз компании.
// marks: [{v, kind, text, tip}]; band: [lo, hi] — полоса прогноза компании.
function stripChart(marks, opts = {}) {
  return chart((W) => {
    const vals = marks.map((m) => m.v).concat(opts.band || []).filter(isNum);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.05 || 0.002;
    const x = scale(lo - pad, hi + pad, 10, W - 10);
    const cy = 26;
    const labelled = marks.filter((m) => m.text).map((m) => placed(x(m.v), m.text, W, 12.5, m.kind === "model" ? 640 : 520));
    marks.filter((m) => m.text).forEach((m, i) => { labelled[i].strong = m.kind === "model"; });
    const rows = stackLabels(labelled, 10);
    const H = cy + 22 + rows * 16 + 20;
    const svg = svgBox(W, H, opts.label);
    svg.append(line(10, cy, W - 10, cy, { class: "gridline" }));
    if (opts.band && isNum(opts.band[0]) && isNum(opts.band[1])) {
      svg.append(sv("rect", { x: x(opts.band[0]), y: cy - 9, width: Math.max(3, x(opts.band[1]) - x(opts.band[0])), height: 18, rx: 6,
        fill: "var(--market-wash)", stroke: "var(--market)", "stroke-width": 1, tip: opts.bandTip || null }));
    }
    const order = { regime: 0, bench: 1, guide: 2, model: 3 };
    for (const m of marks.slice().sort((a, b) => (order[a.kind] ?? 0) - (order[b.kind] ?? 0))) {
      if (!isNum(m.v)) continue;
      const mx = x(m.v);
      if (m.kind === "regime") svg.append(line(mx, cy - 7, mx, cy + 7, { stroke: "var(--axis)", "stroke-width": 2, tip: m.tip || null }));
      else if (m.kind === "guide") svg.append(line(mx, cy - 13, mx, cy + 13, { stroke: "var(--market)", "stroke-width": 2.2, tip: m.tip || null }));
      else if (m.kind === "bench") svg.append(sv("circle", { cx: mx, cy, r: 5, fill: "var(--third)", stroke: "var(--surface)", "stroke-width": 2, tip: m.tip || null }));
      else svg.append(sv("circle", { cx: mx, cy, r: 7, fill: "var(--model)", stroke: "var(--surface)", "stroke-width": 2, tip: m.tip || null }));
    }
    for (const t of labelled) svg.append(label(t.x0, cy + 30 + t.row * 16, t.text, { class: t.strong ? "label-strong" : "label" }));
    const tickMarks = ticks(lo - pad, hi + pad, W < 420 ? 3 : 5);
    const tickLabel = opts.pct ? pctTicks(tickMarks) : opts.fmt;
    for (const t of tickMarks) {
      const s = tickLabel(t);
      const w = textWidth(s, 12.5, 400);
      if (x(t) - w / 2 >= 0 && x(t) + w / 2 <= W) svg.append(text(x(t), H - 3, s, { class: "tick", "text-anchor": "middle" }));
    }
    return svg;
  }, opts.label);
}

function expectationCard(d) {
  const nr = obj(d.next_report);
  const exp = obj(nr.expectation);
  const g = obj(nr.guidance);
  const benches = list(nr.benchmarks);
  if (!isNum(exp.margin)) return card({ title: "Ожидание модели", span: 7 }, missing("ожидание модели"));
  const p = periodName(nr.period);
  const marginMarks = [
    { v: exp.margin, kind: "model", text: `модель ${fmt.pct(exp.margin, 2)}`, tip: { title: `Ожидание модели на ${p}`, rows: [["маржа", fmt.pct(exp.margin, 2)]] } },
    ...list(exp.by_regime).filter((r) => isNum(r.margin)).map((r) => ({ v: r.margin, kind: "regime",
      tip: { title: `Режим «${regimeName(d, r.regime)}»`, rows: [["маржа", fmt.pct(r.margin, 2)]] } })),
    ...benches.filter((b) => isNum(b.margin)).map((b) => ({ v: b.margin, kind: "bench",
      tip: { title: `Эталон: ${benchName(b.name)}`, rows: [["маржа", fmt.pct(b.margin, 2)]] } })),
    ...(isNum(g.required_h2_margin) ? [{ v: g.required_h2_margin, kind: "guide", text: `нужно компании ${fmt.pct(g.required_h2_margin, 1)}`,
      tip: { title: "Маржа полугодия, нужная для прогноза на год", rows: [["маржа", fmt.pct(g.required_h2_margin, 2)], ["прогноз на год", isNum(g.margin_min) ? `≥ ${fmt.pct(g.margin_min, 1)}` : "—"]] } }] : []),
  ];
  const growthMarks = [
    ...(isNum(exp.revenue_growth) ? [{ v: exp.revenue_growth, kind: "model", text: `модель ${fmt.signedPct(exp.revenue_growth, 1)}`,
      tip: { title: `Ожидание модели на ${p}`, rows: [["рост выручки", fmt.signedPct(exp.revenue_growth, 1)]] } }] : []),
    ...benches.filter((b) => isNum(b.revenue_growth)).map((b) => ({ v: b.revenue_growth, kind: "bench",
      tip: { title: `Эталон: ${benchName(b.name)}`, rows: [["рост выручки", fmt.signedPct(b.revenue_growth, 1)]] } })),
    ...(isNum(g.required_h2_growth) ? [{ v: g.required_h2_growth, kind: "guide", text: `нужно компании ${fmt.signedPct(g.required_h2_growth, 1)}`,
      tip: { title: "Рост полугодия, нужный для нижней границы прогноза", rows: [["рост", fmt.signedPct(g.required_h2_growth, 1)]] } }] : []),
  ];
  // Годовой прогноз компании — в тексте и таблице; на шкале полугодия — полоса
  // роста 2П, при котором год попадает в прогноз (та же величина, что у отметок).
  const yearBand = Array.isArray(g.revenue_growth) && g.revenue_growth.every(isNum) ? g.revenue_growth : null;
  const growthBand = Array.isArray(g.required_h2_growth_range) && g.required_h2_growth_range.every(isNum) ? g.required_h2_growth_range : null;
  const yearText = yearBand ? `${fmt.num(yearBand[0] * 100, 0)}–${fmt.num(yearBand[1] * 100, 0)}${THIN}%` : "—";
  const bandText = growthBand ? `${fmt.signedPct(growthBand[0], 1)}…${fmt.signedPct(growthBand[1], 1)}` : null;
  const charts = el("div", {},
    el("span", { class: "tile-label" }, `Скорректированная маржа EBITDA, ${p}`),
    stripChart(marginMarks, { pct: true, label: "Маржа: модель, режимы, эталоны и прогноз компании" }),
    growthMarks.length ? el("span", { class: "tile-label", style: "display:block;margin-top:12px" }, `Рост выручки год к году, ${p}`) : null,
    growthMarks.length ? stripChart(growthMarks, { band: growthBand, pct: true,
      bandTip: growthBand ? { title: `Рост ${p}, при котором год попадает в прогноз`, rows: [[`рост ${p}`, bandText], ["прогноз на год", yearText]] } : null,
      label: "Рост выручки: модель, эталоны и прогноз компании" }) : null);
  const fig = withTable(charts, () => dataTable([
    { title: "Кто", value: (r) => r.name, cls: "name" },
    { title: "Маржа", num: true, value: (r) => r.margin },
    { title: "Рост выручки", num: true, value: (r) => r.growth },
  ], [
    { name: "Ожидание модели", margin: fmt.pct(exp.margin, 2), growth: fmt.signedPct(exp.revenue_growth, 1) },
    ...list(exp.by_regime).map((r) => ({ name: `модель в режиме «${regimeName(d, r.regime)}»`, margin: fmt.pct(r.margin, 2), growth: "" })),
    ...benches.map((b) => ({ name: `Эталон: ${benchName(b.name)}`, margin: fmt.pct(b.margin, 2), growth: fmt.signedPct(b.revenue_growth, 1) })),
    { name: "Прогноз компании на год", margin: isNum(g.margin_min) ? `≥ ${fmt.pct(g.margin_min, 1)}` : "—", growth: yearText },
    { name: `Нужно компании в ${p}`, margin: fmt.pct(g.required_h2_margin, 2), growth: bandText || fmt.signedPct(g.required_h2_growth, 1) },
  ]));
  return card({ title: `Ожидание модели на ${p}`, span: 7, tools: fig.button,
    sub: "против прогноза компании и наивных эталонов на одной шкале" },
  el("div", { class: "kpis" },
    kpi(fmt.pct(exp.margin, 2), "скорр. маржа EBITDA"),
    isNum(exp.revenue_growth) ? kpi(fmt.signedPct(exp.revenue_growth, 1), "рост выручки год к году") : null,
    isNum(exp.revenue) ? kpi(fmt.bn(exp.revenue, 0), "выручка полугодия") : null,
    isNum(exp.adj_ebitda) ? kpi(fmt.bn(exp.adj_ebitda, 1), "скорр. EBITDA") : null),
  el("div", { style: "margin-top:14px" }, legend([["key-dot key-model", "модель"], ["key-line key-axis", "модель по режимам"],
    ["key-dot key-third", "наивные эталоны"], ["key-line key-market", "нужно компании для прогноза"],
    ...(growthBand ? [["key-band", `рост ${p}, нужный для прогноза на год`]] : [])]), fig.box),
  el("p", { class: "card-foot" },
    `Прогноз компании на год: маржа ${isNum(g.margin_min) ? "не ниже " + fmt.pct(g.margin_min, 1) : "—"}`,
    yearBand ? `, выручка ${fmt.signedPct(yearBand[0], 0)}…${fmt.signedPct(yearBand[1], 0)}` : "",
    Array.isArray(g.capex_pct) && g.capex_pct.every(isNum) ? `, capex ${fmt.num(g.capex_pct[0] * 100, 1)}–${fmt.num(g.capex_pct[1] * 100, 1)}${THIN}% выручки` : "",
    isNum(g.openings) ? `, открытий больше ${fmt.num(g.openings)}` : "", ".",
    g.src ? [" ", detailsBlock("Источник", ruText(g.src))] : null));
}

// «Что даст отчёт»: факт маржи → медиана (и точка) по строкам выпуска
// `next_report.table`; вертикали — нейтральная маржа и ожидание модели.
function impactChart(d, compact = false) {
  const nr = obj(d.next_report);
  const rows = list(nr.table).filter((r) => isNum(r.margin)).sort((a, b) => a.margin - b.margin);
  const xs = rows.map((r) => r.margin);
  const span = (Math.max(...xs) - Math.min(...xs)) || 0.01;
  const now = obj(d.headline).central;
  const market = obj(d.market).price;
  const n = obj(nr.neutral);
  const exp = obj(nr.expectation);
  const series = [{ name: "медиана", color: "var(--model)", width: 2.2, dots: true, r: compact ? 3.5 : 4.5, points: rows.map((r) => [r.margin, r.median]) }];
  if (!compact && rows.some((r) => isNum(r.point))) {
    series.push({ name: "точка", color: "var(--ink-2)", width: 1.4, dashed: true, dots: false, points: rows.map((r) => [r.margin, r.point]) });
  }
  const vlines = [];
  if (isNum(n.median)) vlines.push({ x: n.median, text: `нейтральная ${fmt.pct(n.median, 2)}`, color: "var(--ink-2)", dash: "1 3",
    tip: { title: "Нейтральная маржа", rows: [["для медианы", fmt.pct(n.median, 2)], ["для точки", fmt.pct(n.point, 2)]] } });
  if (!compact && isNum(exp.margin)) vlines.push({ x: exp.margin, text: `ожидание ${fmt.pct(exp.margin, 2)}`, color: "var(--model)", dash: "3 3" });
  return linesChart(series, {
    height: compact ? 210 : 300, left: 50,
    xMin: Math.min(...xs) - span * 0.06, xMax: Math.max(...xs) + span * 0.06,
    xTicks: compact ? [xs[0], xs[Math.floor(xs.length / 2)], xs[xs.length - 1]] : xs, xFmt: (v) => fmt.num(v * 100, 1) + THIN + "%",
    yFmt: (v) => fmt.num(v), tipFmt: (v) => fmt.rub(v), top: compact ? 30 : 36, yTicks: compact ? 3 : 6,
    hrefs: [{ value: now, text: `сейчас ${fmt.rub(now)}`, color: "var(--ink)", side: "left" },
      { value: market, text: `рынок ${fmt.rub(market)}`, color: "var(--market)", side: "left" }].filter((r) => isNum(r.value)),
    vlines, label: `Что даст отчёт: маржа ${periodName(nr.period)} и медиана` });
}

function impactCard(d) {
  const nr = obj(d.next_report);
  const rows = list(nr.table);
  if (!rows.length) return card({ title: "Что даст отчёт" }, missing("что даст отчёт"));
  const keys = regimeKeys(d);
  const fig = withTable(impactChart(d, false), () => dataTable([
    { title: "Маржа отчёта", value: (r) => fmt.pct(r.margin, 1), cls: "name" },
    { title: "Медиана", num: true, value: (r) => el("strong", {}, fmt.rub(r.median)) },
    { title: "Изменение", num: true, value: (r) => fmt.signedRub(r.d_median) },
    { title: "Точка", num: true, value: (r) => fmt.rub(r.point) },
    { title: "Изменение точки", num: true, value: (r) => fmt.signedRub(r.d_point) },
    ...keys.map((k) => ({ title: upperFirst(regimeName(d, k)), num: true, value: (r) => fmt.pct(obj(r.posterior)[k], 1) })),
  ], rows));
  const byMargin = rows.slice().sort((a, b) => a.margin - b.margin);
  const lo = byMargin[0], hi = byMargin[byMargin.length - 1];
  return card({ title: `Что даст отчёт: факт маржи ${periodName(nr.period)} → оценка`, tools: fig.button,
    sub: "Факт маржи сдвигает вероятности режимов и отклонение маржи от цели; с ними — медиану и точку." },
  legend([["key-line key-model", "медиана"], ["key-line key-dash", "точка"], ["key-line key-ink", "медиана сейчас"],
    ["key-line key-market", "рынок"]]),
  fig.box,
  el("p", { class: "card-foot" }, neutralSentence(d),
    lo && hi ? ` На краях таблицы: маржа ${fmt.pct(lo.margin, 1)} — медиана ${fmt.rub(lo.median)}, маржа ${fmt.pct(hi.margin, 1)} — ${fmt.rub(hi.median)}.` : ""));
}

function journalCard(d) {
  const J = obj(d.journal);
  const entries = list(J.entries).slice().sort((a, b) => String(b.recorded_at).localeCompare(String(a.recorded_at)));
  if (!entries.length) return card({ title: "Журнал прогнозов" }, empty("Записей в журнале ещё нет."));
  const bText = (map, f) => Object.entries(obj(map)).filter(([, v]) => isNum(v)).map(([k, v]) => `${benchName(k)} ${f(v)}`).join("; ") || "—";
  return card({ title: "Журнал прогнозов", sub: "неизменяемые записи: прогноз модели и наивные эталоны до отчёта, факт и ошибки — после" },
    dataTable([
      { title: "Показатель", value: (r) => el("span", {}, targetName(r.target),
        el("span", { class: "hint" }, `${periodName(r.period)} · записан ${fmt.date(r.recorded_at)}`)), cls: "name" },
      { title: "Прогноз", num: true, value: (r) => el("strong", {}, targetValue(r.target, r.forecast)) },
      { title: "Факт", num: true, value: (r) => (isNum(r.actual) ? targetValue(r.target, r.actual) : "ждём") },
      { title: "Ошибка", num: true, value: (r) => (isNum(obj(r.errors).forecast) ? fmt.pp(r.errors.forecast, 2) : "—") },
    ], entries, { detail: (r) => el("p", { class: "muted small" },
      sentence(`Эталоны: ${bText(r.benchmarks, (v) => targetValue(r.target, v))}`
        + (isNum(r.actual) && Object.keys(obj(obj(r.errors).benchmarks)).length ? `; их ошибки: ${bText(r.errors.benchmarks, (v) => fmt.pp(v, 2))}` : ""))) }),
    J.rule || J.status ? el("div", { class: "card-foot" },
      J.status ? el("p", {}, sentence(upperFirst(ruText(J.status)))) : null,
      J.rule ? detailsBlock("Правило допуска прогноза к цене", sentence(upperFirst(ruText(J.rule)))) : null) : null);
}

/* ───────────────────────────── экран «Деньги и долг» ───────────────────────────── */

// Разложение требований на дату оценки и строки моста из отчётности. Выпуск
// кладёт их в `debt.bridge` (список строк или объект с `lines`,
// `rows_at_valuation`, `total`) — витрина читает обе формы.
function bridgeParts(d) {
  const debt = obj(d.debt);
  const b = debt.bridge;
  const lines = Array.isArray(b) ? b : list(obj(b).lines || obj(b).rows || obj(b).items);
  const atVal = obj(b).rows_at_valuation !== undefined ? b.rows_at_valuation : debt.rows_at_valuation;
  const rows = Array.isArray(atVal) ? atVal : list(obj(atVal).rows);
  const total = [obj(b).total, obj(atVal).total, debt.total].find(isNum);
  // Разложение EV (из чего складывается V0) — все строки выпуска, какие есть.
  const evRows = list(obj(b).ev_rows).filter((r) => isNum(r.amount));
  // Что прибавляется к капиталу в формуле цены (казначейский пакет) — тоже все строки.
  const equityRows = list(obj(b).equity_rows).filter((r) => isNum(r.amount) && r.amount !== 0);
  const treasury = isNum(obj(b).treasury_mln) ? b.treasury_mln : 0;
  return { lines, rows: rows.filter((r) => isNum(r.amount)), total, evRows, equityRows, treasury };
}

function screenDebt(d) {
  const a = obj(obj(d.debt).anchor);
  const pol = obj(obj(d.dividends).policy);
  const { total } = bridgeParts(d);
  const tl = list(pol.target_leverage);
  const lede = [
    isNum(total) ? `Требования на дату оценки — ${fmt.bn(total, 0)}` : "",
    tl.length === 2 ? `дивиденды платятся при чистом долге ${fmt.num(tl[0], 1)}–${fmt.num(tl[1], 1)}× EBITDA на конец года` : "",
    isNum(pol.no_pay_above) ? `выше ${fmt.num(pol.no_pay_above, 1)}× выплат нет` : "",
  ].filter(Boolean).join("; ");
  const root = el("div", { class: "screen" },
    screenHead("Деньги и долг",
      isNum(a.net_debt) ? `Чистый долг ${fmt.bn(a.net_debt, 0)} — ${fmt.x(a.leverage, 2)} EBITDA` : "Как бизнес обслуживает долг",
      lede ? sentence(lede) : ""));
  root.append(el("div", { class: "grid" }, debtKpis(d)));
  root.append(el("div", { class: "grid section" }, bridgeCard(d), dividendCard(d)));
  root.append(el("div", { class: "grid section" }, dividendHistoryCard(d)));
  root.append(el("div", { class: "grid section" }, annualCard(d)));
  root.append(el("div", { class: "grid section" }, wallCard(d), loansCard(d)));
  root.append(el("div", { class: "grid section" }, bondsCard(d)));
  return root;
}

function debtKpis(d) {
  const a = obj(obj(d.debt).anchor);
  if (!Object.keys(a).length) return card({ title: "Долг" }, missing("долг на дату якоря"));
  const { total } = bridgeParts(d);
  return card({ title: "Долг", sub: `на ${fmt.date(a.as_of)} и на дату оценки ${fmt.date(obj(d.meta).valuation_date)} · база ${obj(d.meta).basis || "до МСФО 16"}` },
    el("div", { class: "kpis" },
      kpi(fmt.bn(a.net_debt, 1), `чистый долг на ${fmt.date(a.as_of)}`),
      kpi(fmt.x(a.leverage, 2), "чистый долг / EBITDA"),
      isNum(total) ? kpi(fmt.bn(total, 1), "требования на дату оценки") : null,
      kpi(fmt.bn(a.total_debt, 1), "общий долг"),
      kpi(fmt.bn(a.cash, 1), "денежные средства"),
      kpi(fmt.bn(a.credit_lines_unused, 0), "невыбранные кредитные линии"),
      kpi(fmt.pct(a.floating_share, 0), "доля плавающей ставки"),
      kpi(fmt.pct(a.effective_rate, 1), "эффективная ставка долга")),
    el("p", { class: "card-foot" },
      isNum(a.lease_liabilities_ifrs16) ? `Обязательства по аренде по МСФО 16 — ${fmt.bn(a.lease_liabilities_ifrs16, 0)}: в модели аренда — расход в EBITDA, в требования она не входит. ` : "",
      list(a.ratings).length ? sentence("Рейтинги: " + a.ratings.map((r) => `${r.agency} ${r.rating}${r.outlook ? `, ${lowerFirst(r.outlook)}` : ""}${r.date ? ` (${fmt.date(r.date)})` : ""}`).join("; ")) : ""));
}

function bridgeCard(d) {
  const an = obj(obj(d.layers).analytical);
  const { lines, rows, total, evRows, equityRows, treasury } = bridgeParts(d);
  if (!isNum(an.v0) || !rows.length) return card({ title: "Мост: стоимость бизнеса → капитал", span: 7 }, missing("мост на дату оценки"));
  // Составляющие EV прибавляются (знак — свой у каждой), требования вычитаются.
  const steps = [];
  let run = 0;
  for (const r of evRows) {
    steps.push({ title: ruText(r.label), from: run, to: run + r.amount, value: -r.amount });
    run += r.amount;
  }
  steps.push({ title: "Стоимость бизнеса V0", from: 0, to: an.v0, total: true, value: an.v0 });
  run = an.v0;
  for (const r of rows) {
    steps.push({ title: ruText(r.label), from: run, to: run - r.amount, value: r.amount });
    run -= r.amount;
  }
  steps.push({ title: "Капитал", from: 0, to: an.equity, total: true, value: an.equity });
  if (equityRows.length) {
    run = an.equity;
    for (const r of equityRows) {
      steps.push({ title: ruText(r.label), from: run, to: run + r.amount, value: -r.amount });
      run += r.amount;
    }
    steps.push({ title: "Капитал с учётом казначейского пакета", from: 0, to: run, total: true, value: run });
  }
  const lo = Math.min(0, ...steps.map((s) => Math.min(s.from, s.to)));
  const hi = Math.max(...steps.map((s) => Math.max(s.from, s.to)));
  const sign = (v) => (v < 0 ? "+" : MINUS);
  const box = el("div", { class: "wf" }, steps.map((s) => el("div", { class: cls("wf-row", s.total && "is-total") },
    el("span", { class: "wf-name" }, s.title),
    el("span", { class: "wf-bar" }, barTrack([{ from: s.from, to: s.to, cls: s.total ? "is-total" : s.to < s.from ? "is-down" : "is-up",
      tip: { title: s.title, rows: [["млрд ₽", s.total ? fmt.num(s.value, 1) : `${sign(s.value)} ${fmt.num(Math.abs(s.value), 1)}`]] } }], [lo, hi])),
    el("span", { class: "wf-val" }, s.total ? fmt.num(s.value, 1) : `${sign(s.value)}${NBSP}${fmt.num(Math.abs(s.value), 1)}`))));
  const meta = obj(d.meta);
  return card({ title: "Мост: стоимость бизнеса → капитал", span: 7,
    sub: `${evRows.length ? "из чего складывается стоимость бизнеса и что из неё вычитается до капитала; " : ""}`
      + `слой «${layerName(d, "analytical")}», млрд ₽ на дату оценки ${fmt.date(meta.valuation_date)}` },
  box,
  el("div", { class: "kpis", style: "margin-top:16px" },
    isNum(total) ? kpi(fmt.bn(total, 1), "требования всего") : null,
    kpi(fmt.pct(meta.governance_discount, 0), "дисконт за управление"),
    kpi(`${fmt.num(meta.shares_mln, 1)} млн`, "акций в обращении"),
    treasury > 0 ? kpi(`${fmt.num(treasury, 1)} млн`, "казначейский пакет: продаётся и входит в число акций") : null,
    kpi(fmt.rub(an.price), "цена слоя на акцию")),
  lines.length ? el("div", { class: "card-foot" }, detailsBlock(`Строки моста из отчётности · ${lines.length}`, dataTable([
    { title: "Строка", value: (r) => r.label, cls: "name" },
    { title: "млрд ₽", num: true, value: (r) => fmt.num(r.amount, 1) },
    { title: "", value: (r) => (r.included ? badge("в требованиях", "model") : badge("не входит", "out")) },
  ], lines, { cls: "compact", detail: (r) => (r.src ? detailsBlock("Источник", ruText(r.src)) : null) }))) : null);
}

function dividendCard(d) {
  const dv = obj(d.dividends);
  const pol = obj(dv.policy);
  const reg = list(dv.register);
  const next = obj(dv.next_expected);
  const tl = list(pol.target_leverage);
  return card({ title: "Дивиденды: политика и реестр", span: 5, sub: pol.text ? sentence(ruText(pol.text)) : "" },
    el("div", { class: "kpis" },
      tl.length === 2 ? kpi(`${fmt.num(tl[0], 1)}–${fmt.num(tl[1], 1)}×`, "целевой чистый долг / EBITDA") : null,
      isNum(pol.no_pay_above) ? kpi(`${fmt.num(pol.no_pay_above, 1)}×`, "выше — выплат нет") : null,
      // «Дважды в год: за …» — коротко в значении, пояснение — в подписи.
      pol.frequency ? kpi(upperFirst(String(pol.frequency).split(":")[0].trim()),
        String(pol.frequency).includes(":") ? `частота: ${String(pol.frequency).split(":").slice(1).join(":").trim()}` : "частота", { text: true }) : null,
      isNum(dv.yield_ltm) ? kpi(fmt.pct(dv.yield_ltm, 1), "доходность за 12 месяцев") : null),
    reg.length ? el("div", { style: "margin-top:14px" }, dataTable([
      { title: "Выплата", value: (r) => el("span", {}, r.label, el("span", { class: "hint" },
        [isNum(r.amount) ? fmt.bn(r.amount, 1) : "", r.pay_until ? `выплата до ${fmt.date(r.pay_until)}` : "", r.in_claims ? "в требованиях" : ""].filter(Boolean).join(" · ")),
        el("span", { class: "cell-badge" }, divStatus(r))), cls: "name" },
      { title: "На акцию", num: true, value: (r) => fmt.rub(r.dps, isNum(r.dps) && r.dps % 1 ? 2 : 0) },
      { title: "Отсечка", num: true, value: registerCutoff },
    ], reg.slice().sort((a, b) => String(b.ex_date || b.record_date).localeCompare(String(a.ex_date || a.record_date))), { cls: "compact" })) : null,
    next.label ? el("p", { class: "card-foot" }, sentence(`Следующая выплата — ${lowerFirst(next.label)}: по модели ${fmt.rub(next.dps_model)} на акцию`
      + `${next.pay_period ? ` (выплата в ${periodName(next.pay_period)})` : ""}, ожидаемая отсечка — ${nextRecordText(next)}`
      + (next.note ? `. ${upperFirst(ruText(next.note))}` : ""))) : null);
}

function dividendHistoryCard(d) {
  const dv = obj(d.dividends);
  // Выплаты — по времени (выпуск их так и отдаёт; порядок страхуется и здесь).
  const ord = (p) => { const m = /^(9M|FY)(\d{4})$/.exec(String(p)); return m ? +m[2] * 10 + (m[1] === "FY" ? 5 : 3) : 0; };
  const hist = list(dv.history).filter((r) => isNum(r.dps)).sort((a, b) => ord(a.period) - ord(b.period));
  const model = list(dv.model).filter((r) => isNum(r.dps) || isNum(r.amount));
  if (!hist.length && !model.length) return card({ title: "Дивиденды по годам" }, missing("история дивидендов"));
  // Подпись столбика: «2024» за год, «9 мес. 2025» — промежуточный.
  const short = (r) => { const m = /^(9M|FY)(\d{4})$/.exec(String(r.period)); return m ? (m[1] === "FY" ? m[2] : `9${NBSP}мес.${NBSP}${m[2]}`) : String(r.label || r.period); };
  const past = columnsChart(hist.map((r) => ({ key: r.period, label: short(r), value: r.dps, tipTitle: upperFirst(ruText(r.label || r.period)) })),
    { height: 210, color: "var(--market)", valueName: "₽ на акцию", fmt: (v) => fmt.rub(v), short: (v) => fmt.num(v), label: "Выплаченные дивиденды на акцию" });
  const future = columnsChart(model.map((r) => ({ key: r.year, label: String(r.year), value: r.dps, tipTitle: `Модель, ${r.year}` })),
    { height: 210, color: "var(--model)", valueName: "₽ на акцию", fmt: (v) => fmt.rub(v), short: (v) => fmt.num(v), label: "Дивиденды модели по годам" });
  const both = el("div", { class: "split" },
    el("div", {}, el("span", { class: "tile-label" }, "Выплачено, ₽ на акцию"), past),
    el("div", {}, el("span", { class: "tile-label" }, "Модель: ожидаемые выплаты, ₽ на акцию"), future));
  const fig = withTable(both, () => el("div", { class: "split" },
    dataTable([
      { title: "Период", value: (r) => upperFirst(ruText(r.label || r.period)), cls: "name" },
      { title: "На акцию", num: true, value: (r) => fmt.rub(r.dps) },
      { title: "Всего", num: true, value: (r) => fmt.bn(r.amount, 1) },
      { title: "Отсечка", num: true, value: (r) => fmt.date(r.record_date) },
    ], hist, { cls: "compact" }),
    dataTable([
      { title: "Год", value: (r) => String(r.year), cls: "name" },
      { title: "На акцию", num: true, value: (r) => fmt.rub(r.dps) },
      { title: "Всего", num: true, value: (r) => fmt.bn(r.amount, 1) },
    ], model, { cls: "compact" })));
  return card({ title: "Дивиденды: история и модель", tools: fig.button,
    sub: `слева — выплаты X5, справа — выплаты модели в слое «${layerName(d, "analytical")}» по правилу целевого рычага` }, fig.box);
}

// Строки годового пути, где есть факт: `paths.fact_marks` — список годов и
// полугодий или объект с ними.
function isFactRow(d, key) {
  const marks = obj(d.paths).fact_marks;
  const k = String(key);
  if (Array.isArray(marks)) return marks.map(String).includes(k);
  const m = obj(marks);
  return [m.annual, m.halves, m.years, m.periods].some((x) => (Array.isArray(x) ? x.map(String).includes(k) : !!obj(x)[k])) || !!m[k];
}

function annualCard(d) {
  const P = obj(d.paths);
  const A = list(P.annual);
  if (!A.length) return card({ title: "По годам" }, missing("путь по годам"));
  const yl = (r) => String(r.year);
  // Строка года якоря: поля `forecast_only` — только прогнозные полугодия (2П).
  const partial = (r, key) => list(r.forecast_only).includes(key);
  const partName = (r) => list(r.forecast_periods).map((p) => { const m = /^\d{4}H([12])$/.exec(String(p)); return m ? `${m[1]}П` : periodName(p); }).join(" + ");
  const cell = (key, f) => (r) => (partial(r, key) ? el("span", {}, f(r[key]),
    el("span", { class: "part-mark", tip: `только ${partName(r)} ${r.year}: факта первого полугодия в этой строке нет` }, partName(r))) : f(r[key]));
  const mini = (title, key, opts, color) => el("div", { class: "mini" },
    el("span", { class: "tile-label" }, title),
    // Годовой поток года якоря без факта 1П на мини-графике не ставится.
    linesChart([{ name: title, color, dots: true, r: 3, points: A.map((r) => [yl(r), partial(r, key) ? null : r[key]]) }],
      { xType: "band", categories: A.map(yl), height: 150, left: 46, xFmt: (k) => `’${k.slice(2)}`, ...opts }));
  const halves = list(P.halves);
  const anyPartial = A.some((r) => list(r.forecast_only).length);
  return card({ title: "Путь бизнеса и долга по годам",
    sub: `слой «${layerName(d, "analytical")}», ожидание по вероятностям клеток; млрд ₽` },
  el("div", { class: "minis" },
    mini("Скорр. маржа EBITDA", "margin", { yPct: true, tipFmt: (v) => fmt.pct(v, 2) }, "var(--model)"),
    mini("Свободный поток FCFF, млрд ₽", "fcff", { yFmt: (v) => fmt.num(v, 0), tipFmt: (v) => fmt.num(v, 1) }, "var(--third)"),
    mini("Чистый долг / EBITDA", "leverage", { yFmt: (v, digits) => fmt.x(v, Math.max(1, digits || 0)), tipFmt: (v) => fmt.x(v, 2) }, "var(--market)")),
  el("div", { style: "margin-top:18px" }, dataTable([
    { title: "Год", value: (r) => el("span", {}, yl(r), list(r.fact).length ? el("span", { class: "hint nowrap" }, "факт 1П + прогноз") : null), cls: "name" },
    { title: "Выручка", num: true, value: (r) => fmt.num(r.revenue, 0) },
    { title: "Рост", num: true, value: (r) => fmt.pct(r.revenue_growth, 1) },
    { title: "Чек без НДС", num: true, value: cell("ticket", (v) => fmt.pct(v, 1)) },
    { title: "Трафик зрелой сети", num: true, value: cell("traffic", (v) => fmt.pct(v, 1)) },
    { title: "Площадь, тыс. м²", num: true, value: (r) => fmt.num(r.area_end, 0) },
    { title: "Маржа", num: true, value: (r) => fmt.pct(r.margin, 2) },
    { title: "Скорр. EBITDA", num: true, value: (r) => fmt.num(r.adj_ebitda, 1) },
    { title: "Capex", num: true, value: (r) => fmt.num(r.capex, 1) },
    { title: "Capex / выручка", num: true, value: (r) => fmt.pct(r.capex_pct, 2) },
    { title: "Налог", num: true, value: cell("tax_unlevered", (v) => fmt.num(v, 1)) },
    { title: "FCFF", num: true, value: cell("fcff", (v) => el("strong", {}, fmt.num(v, 1))) },
    { title: "Проценты", num: true, value: cell("interest", (v) => fmt.num(v, 1)) },
    { title: "Дивиденды", num: true, value: cell("dividends", (v) => fmt.num(v, 1)) },
    { title: "Чистый долг", num: true, value: (r) => fmt.num(r.net_debt, 1) },
    { title: "ЧД / EBITDA", num: true, value: (r) => fmt.x(r.leverage, 2) },
  ], A)),
  el("p", { class: "card-foot" },
    "Чек и трафик — LFL зрелой сети по книге: созревание новых магазинов учтено в площади, поэтому отчётный LFL X5 выше; "
    + "чек — выручка без НДС, с поправкой книги на НДС 22 %.",
    anyPartial ? " В году отчётного якоря выручка, EBITDA, D&A и capex — факт 1П и прогноз, потоки с пометкой «2П» — только прогноз второго полугодия; дивиденды, объявленные до якоря, учтены в чистом долге." : ""),
  halves.length ? el("div", { class: "card-foot" }, detailsBlock(`По полугодиям · ${halves.length}`, dataTable([
    { title: "Полугодие", value: (r) => el("span", {}, periodName(r.period), isFactRow(d, r.period) ? el("span", { class: "hint" }, "факт") : null), cls: "name" },
    { title: "Выручка", num: true, value: (r) => fmt.num(r.revenue, 0) },
    { title: "Маржа", num: true, value: (r) => fmt.pct(r.margin, 2) },
    { title: "Скорр. EBITDA", num: true, value: (r) => fmt.num(r.adj_ebitda, 1) },
    { title: "Capex", num: true, value: (r) => fmt.num(r.capex, 1) },
    { title: "FCFF", num: true, value: (r) => fmt.num(r.fcff, 1) },
    { title: "Чистый долг", num: true, value: (r) => fmt.num(r.net_debt, 1) },
  ], halves, { cls: "compact" }))) : null);
}

// Квартал словами: «2027Q1» → «1 кв. 2027», на оси — «1к27».
function quarterName(p) {
  const m = /^(\d{4})Q([1-4])$/.exec(String(p));
  return m ? `${m[2]}${NBSP}кв.${NBSP}${m[1]}` : periodName(p);
}
function quarterShort(p) {
  const m = /^(\d{4})Q([1-4])$/.exec(String(p));
  return m ? `${m[2]}к${m[1].slice(2)}` : periodShort(p);
}

function wallCard(d) {
  const debt = obj(d.debt);
  const wall = list(debt.wall).filter((w) => isNum(w.bonds) || isNum(w.banks));
  if (!wall.length) return card({ title: "Стена погашений", span: 7 }, missing("стена погашений"));
  const a = obj(debt.anchor);
  const items = wall.map((w) => ({ key: w.period, label: quarterShort(w.period), tipTitle: `Погашения и оферты: ${quarterName(w.period)}`,
    parts: [{ value: w.bonds, color: "var(--model)", name: "облигации" }, { value: w.banks, color: "var(--axis)", name: "банки" }] }));
  const plot = columnsChart(items, { height: 230, fmt: (v) => fmt.bn(v, 1), short: (v) => fmt.num(v, 0),
    refs: isNum(a.cash) ? [{ value: a.cash, text: `касса ${fmt.num(a.cash, 0)} на ${fmt.date(a.as_of)}`, color: "var(--market)", dash: "5 4" }] : [],
    label: "Погашения и оферты по периодам, млрд ₽" });
  const fig = withTable(plot, () => dataTable([
    { title: "Период", value: (w) => quarterName(w.period), cls: "name" },
    { title: "Облигации", num: true, value: (w) => fmt.num(w.bonds, 1) },
    { title: "Банки", num: true, value: (w) => fmt.num(w.banks, 1) },
  ], wall));
  return card({ title: "Стена погашений и оферт", span: 7, tools: fig.button, sub: "млрд ₽; облигации — по датам оферт и погашений" },
    legend([["key-model", "облигации"], ["key-axis", "банковские кредиты"], ["key-line key-market", "касса"]]), fig.box);
}

function loansCard(d) {
  const debt = obj(d.debt);
  const a = obj(debt.anchor);
  const bl = obj(debt.bank_loans);
  // Облигации — на ту же дату, что банки и лизинг (`outstanding_anchor`); выпуски
  // с нулевым остатком в счёт не идут.
  const all = list(debt.bonds);
  const onAnchor = all.some((b) => b && Object.prototype.hasOwnProperty.call(b, "outstanding_anchor"));
  const amount = (b) => (onAnchor ? b.outstanding_anchor : b.outstanding);
  const bonds = all.filter((b) => isNum(amount(b)) && amount(b) > 0);
  const bondSum = bonds.reduce((s, b) => s + amount(b), 0);
  return card({ title: "Из чего долг", span: 5, sub: `на ${fmt.date(a.as_of)}, млрд ₽` },
    el("div", { class: "kpis" },
      bonds.length ? kpi(fmt.num(bondSum, 1), `облигации в обращении · ${fmt.num(bonds.length)} ${plural(bonds.length, ["выпуск", "выпуска", "выпусков"])}`) : null,
      isNum(bl.total) ? kpi(fmt.num(bl.total, 1), "банковские кредиты") : null,
      isNum(bl.short) ? kpi(fmt.num(bl.short, 1), "из них краткосрочные") : null,
      isNum(bl.long) ? kpi(fmt.num(bl.long, 1), "из них долгосрочные") : null,
      isNum(a.leasing) ? kpi(fmt.num(a.leasing, 1), "лизинг") : null),
    el("p", { class: "card-foot" }, `Доля плавающей ставки — ${fmt.pct(a.floating_share, 0)}, эффективная ставка — ${fmt.pct(a.effective_rate, 1)}; `
      + `невыбранные линии — ${fmt.bn(a.credit_lines_unused, 0)}.`));
}

function bondsCard(d) {
  // В обращении сейчас: погашенный выпуск (остаток 0) в таблицу не идёт.
  const bonds = list(obj(d.debt).bonds).filter((b) => !(isNum(b.outstanding) && b.outstanding <= 0));
  if (!bonds.length) return card({ title: "Облигации" }, empty("Облигаций в выпуске нет."));
  const coupon = (b) => (b.coupon_type === "floating"
    ? `ключевая + ${fmt.num((b.spread || 0) * 100, 2)}${NBSP}п.п.`
    : fmt.pct(b.coupon, 2));
  return card({ title: "Облигации X5", sub: `${fmt.num(bonds.length)} ${plural(bonds.length, ["выпуск", "выпуска", "выпусков"])}; остаток — за вычетом выкупленного по офертам; цены и доходности — на дату снимка` },
    dataTable([
      { title: "Выпуск", value: (b) => el("span", {}, b.name || b.isin, b.name ? el("span", { class: "hint" }, b.isin) : null), cls: "name" },
      { title: "В обращении, млрд ₽", num: true, value: (b) => fmt.num(b.outstanding, 1) },
      { title: "Купон", num: true, value: coupon },
      { title: "Оферта", num: true, value: (b) => fmt.date(b.put_date) },
      { title: "Погашение", num: true, value: (b) => fmt.date(b.maturity) },
      { title: "Цена, % ном.", num: true, value: (b) => fmt.num(b.price, 2) },
      { title: "Доходность", num: true, value: (b) => fmt.pct(b.ytm, 2) },
      { title: "Дата", num: true, value: (b) => fmt.date(b.as_of) },
    ], bonds.slice().sort((a, b) => String(a.put_date || a.maturity).localeCompare(String(b.put_date || b.maturity)))));
}

/* ───────────────────────────── экран «Допущения» ───────────────────────────── */

let ALL_JUDGEMENTS = false;

function screenBook(d) {
  const book = obj(d.book);
  const meta = obj(d.meta);
  const rows = judgementRows(d);
  const gates = list(obj(d.checks).gates);
  const fired = gates.filter((g) => g.fired);
  const lede = [
    rows.length ? `${fmt.num(rows.length)} ${plural(rows.length, ["суждение", "суждения", "суждений"])} с диапазоном и ценой ошибки` : "",
    gates.length ? (fired.length ? `сработало ${fmt.num(fired.length)} из ${fmt.num(gates.length)} ${plural(gates.length, ["проверки", "проверок", "проверок"])} правдоподобия — у каждой письменное объяснение`
      : `все ${fmt.num(gates.length)} ${plural(gates.length, ["проверка", "проверки", "проверок"])} правдоподобия в коридорах`) : "",
    meta.facts_date ? `факты — на ${fmt.date(meta.facts_date)}` : "",
  ].filter(Boolean).join("; ");
  const root = el("div", { class: "screen" },
    screenHead("Допущения и проверки", `Книга допущений ${book.version || meta.book_version || ""}`, lede ? sentence(upperFirst(lede)) : ""));
  root.append(el("div", { class: "grid" }, bookMetaCard(d)));
  root.append(el("div", { class: "grid section" }, judgementsCard(d)));
  root.append(el("div", { class: "grid section" }, keyJudgementsCard(d), worldsBookCard(d)));
  root.append(el("div", { class: "grid section" }, gatesCard(d)));
  root.append(el("div", { class: "grid section" }, freshnessCard(d), marketTilesCard(d)));
  root.append(el("div", { class: "grid section" }, curveCard(d)));
  return root;
}

function bookMetaCard(d) {
  const m = obj(d.meta);
  const book = obj(d.book);
  const sections = list(book.sections);
  return card({ title: "Выпуск и книга" },
    el("div", { class: "kpis" },
      kpi(book.version || m.book_version || "—", book.tag ? `версия книги · ${book.tag}` : "версия книги допущений"),
      kpi(fmt.date(book.date || m.book_date), "дата книги"),
      kpi(fmt.date(m.valuation_date), "дата оценки"),
      kpi(fmt.date(book.facts_date || m.facts_date), `отчётные факты · ${periodName(m.anchor_period)}`),
      kpi(fmt.date(m.curve_as_of), "кривая миров"),
      kpi(fmt.stamp(m.generated_at), "выпуск собран", { text: true }),
      kpi(String(m.engine_commit || "—").slice(0, 7), "код модели"),
      kpi(isNum(m.bytes) ? `${fmt.num(m.bytes / 1000, 0)} КБ` : "—", "размер выпуска")),
    el("div", { class: "card-foot" },
      el("p", {}, `Содержание выпуска ${String(m.payload_sha256 || "—").slice(0, 12)}`,
        m.previous_sha256 ? `, прошлый выпуск ${String(m.previous_sha256).slice(0, 12)}` : "",
        `; контракт ${d.schema || "—"}; ${fmt.num(m.shares_mln, 3)} млн акций в обращении; дисконт за управление ${fmt.pct(m.governance_discount, 0)}.`),
      sections.length ? detailsBlock(`Разделы книги · ${sections.length}`, el("ul", { class: "reasons" }, sections.map((s) => el("li", {}, s.title || s.id)))) : null));
}

function judgementsCard(d) {
  const rows = judgementRows(d).slice().sort((a, b) => (b.swing || 0) - (a.swing || 0));
  if (!rows.length) return card({ title: "Суждения книги" }, missing("суждения по цене ошибки"));
  const center = obj(d.fair_value).central;
  const prices = rows.flatMap((r) => [r.price_low, r.price_high]).filter(isNum);
  const stepR = niceStep(Math.max(center, ...prices), 4);
  const dom = [Math.max(0, Math.floor((Math.min(center, ...prices) - stepR * 0.02) / stepR) * stepR),
    Math.ceil((Math.max(center, ...prices) + stepR * 0.02) / stepR) * stepR];
  const body = el("div");
  const draw = () => {
    const shown = ALL_JUDGEMENTS ? rows : rows.slice(0, 12);
    body.replaceChildren(dataTable([
      { title: "Суждение", value: (r) => el("span", {}, r.name,
        r.book && typeof r.book === "object" ? el("span", { class: "hint" }, weightsNames(d, r.book)) : null), cls: "name" },
      { title: "В книге", num: true, value: (r) => formatByUnit(r.book, r.unit, d, true) },
      { title: "Диапазон", num: true, value: (r) => `${formatByUnit(r.low, r.unit, d, true)} … ${formatByUnit(r.high, r.unit, d, true)}` },
      { title: "Точка на краях, ₽", value: (r) => (isNum(r.price_low) && isNum(r.price_high) ? el("div", { class: "tn-cell" },
        barTrack([
          { from: Math.min(r.price_low, r.price_high), to: Math.min(center, Math.max(r.price_low, r.price_high)), cls: "is-down" },
          { from: Math.max(center, Math.min(r.price_low, r.price_high)), to: Math.max(r.price_low, r.price_high), cls: "is-up" },
        ].filter((s) => s.to > s.from), dom, { center }),
        el("span", { class: "tn-nums" }, `${fmt.num(r.price_low)} / ${fmt.num(r.price_high)}`)) : "—") },
      { title: "Цена ошибки", num: true, value: (r) => el("strong", {}, fmt.rub(r.swing)) },
      { title: "Доля полосы", num: true, value: (r) => fmt.pct(r.share, r.share < 0.1 ? 1 : 0) },
    ], shown),
    rows.length > 12 ? el("button", { class: "view-toggle", type: "button", style: "margin-top:12px",
      on: { click: () => { ALL_JUDGEMENTS = !ALL_JUDGEMENTS; draw(); } } }, ALL_JUDGEMENTS ? "Показать главные 12" : `Показать все ${rows.length}`) : null);
  };
  draw();
  return card({ title: "Суждения книги по цене ошибки",
    sub: `Точка при центральных значениях на краях диапазона каждого суждения (по одному, остальные в центре); вертикаль — ${fmt.rub(center)}. Цена ошибки — размах точки; доля полосы — вклад суждения в разброс медианы.` },
  legend([["key-neg", "ниже точки"], ["key-model", "выше точки"]]), body);
}

function keyJudgementsCard(d) {
  const rows = list(obj(d.book).key_judgements);
  if (!rows.length) return card({ title: "Главные суждения", span: 6 }, missing("главные суждения"));
  return card({ title: "Главные суждения книги", span: 6 },
    dataTable([
      { title: "Суждение", value: (r) => r.name, cls: "name" },
      { title: "Значение", num: true, value: (r) => el("strong", {}, formatByUnit(r.value, r.unit, d)) },
    ], rows, { cls: "compact" }));
}

function worldsBookCard(d) {
  const keys = worldKeys(d);
  if (!keys.length) return card({ title: "Миры ставок", span: 6 }, missing("миры ставок"));
  const W_ = obj(d.worlds);
  return card({ title: "Миры ставок: веса по слоям", span: 6, link: ["model", "Кривые и ставки"] },
    dataTable([
      { title: "Мир", value: (k) => worldName(d, k), cls: "name" },
      ...LAYER_ORDER.slice().reverse().map((layer) => ({ title: upperFirst(LAYER_NAMES[layer]), num: true, value: (k) => fmt.pct(obj(W_[k].weights)[layer], 0) })),
      { title: "Инфляция далее", num: true, value: (k) => fmt.pct(W_[k].lt_inflation, 1) },
    ], keys, { cls: "compact" }),
    el("p", { class: "card-foot" }, worldsSourceText(d) || (obj(d.book).worlds_source ? sentence(ruText(d.book.worlds_source)) : "")));
}

function gatesCard(d) {
  const ch = obj(d.checks);
  const inv = list(ch.invariants);
  const broken = inv.filter((i) => !i.ok);
  const gates = list(ch.gates);
  const fired = gates.filter((g) => g.fired);
  const calm = gates.filter((g) => !g.fired);
  const flags = list(ch.flags);
  const massText = (v) => (Array.isArray(v) ? `${fmt.pct(v[0], 1)}–${fmt.pct(v[1], 1)}` : fmt.pct(v, 1));
  return card({ title: "Проверки выпуска",
    sub: "Тождества блокируют сборку; сработавшая проверка правдоподобия требует письменного объяснения со сроком и ожидаемой массой; флаги — предупреждения." },
  el("div", { class: "kpis" },
    kpi(`${fmt.num(inv.length - broken.length)} из ${fmt.num(inv.length)}`, "тождеств выполнено"),
    kpi(`${fmt.num(fired.length)} из ${fmt.num(gates.length)}`, "проверок правдоподобия сработало"),
    kpi(fmt.num(flags.filter((f) => f.raised).length), "флагов поднято")),
  broken.length ? el("div", { class: "note-box", style: "margin-top:14px" }, el("strong", {}, "Нарушены тождества: "),
    broken.map((i) => `${i.name}${i.detail ? ` (${ruText(i.detail)})` : ""}`).join("; ")) : null,
  fired.length ? el("div", { class: "gates" }, fired.map((g) => el("article", { class: "gate" },
    el("div", { class: "gate-head" },
      el("strong", {}, g.title || g.name),
      isNum(g.mass) ? el("span", { class: "gate-mass" }, `${fmt.pct(g.mass, 1)} вероятности`) : null,
      g.explanation ? badge("объяснено", "good") : badge("без объяснения", "bad"),
      g.expected_mass !== undefined && g.expected_mass !== null ? el("span", { class: "gate-mass" }, `ожидалось ${massText(g.expected_mass)}`) : null),
    g.valid_until ? el("p", { class: "muted small", style: "margin-top:4px" }, `Объяснение действует по ${fmt.date(g.valid_until)} включительно.`) : null,
    g.explanation ? el("p", { class: "ink-2", style: "margin-top:6px" }, sentence(upperFirst(ruText(g.explanation)))) : null))) : null,
  calm.length ? el("p", { class: "card-foot" }, sentence(`Не сработали: ${calm.map((g) => lowerFirst(g.title || g.name)).join(", ")}`)) : null,
  flags.length ? el("div", { style: "margin-top:14px" }, dataTable([
    { title: "Флаг", value: (f) => f.title || f.name, cls: "name" },
    { title: "Состояние", value: (f) => (f.raised ? badge("поднят", "warn") : badge("не поднят", "good")) },
  ], flags, { cls: "compact", detail: (f) => (f.detail ? el("p", { class: "muted small" }, sentence(upperFirst(ruText(f.detail)))) : null) })) : null);
}

function freshnessCard(d) {
  const rows = list(obj(d.inputs).rows);
  const live = obj(d.live);
  const price = obj(live.price);
  return card({ title: "Свежесть входов", span: 5, sub: `дата оценки ${fmt.date(live.valuation_date || obj(d.meta).valuation_date)}` },
    rows.length ? dataTable([
      { title: "Вход", value: (r) => el("span", {}, r.name, r.source ? el("span", { class: "hint" }, ruText(r.source)) : null), cls: "name" },
      { title: "Значение", num: true, value: (r) => (typeof r.value === "number" ? formatByUnit(r.value, r.unit, d) : r.unit === "version" ? String(r.value) : ruText(r.value)) },
      { title: "На дату", num: true, value: (r) => fmt.date(r.as_of) },
      { title: "", value: (r) => { const [t, k] = INPUT_STATUS[r.status] || [r.status || "—", null]; return badge(t, k); } },
    ], rows, { cls: "compact" }) : missing("входы"),
    Object.keys(price).length ? el("p", { class: "card-foot" },
      `Цена ${fmt.rub(price.value, 1)} на ${fmt.date(price.date)} — ${price.accepted ? "принята" : "не принята"}`,
      price.reason ? `: ${ruText(price.reason)}` : "", ".") : null);
}

// Значение плитки в её единице: цена — ₽, ставки и доходности — %, спред — б.п.
function tileValue(item, v) {
  if (!isNum(v)) return "—";
  const u = String(item.unit || "");
  if (u === "rub" || u === "₽") return fmt.rub(v, v < 100 ? 2 : 0);
  if (u === "pct" || u === "%" || u === "share") return fmt.pct(v, 2);
  if (u === "bp" || u === "б.п.") return fmt.bp(v);
  return fmt.num(v, 2);
}
function tileChange(item) {
  const c = item.change;
  if (!isNum(c)) return "";
  const u = String(item.unit || "");
  if (u === "rub" || u === "₽") return fmt.signedRub(c, Math.abs(c) < 100 ? 1 : 0);
  if (u === "pct" || u === "%" || u === "share") return fmt.pp(c, 2);
  if (u === "bp" || u === "б.п.") return fmt.signedBp(c);
  return fmt.signed(c, 2);
}

// Дата значения и изменение с периодом: у ставки — дата решения ЦБ, у прочих —
// к какой дате считается изменение.
function tileWhen(i) {
  const ch = tileChange(i);
  if (i.since) return `с ${fmt.date(i.since)}${ch ? ` · изменение ${ch}` : ""}${i.date && i.date !== i.since ? ` · на ${fmt.date(i.date)}` : ""}`;
  return [fmt.date(i.date), ch ? `изменение ${ch}${i.change_from ? ` к ${fmt.date(i.change_from)}` : ""}` : ""].filter(Boolean).join(" · ");
}

function marketTilesCard(d) {
  const tiles = list(obj(d.indicators).tiles);
  if (!tiles.length) return card({ title: "Рынок и ставки", span: 7 }, empty("Плиток в выпуске нет."));
  return card({ title: "Рынок и ставки: наблюдение", span: 7, sub: "живые ряды; в оценку идут миры книги, ряды показывают, куда ушёл рынок" },
    el("div", { class: "ind-tiles" }, tiles.map((i) => el("div", { class: "ind-tile" },
      el("span", { class: "tile-label" }, i.title),
      el("span", { class: "ind-value" }, tileValue(i, i.value)),
      el("span", { class: "muted small" }, tileWhen(i)),
      sparkline(i, (v) => tileValue(i, v))))));
}

function curveCard(d) {
  const c = obj(obj(d.live).curve);
  const nodes = obj(c.nodes), book = obj(c.book_nodes);
  if (!Object.keys(nodes).length && !Object.keys(book).length) return card({ title: "Кривая ОФЗ" }, missing("кривая ОФЗ"));
  const tenor = (k) => (k === "LT" ? 25 : Number(k));
  const pts = (m) => Object.entries(m).map(([t, v]) => [tenor(t), v]).filter((p) => isNum(p[0]) && isNum(p[1])).sort((a, b) => a[0] - b[0]);
  const series = [];
  if (Object.keys(book).length) series.push({ name: `кривая книги ${fmt.date(obj(d.meta).curve_as_of)}`, color: "var(--market)", dots: true, points: pts(book) });
  if (Object.keys(nodes).length) series.push({ name: `наблюдаемая ${fmt.date(c.as_of)}`, color: "var(--ink)", width: 1.6, dots: true, r: 3, points: pts(nodes) });
  const all = [...new Set(series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
  const plot = linesChart(series, { height: 230, xMin: 0, xMax: Math.max(...all) + 1, xMap: Math.sqrt, xTicks: all,
    xFmt: (t) => (t === 25 ? "далее" : `${fmt.num(t)} г.`), yPct: true, tipFmt: (v) => fmt.pct(v, 2),
    label: "Кривая книги и наблюдаемая кривая ОФЗ" });
  const fig = withTable(plot, () => dataTable([
    { title: "Срок", value: (t) => (t === 25 ? "далее" : `${fmt.num(t)} г.`), cls: "name" },
    ...series.map((s) => ({ title: s.name, num: true, value: (t) => { const p = s.points.find((q) => q[0] === t); return p ? fmt.pct(p[1], 2) : "—"; } })),
  ], all));
  const shift = obj(c.shift_bp);
  return card({ title: "Кривая ОФЗ: книга и рынок", tools: fig.button,
    sub: "бескупонная доходность; в оценку идут миры книги, наблюдаемая кривая — сигнал обновить книгу" },
  legend([["key-line key-market", "кривая книги"], ["key-line key-ink", "наблюдаемая"]]),
  fig.box,
  Object.keys(shift).length ? el("div", { class: "kpis", style: "margin-top:12px" },
    Object.entries(shift).map(([t, v]) => kpi(fmt.signedBp(v), `сдвиг ${t}-летнего узла к книге`)),
    isNum(obj(obj(d.live).key_rate).value) ? kpi(fmt.pct(d.live.key_rate.value, 2),
      d.live.key_rate.since ? `ключевая ставка с ${fmt.date(d.live.key_rate.since)}` : `ключевая ставка на ${fmt.date(d.live.key_rate.date)}`) : null) : null);
}

/* ───────────────────────────── пояс плашек ───────────────────────────── */

// Предупреждения живут в одном месте — в поясе перед любым экраном. Условия
// зависят только от выпуска и часов зрителя (возраст выпуска), не от экрана.
function banners(d) {
  const out = [];
  const meta = obj(d.meta);
  const ageHours = (Date.now() - Date.parse(meta.generated_at)) / 3.6e6;
  if (ageHours > STALE_HOURS) {
    out.push(plain("banner-stale", `Выпуску ${fmt.days(Math.floor(ageHours / 24))}: собран ${fmt.stamp(meta.generated_at)}, а конвейер обновляет панель каждый будний день.`));
  }
  const flags = list(obj(d.checks).flags).filter((f) => f.raised);
  for (const f of flags) {
    out.push(plain(f.name === "price_fallback" ? "banner-degraded" : f.name === "dividend_register" ? "banner-event" : "banner-book",
      [el("strong", {}, sentence(f.title || "Флаг выпуска")), f.detail ? " " + sentence(upperFirst(ruText(f.detail))) : ""]));
  }
  const mk = obj(d.market);
  if (mk.price_status === "fallback" && !flags.some((f) => f.name === "price_fallback")) {
    const reason = obj(obj(d.live).price).reason;
    out.push(plain("banner-degraded", [el("strong", {}, "Живая цена не принята."),
      ` В оценке — последняя принятая ${fmt.rub(mk.price, 1)} на ${fmt.date(mk.price_date)}`, reason ? `: ${ruText(reason)}.` : "."]));
  }
  return out.length ? [el("div", { class: "belt" }, out)] : [];
}

function plain(kind, message) {
  return el("div", { class: cls("banner", kind), role: "status" },
    el("span", { class: "banner-icon", "aria-hidden": "true" }, "!"),
    el("div", { class: "banner-body" }, message));
}

/* ───────────────────────────── экраны и переходы ───────────────────────────── */

const SCREENS = {
  overview: screenOverview,
  market: screenMarket,
  model: screenModel,
  report: screenReport,
  debt: screenDebt,
  book: screenBook,
};

function screenFromHash() {
  const name = decodeURIComponent(location.hash.replace(/^#/, ""));
  return Object.prototype.hasOwnProperty.call(SCREENS, name) ? name : "overview";
}

function render(name) {
  const app = $("#app");
  CURRENT = name;
  hideTip();
  for (const tab of document.querySelectorAll(".tab")) {
    const on = tab.dataset.screen === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    // На телефоне полоса вкладок едет: открытая вкладка всегда видна.
    if (on && tab.scrollIntoView) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  let screen;
  try {
    screen = SCREENS[name](DATA);
  } catch (error) {
    // Один сломанный экран не роняет панель: пояс и остальные экраны живут.
    console.error(error);
    screen = el("div", { class: "screen" }, card({ title: "Экран не отрисовался", extra: "broken" },
      el("p", { class: "prose" }, "Ошибка витрины на этом экране; данные выпуска и остальные экраны не затронуты."),
      el("pre", { class: "fatal" }, String((error && error.stack) || error).slice(0, 800))));
  }
  let belt = [];
  try { belt = banners(DATA); } catch (error) { console.error(error); }
  app.replaceChildren(...belt, screen);
  const title = document.querySelector(`.tab[data-screen="${name}"]`);
  document.title = `${title ? title.textContent + " · " : ""}X5 — справедливая стоимость`;
}

function go(name, push = true) {
  if (!Object.prototype.hasOwnProperty.call(SCREENS, name)) name = "overview";
  if (push && location.hash !== `#${name}`) history.pushState(null, "", `#${name}`);
  else if (!push && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  render(name);
  window.scrollTo({ top: 0 });
}

function wireTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  for (const tab of tabs) {
    tab.addEventListener("click", () => { if (DATA) go(tab.dataset.screen); });
    tab.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(tab);
      const next = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length]
        : e.key === "ArrowLeft" ? tabs[(i - 1 + tabs.length) % tabs.length]
          : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : null;
      if (next) { e.preventDefault(); next.focus(); if (DATA) go(next.dataset.screen); }
    });
  }
  // Смена адреса (ссылки «Подробно», «назад» и «вперёд») — hashchange.
  window.addEventListener("hashchange", () => {
    if (!DATA || screenFromHash() === CURRENT) return;
    render(screenFromHash());
    window.scrollTo({ top: 0 });
  });
  // «К содержанию» переводит фокус, а не адрес: иначе #app сбросил бы экран.
  const skip = $(".skip");
  if (skip) skip.addEventListener("click", (e) => { e.preventDefault(); $("#app").focus(); });
}

function wireTheme() {
  const button = $("#theme-toggle");
  if (!button) return;
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    if (window.__theme) window.__theme.remember(next);
    // Цвета графиков — токены CSS, перерисовка не нужна.
  });
}

/* ───────────────────────────── шапка и подвал ───────────────────────────── */

function releaseChip(d) {
  const chip = $("#release-chip");
  if (!chip) return;
  const meta = obj(d.meta);
  const ageHours = (Date.now() - Date.parse(meta.generated_at)) / 3.6e6;
  const flagged = list(obj(d.checks).flags).some((f) => f.raised) || obj(d.market).price_status === "fallback";
  chip.dataset.state = ageHours > STALE_HOURS ? "stale" : flagged ? "warn" : "ok";
  $(".chip-text", chip).replaceChildren(
    el("span", {}, `Выпуск ${fmt.dateShort(mskDay(meta.generated_at))}, ${fmt.time(meta.generated_at)}`),
    el("span", { class: "chip-extra" }, ` · книга ${meta.book_version || ""}`));
  chip.title = ageHours > STALE_HOURS ? "Выпуск старше 96 часов" : flagged ? "Есть предупреждения — см. плашки" : "Выпуск свежий";
}

function colophon(d) {
  const node = $("#colophon-release");
  if (!node) return;
  const meta = obj(d.meta);
  node.textContent = `Модель X5 · книга допущений ${meta.book_version || "—"} · оценка на ${fmt.date(meta.valuation_date)} · `
    + `факты на ${fmt.date(meta.facts_date)} · выпуск ${String(meta.payload_sha256 || "—").slice(0, 12)} · код ${String(meta.engine_commit || "—").slice(0, 7)}`;
}

/* ───────────────────────────── загрузка ───────────────────────────── */

function fatal(title, detail) {
  $("#app").replaceChildren(el("div", { class: "fatal" },
    el("h1", {}, title),
    el("p", {}, "Прежний выпуск остаётся на месте; витрина покажет его, как только ответ придёт."),
    detail ? el("pre", {}, detail) : null));
  const chip = $("#release-chip");
  if (chip) { chip.dataset.state = "stale"; $(".chip-text", chip).textContent = "данные недоступны"; }
}

async function boot() {
  wireTips();
  wireTabs();
  wireTheme();
  let response;
  try {
    response = await fetch(API, { headers: { accept: "application/json" }, cache: "no-cache" });
  } catch (error) {
    fatal("Данные недоступны", `Сеть: ${error.message}`);
    return;
  }
  const body = await response.text();
  if (!response.ok) {
    fatal(response.status === 503 ? "Выпуск ещё не опубликован" : "Данные недоступны", `HTTP ${response.status} · ${body.slice(0, 600)}`);
    return;
  }
  try {
    DATA = JSON.parse(body);
  } catch (error) {
    fatal("Ответ не разобрался как JSON", body.slice(0, 400));
    return;
  }
  if (!DATA || !DATA.meta || !DATA.fair_value) {
    DATA = null;
    fatal("Выпуск без обязательных блоков", "нет meta или fair_value");
    return;
  }
  try { releaseChip(DATA); colophon(DATA); } catch (error) { console.error(error); }
  const name = screenFromHash();
  if (location.hash.replace(/^#/, "") !== name) history.replaceState(null, "", `#${name}`);
  render(name);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
