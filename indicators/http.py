"""HTTP-клиент сборщиков: только стандартная библиотека.

Правила (перенесены из Магнита 850oa, каждое оплачено):

* **Свой User-Agent, только ASCII.** Заголовки HTTP кодируются latin-1:
  кириллица роняет запрос ещё до отправки. В заголовке — адрес проекта, а не
  личные данные владельца: он уходит каждому источнику.
* **Повтор только осмысленный.** Сетевые сбои, 408/425/429 и 5xx повторяются с
  паузами; прочие 4xx — нет (это наша ошибка, повтор дразнит бан).
* **Пауза между обращениями к одному хосту**, без параллелизма внутри хоста.
* **Сырой ответ ложится на диск ДО разбора** — `var/raw/<источник>/<дата>/`
  с `.meta.json` (адрес, время, sha256). Разбор можно починить задним числом,
  потерянные байты — нет.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

USER_AGENT = ("x5-850/1.0 (fair value research pipeline; "
              "+https://github.com/ML371KL/temp-zero-inode-850-x5)")
DEFAULT_TIMEOUT = 30.0
DEFAULT_ATTEMPTS = 4
# Паузы перед 2-й, 3-й, 4-й попыткой, секунды.
RETRY_PAUSES = (3.0, 10.0, 30.0)
# Пауза между обращениями к одному хосту, секунды.
HOST_THROTTLE = 1.0
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

_last_call: dict[str, float] = {}


class FetchError(RuntimeError):
    """Источник не ответил годным HTTP-ответом."""

    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{url}: {message}")
        self.url, self.status = url, status


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes
    fetched_at: str
    headers: dict[str, str] = field(default_factory=dict)
    raw_path: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


def raw_root() -> Path:
    """Корень сырого архива: `X5_RAW_DIR` или `var/raw` репозитория."""
    return Path(os.environ.get("X5_RAW_DIR") or ROOT / "var" / "raw")


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "response"


def save_raw(source: str, name: str, response: Response, *,
             root: Path | None = None) -> Path:
    """Кладёт тело ответа и `.meta.json` рядом. Имя — `ЧЧММСС_<name>`:
    повтор в тот же день не затирает прежний ответ."""
    fetched = datetime.fromisoformat(response.fetched_at.replace("Z", "+00:00"))
    folder = (root or raw_root()) / _safe_name(source) / fetched.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{fetched:%H%M%S}_{_safe_name(name)}"
    path = folder / base
    n = 1
    while path.exists():
        path = folder / f"{base}.{n}"
        n += 1
    path.write_bytes(response.body)
    meta = {"url": response.url, "fetched_at": response.fetched_at,
            "status": response.status, "sha256": response.sha256,
            "bytes": len(response.body),
            "content_type": response.headers.get("Content-Type")
            or response.headers.get("content-type")}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return path


def _throttle(host: str) -> None:
    last = _last_call.get(host)
    if last is not None:
        wait = HOST_THROTTLE - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def fetch(url: str, *, source: str, name: str, data: bytes | None = None,
          headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT,
          attempts: int = DEFAULT_ATTEMPTS, save: bool = True,
          root: Path | None = None) -> Response:
    """GET (или POST, если передан `data`) с повторами и паузами.

    Ответ 200 сохраняется в сырой архив до возврата — то есть до того, как
    вызывающий попробует его разобрать. Ошибка записи не глотается.
    """
    host = urllib.parse.urlsplit(url).netloc
    request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
                       **(headers or {})}
    response = _get(url, data, request_headers, host, timeout, attempts)
    if save:
        # Вне цикла повторов: сбой записи на диск — не сетевой сбой, его не
        # повторяют и не глотают.
        path = save_raw(source, name, response, root=root)
        response = Response(response.url, response.status, response.body,
                            response.fetched_at, response.headers, str(path))
    return response


def _refuse_constant(token: str):
    raise ValueError(f"{token} — не строгий JSON: витрина такой ответ не разберёт")


def strict_json(text: str):
    """Разбор, как у `JSON.parse` браузера: NaN и ±Infinity — ошибка."""
    return json.loads(text, parse_constant=_refuse_constant)


def read_json_source(source: str | Path) -> dict:
    """JSON из файла или по адресу (прошлый выпуск: `--previous PATH|URL`).
    По адресу — тем же клиентом, со своим User-Agent, без сырого архива."""
    text = str(source)
    if text.startswith(("http://", "https://")):
        response = fetch(text, source="release", name="previous.json", save=False,
                         headers={"Accept": "application/json"})
        return strict_json(response.text)
    return strict_json(Path(text).read_text(encoding="utf-8"))


def _get(url: str, data: bytes | None, headers: dict[str, str], host: str,
         timeout: float, attempts: int) -> Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        _throttle(host)
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                body = reply.read()
                if (reply.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
                return Response(
                    url=url, status=reply.status, body=body,
                    fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    headers={k: v for k, v in reply.headers.items()})
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUS:
                raise FetchError(url, exc.code, f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                EOFError, gzip.BadGzipFile) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(RETRY_PAUSES[min(attempt, len(RETRY_PAUSES)) - 1])
    status = last_error.code if isinstance(last_error, urllib.error.HTTPError) else None
    raise FetchError(url, status, f"не удалось за {attempts} попыток: {last_error}")
