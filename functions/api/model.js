/**
 * Единственная дверь данных: браузер → выпуск модели X5.
 *
 * Источник — файл `latest.json` орфан-ветки `data` публичного репозитория
 * (его кладёт конвейер). Запрос к GitHub кэшируется краем на ~60 с
 * (`cf.cacheTtl`): витрина не бьёт в raw.githubusercontent.com каждым заходом.
 *
 * Выпуск — объект, у которого `meta` и `fair_value` — объекты (не массивы), а
 * `meta.payload_sha256` — 64 шестнадцатеричных символа (те же ворота, что у
 * витрины, плюс хэш: из него строится ETag, и чужой символ в нём уронил бы
 * ответ). Ворота одни для ответа GitHub, для записи запасной копии и для её
 * чтения: неполный ответ источника — сбой источника, копию он не затирает.
 *
 * Порядок при сбое GitHub (сеть, 5xx, 404, не-JSON, не выпуск, ответа нет
 * дольше UPSTREAM_TIMEOUT_MS — зависшее соединение отменяется, а не ждёт
 * предела платформы):
 *   1) запасная копия из Cache API края — кладётся фоном (waitUntil) при
 *      каждом удачном ответе, с долгим max-age: копия с max-age=60 жила бы
 *      минуту и не спасала бы от сбоя дольше минуты;
 *   2) статическая копия `/fallback/latest.json` из деплоя, если она есть;
 *   3) 503: {"error":"not published yet"} — GitHub ответил 404 (ветки или
 *      файла ещё нет), {"error":"upstream unavailable"} — прочий сбой.
 * Откуда пришёл ответ — заголовок `x-data-source`: github | cache-fallback |
 * bundled. Устаревшая копия видна и по `Last-Modified`.
 *
 * `Last-Modified` — из `meta.generated_at`, `ETag` — `meta.payload_sha256`
 * выпуска (хэш содержания без полей времени): у одинаковых выпусков один ETag,
 * и заход с `If-None-Match` получает 304 без тела. Префикс `W/` (край отдаёт
 * сжатый ответ со слабым ETag) при сравнении срезается.
 *
 * 503, а не 404, когда выпуска нет: 404 — «такого адреса не бывает» (его
 * отдаёт `_middleware.js` для чужих путей под /api/), 503 — «ещё нет».
 */

const SOURCE = "https://raw.githubusercontent.com/ML371KL/temp-zero-inode-850-x5/data/latest.json";
// Ключ запасной копии в Cache API: нужен абсолютный адрес, домен — условный.
const FALLBACK_KEY = "https://tzi-850-x5.internal/fallback/latest.json";
const BUNDLED_PATH = "/fallback/latest.json";
const CACHE_SECONDS = 60;
const FALLBACK_SECONDS = 60 * 60 * 24 * 30;
// Сколько ждать GitHub (заголовки и тело): дольше — сбой, дальше запасные копии.
const UPSTREAM_TIMEOUT_MS = 8000;

