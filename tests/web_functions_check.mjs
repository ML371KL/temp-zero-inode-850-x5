// Проверка функций Pages без Cloudflare: functions/api/model.js и
// functions/_middleware.js грузятся как модули, fetch, Cache API и ASSETS
// подменяются. Запускает tests/test_web_node.py; печатает JSON с итогами,
// код выхода 1 — если хоть одна проверка не прошла.
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const load = (rel) => import("data:text/javascript;base64," + Buffer.from(readFileSync(new URL(rel, root))).toString("base64"));
const model = await load("functions/api/model.js");
const middleware = await load("functions/_middleware.js");

const SOURCE = "https://raw.githubusercontent.com/ML371KL/temp-zero-inode-850-x5/data/latest.json";
// Выпуск для двери — объект с объектами meta и fair_value и хэшем из 64 hex-символов.
const SHA = "51f1e0e0be1c410dbb5ebe6c54dd163f40680c38f6ec40c6a456bb272662fa7d";
const OLD_SHA = "3935d1c2c5668b71522ef131631cfa82097f31c846550cf39c5c2fb6a4a3b641";
const release = { schema: "x5-v1", meta: { generated_at: "2026-09-28T17:05:00Z", payload_sha256: SHA }, fair_value: {} };
const body = JSON.stringify(release);
const older = JSON.stringify({ schema: "x5-v1", meta: { generated_at: "2026-09-20T17:05:00Z", payload_sha256: OLD_SHA }, fair_value: {} });
const FALLBACK_KEY = "https://tzi-850-x5.internal/fallback/latest.json";
const LF = String.fromCharCode(10);

let store = new Map();
globalThis.caches = {
  default: {
    async put(req, res) { store.set(req.url, { text: await res.text(), cc: res.headers.get("cache-control") }); },
    async match(req) { const hit = store.get(req.url); return hit ? new Response(hit.text, { headers: { "cache-control": hit.cc } }) : undefined; },
  },
};
const calls = [];
// kind — вид ответа источника; "text" — 200 с телом `text` (неполный или чужой файл).
function upstream(kind, text) {
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    if (kind === "ok") return new Response(body, { status: 200 });
    if (kind === "text") return new Response(text, { status: 200 });
    if (kind === "404") return new Response("404: Not Found", { status: 404 });
    if (kind === "500") return new Response("oops", { status: 500 });
    if (kind === "html") return new Response("<html>not json</html>", { status: 200 });
    // Соединение открыто, ответа нет: промис не завершается сам, только по
    // отмене сигналом (как настоящий fetch).
    if (kind === "hang") {
      return new Promise((resolve, reject) => {
        const signal = init && init.signal;
        if (!signal) return;
        if (signal.aborted) reject(signal.reason);
        signal.addEventListener("abort", () => reject(signal.reason));
      });
    }
    throw new TypeError("network down");
  };
}
// Таймер двери подменяется коротким: проверка не ждёт 8 с, но видит, какой
// предел дверь просила.
const realTimeout = AbortSignal.timeout.bind(AbortSignal);
let askedTimeout = null;
AbortSignal.timeout = (ms) => { askedTimeout = ms; return realTimeout(50); };
const within = (promise, ms) => Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve("pending"), ms))]);
const assets = (text, status = 200) => ({ fetch: async () => new Response(text, { status }) });
let waits = [];
const call = (method, headers = {}, env = {}) => model.onRequest({
  request: new Request("https://tzi-850-x5.pages.dev/api/model", { method, headers }), env, waitUntil: (p) => waits.push(p) });

const checks = [];
const check = (name, cond, detail) => checks.push({ name, ok: !!cond, detail: cond ? undefined : detail });
const json = async (res) => { try { return await res.json(); } catch { return null; } };

// 1. удачный ответ источника
upstream("ok");
let res = await call("GET");
let text = await res.text();
check("GET 200", res.status === 200, res.status);
check("тело — как у источника", text === body, text.slice(0, 80));
check("x-data-source github", res.headers.get("x-data-source") === "github", res.headers.get("x-data-source"));
check("ETag = payload_sha256", res.headers.get("etag") === `"${SHA}"`, res.headers.get("etag"));
check("Last-Modified из generated_at", res.headers.get("last-modified") === "Mon, 28 Sep 2026 17:05:00 GMT", res.headers.get("last-modified"));
check("кэш клиенту 60 с", res.headers.get("cache-control") === "public, max-age=60", res.headers.get("cache-control"));
check("content-type JSON", /application\/json/.test(res.headers.get("content-type")), res.headers.get("content-type"));
check("источник — ветка data публичного репозитория", calls[0] && calls[0].url === SOURCE, calls[0] && calls[0].url);
check("край кэширует источник ~60 с", calls[0] && calls[0].init.cf && calls[0].init.cf.cacheTtl === 60, calls[0] && calls[0].init.cf);
await Promise.all(waits); waits = [];
const saved = store.get(FALLBACK_KEY);
check("запасная копия положена фоном", saved && saved.text === body, saved);
check("у запасной копии долгий max-age", saved && /max-age=(\d+)/.test(saved.cc) && +saved.cc.match(/max-age=(\d+)/)[1] >= 86400, saved && saved.cc);

