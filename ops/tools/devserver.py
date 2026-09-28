"""Локальный предпросмотр витрины без Cloudflare.

Отдаёт `web/` как статику, а на `/api/model` — файл выпуска: по умолчанию
`var/release/sample.json` (его пишет ядро), если его нет — мок
`tests/fixtures/sample_payload.json`. Нужен только для проверки вёрстки: в
проде `/api/model` обслуживает функция Pages (`functions/api/model.js`).

CSP берётся ИЗ `functions/_middleware.js`, а не пишется здесь второй раз:
устаревший хэш скрипта темы должен ломать тему и локально, а не только в проде.

    python ops/tools/devserver.py [порт] [путь-к-выпуску]
"""

from __future__ import annotations

import json
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
MIDDLEWARE = ROOT / "functions" / "_middleware.js"
CANDIDATES = [ROOT / "var" / "release" / "sample.json", ROOT / "tests" / "fixtures" / "sample_payload.json"]


def read_csp() -> str:
    """Та же строка CSP, что соберёт функция в проде."""
    src = MIDDLEWARE.read_text(encoding="utf-8")
    hash_ = re.search(r'THEME_SCRIPT_HASH = "([^"]+)"', src).group(1)
    body = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S).group(1)
    parts = [m.group(1) for m in re.finditer(r"[`\"]([^`\"]+)[`\"]", body)]
    return "; ".join(p.replace("${THEME_SCRIPT_HASH}", hash_) for p in parts)


CSP = read_csp()


class Handler(SimpleHTTPRequestHandler):
    release: Path = CANDIDATES[-1]

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/model":
            if not self.release.exists():
                return self._json(503, {"error": "not published yet"})
            return self._raw(200, self.release.read_bytes())
        if path.startswith("/api"):
            return self._json(404, {"error": "not found", "requested": path[5:69]})
        target = WEB / path.lstrip("/")
        if path != "/" and not target.exists():
            return self._raw(404, (WEB / "404.html").read_bytes(), "text/html; charset=utf-8")
        return super().do_GET()

    def _json(self, status, body):
        self._raw(status, json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _raw(self, status, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):  # noqa: N802
        # Предпросмотр ничего не кэширует: иначе правка стилей не доезжает до
        # браузера, и проверка вёрстки идёт по вчерашнему файлу.
        self.send_header("cache-control", "no-store, must-revalidate")
        self.send_header("content-security-policy", CSP)
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        super().end_headers()

    def log_message(self, fmt, *args):  # noqa: A002
        pass


def main(argv: list[str]) -> None:
    port = int(argv[1]) if len(argv) > 1 else 8872
    if len(argv) > 2:
        Handler.release = Path(argv[2]).resolve()
    else:
        Handler.release = next((p for p in CANDIDATES if p.exists()), CANDIDATES[-1])
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(WEB)))
    print(f"витрина: http://127.0.0.1:{port}/  выпуск: {Handler.release}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv)