export async function onRequest({ request, env, waitUntil }) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return json(405, { error: "method not allowed", method: request.method }, { allow: "GET, HEAD" });
  }

  let notPublished = false;
  let detail = null;
  try {
    const upstream = await fetch(SOURCE, {
      headers: { accept: "application/json", "user-agent": "tzi-850-x5-pages" },
      cf: { cacheTtl: CACHE_SECONDS, cacheEverything: true },
      // Отмена по таймеру попадает в catch — тот же путь, что у сбоя сети;
      // тот же сигнал ограничивает и чтение тела (upstream.text()).
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    if (upstream.ok) {
      const text = await upstream.text();
      const { release, problem } = parse(text);
      if (release) {
        // Запасная копия — фоном: ждать записи в кэш значит добавить её время
        // к каждому ответу.
        const saved = saveFallback(text);
        if (typeof waitUntil === "function") waitUntil(saved);
        return respond(request, text, release, "github");
      }
      detail = `ответ источника — не выпуск: ${problem}`;
    } else if (upstream.status === 404) {
      notPublished = true;
    } else {
      detail = `источник ответил ${upstream.status}`;
    }
  } catch (error) {
    detail = error && error.name === "TimeoutError"
      ? `источник не ответил за ${UPSTREAM_TIMEOUT_MS / 1000} с`
      : String(error).slice(0, 200);
  }

  const cached = await readFallback();
  if (cached) return respond(request, cached.text, cached.release, "cache-fallback");
  const bundled = await readBundled(env, request);
  if (bundled) return respond(request, bundled.text, bundled.release, "bundled");
  if (notPublished) return json(503, { error: "not published yet" });
  return json(503, { error: "upstream unavailable", detail });
}

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const SHA256_HEX = /^[0-9a-f]{64}$/i;

// Почему текст — не выпуск (null — выпуск): страница ошибки, обрезанный ответ,
// файл без обязательных блоков или с негодным хэшем выпуском не считаются.
function releaseProblem(release) {
  if (!isObject(release)) return "не объект";
  if (!isObject(release.meta)) return "нет объекта meta";
  if (!isObject(release.fair_value)) return "нет объекта fair_value";
  if (typeof release.meta.payload_sha256 !== "string" || !SHA256_HEX.test(release.meta.payload_sha256)) {
    return "meta.payload_sha256 — не 64 шестнадцатеричных символа";
  }
  return null;
}

function parse(text) {
  let release;
  try {
    release = JSON.parse(text);
  } catch (error) {
    return { release: null, problem: "не JSON" };
  }
  const problem = releaseProblem(release);
  return problem ? { release: null, problem } : { release, problem: null };
}

function saveFallback(text) {
  if (typeof caches === "undefined") return Promise.resolve();
  const copy = new Response(text, {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${FALLBACK_SECONDS}`,
    },
  });
  return caches.default.put(new Request(FALLBACK_KEY), copy).catch(() => {});
}

async function readFallback() {
  if (typeof caches === "undefined") return null;
  try {
    const hit = await caches.default.match(new Request(FALLBACK_KEY));
    if (!hit) return null;
    const text = await hit.text();
    const { release } = parse(text);
    return release ? { text, release } : null;
  } catch (error) {
    return null;
  }
}

async function readBundled(env, request) {
  if (!env || !env.ASSETS || typeof env.ASSETS.fetch !== "function") return null;
  try {
    const hit = await env.ASSETS.fetch(new Request(new URL(BUNDLED_PATH, request.url)));
    if (!hit.ok) return null;
    const text = await hit.text();
    const { release } = parse(text);
    return release ? { text, release } : null;
  } catch (error) {
    return null;
  }
}

function respond(request, text, release, source) {
  const headers = new Headers({
    "content-type": "application/json; charset=utf-8",
    "cache-control": `public, max-age=${CACHE_SECONDS}`,
    "x-content-type-options": "nosniff",
    "x-data-source": source,
  });
  const generated = Date.parse(release.meta.generated_at);
  if (Number.isFinite(generated)) headers.set("last-modified", new Date(generated).toUTCString());
  // Хэш уже проверен воротами (64 hex-символа): метка всегда годна для заголовка.
  const etag = `"${release.meta.payload_sha256}"`;
  headers.set("etag", etag);

  const inm = request.headers.get("if-none-match");
  if (inm && sameTag(inm, etag)) return new Response(null, { status: 304, headers });
  if (request.method === "HEAD") return new Response(null, { status: 200, headers });
  return new Response(text, { status: 200, headers });
}

// If-None-Match — список меток через запятую или «*»; слабая метка W/"…"
// сравнивается как сильная.
function sameTag(header, etag) {
  const strip = (tag) => tag.trim().replace(/^W\//, "");
  return header.split(",").some((tag) => tag.trim() === "*" || strip(tag) === etag);
}

function json(status, body, extra) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      ...(extra || {}),
    },
  });
}