// 2. HEAD, 304, чужой ETag, 405
res = await call("HEAD");
check("HEAD 200 без тела", res.status === 200 && (await res.text()) === "", res.status);
res = await call("GET", { "if-none-match": `W/"${SHA}"` });
check("304 по слабому ETag", res.status === 304, res.status);
res = await call("GET", { "if-none-match": `"zzz", "${SHA}"` });
check("304 по списку меток", res.status === 304, res.status);
res = await call("GET", { "if-none-match": '"other"' });
check("чужой ETag — 200", res.status === 200, res.status);
res = await call("POST");
check("POST 405", res.status === 405 && res.headers.get("allow") === "GET, HEAD", res.status);
await Promise.all(waits); waits = [];

// 3. сбой сети: запасная копия из кэша
upstream("down");
res = await call("GET");
check("сбой сети — копия из кэша", res.status === 200 && res.headers.get("x-data-source") === "cache-fallback", [res.status, res.headers.get("x-data-source")]);
check("копия клиенту — на 60 с", res.headers.get("cache-control") === "public, max-age=60", res.headers.get("cache-control"));

// 3а. источник завис: отмена по таймеру → запасная копия, а не ожидание предела платформы
upstream("hang");
calls.length = 0;
res = await within(call("GET"), 2000);
check("источник завис — копия из кэша быстрее таймаута проверки", res !== "pending" && res.status === 200
  && res.headers.get("x-data-source") === "cache-fallback", res === "pending" ? "ответа нет за 2 с" : [res.status, res.headers.get("x-data-source")]);
check("запрос к источнику идёт с сигналом отмены", calls[0] && calls[0].init && calls[0].init.signal instanceof AbortSignal, calls[0] && calls[0].init);
check("предел ожидания источника — 5–8 с", askedTimeout >= 5000 && askedTimeout <= 8000, askedTimeout);
upstream("down");

// 4. кэша нет: статическая копия деплоя, затем 503
store = new Map();
res = await call("GET", {}, { ASSETS: assets(older) });
check("сбой сети — статическая копия", res.status === 200 && res.headers.get("x-data-source") === "bundled", [res.status, res.headers.get("x-data-source")]);
check("у копии свой Last-Modified", res.headers.get("last-modified") === "Sun, 20 Sep 2026 17:05:00 GMT", res.headers.get("last-modified"));
res = await call("GET", {}, { ASSETS: assets("<html>404</html>", 404) });
let out = await json(res);
check("сбой сети без копий — 503 upstream unavailable", res.status === 503 && out && out.error === "upstream unavailable", [res.status, out]);
check("503 не кэшируется", res.headers.get("cache-control") === "no-store", res.headers.get("cache-control"));
upstream("hang");
res = await within(call("GET", {}, { ASSETS: assets("<html>404</html>", 404) }), 2000);
out = res === "pending" ? null : await json(res);
check("источник завис, копий нет — 503 upstream unavailable с причиной", res !== "pending" && res.status === 503
  && out && out.error === "upstream unavailable" && /не ответил/.test(out.detail || ""), res === "pending" ? "ответа нет за 2 с" : [res.status, out]);

upstream("404");
res = await call("GET");
out = await json(res);
check("файла нет — 503 not published yet", res.status === 503 && out && out.error === "not published yet", [res.status, out]);
res = await call("GET", {}, { ASSETS: assets(older) });
check("файла нет, но есть копия деплоя — копия", res.status === 200 && res.headers.get("x-data-source") === "bundled", res.status);

upstream("500");
res = await call("GET");
out = await json(res);
check("5xx источника — 503 upstream unavailable", res.status === 503 && out && out.error === "upstream unavailable", [res.status, out]);

upstream("html");
res = await call("GET");
out = await json(res);
check("не-JSON от источника — не выпуск", res.status === 503 && out && out.error === "upstream unavailable", [res.status, out]);

// 4а. неполный ответ источника — сбой источника: запасная копия не затирается и отдаётся
const safe = async (promise) => { try { return await promise; } catch (error) { return { error: String(error) }; } };
const without = (key) => { const r = JSON.parse(body); delete r[key]; return JSON.stringify(r); };
const withSha = (sha) => JSON.stringify({ ...release, meta: { ...release.meta, payload_sha256: sha } });
const partial = [
  ["{\"meta\":{}}", '{"meta":{}}'],
  ["{\"meta\":[]}", '{"meta":[]}'],
  ["без fair_value", without("fair_value")],
  ["fair_value — массив", JSON.stringify({ ...release, fair_value: [] })],
  ["meta — массив при fair_value", JSON.stringify({ ...release, meta: [] })],
  ["без payload_sha256", JSON.stringify({ ...release, meta: { generated_at: release.meta.generated_at } })],
  ["короткий хэш", withSha("abc123")],
  ["хэш с кириллицей", withSha("аб" + SHA.slice(2))],
  ["хэш с переводом строки", withSha(SHA.slice(0, 32) + LF + SHA.slice(33))],
  ["хэш-число", JSON.stringify({ ...release, meta: { ...release.meta, payload_sha256: 12345 } })],
];
store = new Map();
upstream("ok");
await call("GET");
await Promise.all(waits); waits = [];
for (const [name, text] of partial) {
  upstream("text", text);
  for (const attempt of [1, 2]) {
    res = await safe(call("GET"));
    const got = res.error ? null : await res.text();
    check(`неполный ответ (${name}), запрос ${attempt}: отдана прежняя копия`, !res.error && res.status === 200
      && res.headers.get("x-data-source") === "cache-fallback" && got === body && res.headers.get("etag") === `"${SHA}"`,
    res.error || [res.status, res.headers.get("x-data-source"), got && got.slice(0, 60)]);
    await Promise.all(waits); waits = [];
    check(`неполный ответ (${name}), запрос ${attempt}: копия не затёрта`, store.get(FALLBACK_KEY) && store.get(FALLBACK_KEY).text === body,
      store.get(FALLBACK_KEY) && store.get(FALLBACK_KEY).text.slice(0, 60));
  }
}
upstream("ok");
res = await call("GET");
check("после неполного ответа исправный снова идёт от источника", res.status === 200 && res.headers.get("x-data-source") === "github", res.status);
await Promise.all(waits); waits = [];

// Копий нет — 503 с причиной; негодная копия (в кэше или в деплое) не отдаётся.
store = new Map();
upstream("text", '{"meta":{}}');
res = await call("GET");
out = await json(res);
check("неполный ответ без копий — 503 upstream unavailable с причиной", res.status === 503 && out && out.error === "upstream unavailable"
  && /не выпуск: нет объекта fair_value/.test(out.detail || ""), [res.status, out]);
check("неполный ответ без копий — копия не положена", !store.has(FALLBACK_KEY), store.get(FALLBACK_KEY));
upstream("text", withSha("аб" + SHA.slice(2)));
res = await safe(call("GET"));
out = res.error ? null : await json(res);
check("хэш с кириллицей без копий — 503, а не исключение", !res.error && res.status === 503 && out
  && /payload_sha256/.test(out.detail || ""), res.error || [res.status, out]);
upstream("down");
store.set(FALLBACK_KEY, { text: '{"meta":{}}', cc: "public, max-age=2592000" });
res = await call("GET");
out = await json(res);
check("негодная копия в кэше не отдаётся — 503", res.status === 503 && out && out.error === "upstream unavailable", [res.status, out]);
res = await call("GET", {}, { ASSETS: assets(older) });
check("негодная копия в кэше — дальше статическая копия деплоя", res.status === 200 && res.headers.get("x-data-source") === "bundled"
  && res.headers.get("etag") === `"${OLD_SHA}"`, [res.status, res.headers.get("x-data-source")]);
store.set(FALLBACK_KEY, { text: withSha("ab" + LF + "cd"), cc: "public, max-age=2592000" });
res = await safe(call("GET", {}, { ASSETS: assets(JSON.stringify({ meta: [] })) }));
out = res.error ? null : await json(res);
check("негодные копии в кэше и деплое — 503, а не исключение", !res.error && res.status === 503 && out && out.error === "upstream unavailable",
  res.error || [res.status, out]);
store = new Map();

// 5. граница /api/ и заголовки безопасности
const mw = async (path) => {
  let nextCalled = false;
  const r = await middleware.onRequest({ request: new Request("https://tzi-850-x5.pages.dev" + path),
    next: async () => { nextCalled = true; return new Response("ok", { headers: { "content-type": "text/plain" } }); } });
  return { r, nextCalled };
};
for (const bad of ["/api/nope", "/api//model", "/api/model/", "/api"]) {
  const { r, nextCalled } = await mw(bad);
  const o = await json(r);
  check(`${bad} — JSON-404`, r.status === 404 && o && o.error === "not found" && !nextCalled, [r.status, o]);
}
let m = await mw("/api/model");
check("/api/model проходит к функции", m.nextCalled && m.r.status === 200, m.r.status);
check("CSP на ответе функции", /script-src 'self' 'sha256-/.test(m.r.headers.get("content-security-policy") || ""), m.r.headers.get("content-security-policy"));
check("nosniff на ответе функции", m.r.headers.get("x-content-type-options") === "nosniff", null);
m = await mw("/api/nope");
check("CSP и на 404", !!m.r.headers.get("content-security-policy"), null);

const failed = checks.filter((c) => !c.ok);
console.log(JSON.stringify({ total: checks.length, failed }, null, 1));
process.exitCode = failed.length ? 1 : 0;
