"""Витрина: статика Pages, CSP, тема, бюджет веса.

Правила витрины X5 (образец — витрина Магнита 850oa):
* одна CSP в двух местах — `web/_headers` (статика) и `functions/_middleware.js`
  (ответы функций), посимвольно одинаковая;
* инлайн-скрипт темы разрешён по sha256 его текста с переводами строк LF;
* вся витрина (`web/`) — меньше 300 000 байт;
* функции вызываются только для `/api/*`; настоящая 404;
* со сторонних адресов не грузится ничего; управляющих байтов в исходниках нет.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
MIDDLEWARE = ROOT / "functions" / "_middleware.js"
FRONT_BUDGET = 300_000


def _theme_script() -> str:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert match, "в index.html нет инлайн-скрипта темы (<script> без атрибутов)"
    return match.group(1).replace("\r\n", "\n")


def _theme_hash() -> str:
    digest = hashlib.sha256(_theme_script().encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def _headers_csp() -> str:
    for line in (WEB / "_headers").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("Content-Security-Policy:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("в web/_headers нет строки Content-Security-Policy")


def _middleware_csp() -> str:
    src = MIDDLEWARE.read_text(encoding="utf-8")
    hash_ = re.search(r'THEME_SCRIPT_HASH = "([^"]+)"', src).group(1)
    body = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S).group(1)
    parts = [m.group(1) for m in re.finditer(r"[`\"]([^`\"]+)[`\"]", body)]
    return "; ".join(p.replace("${THEME_SCRIPT_HASH}", hash_) for p in parts)


def test_csp_is_the_same_in_headers_and_middleware():
    assert _headers_csp() == _middleware_csp(), (
        f"политики разошлись:\n  _headers:   {_headers_csp()}\n  middleware: {_middleware_csp()}")


def test_theme_script_hash_matches_csp():
    want = _theme_hash()
    src = MIDDLEWARE.read_text(encoding="utf-8")
    have = re.search(r'THEME_SCRIPT_HASH = "([^"]+)"', src).group(1)
    lines = (f'\nfunctions/_middleware.js: const THEME_SCRIPT_HASH = "{want}";'
             f"\nweb/_headers: ... script-src 'self' '{want}'; ...")
    assert have == want, "хэш скрипта темы устарел; строки на замену:" + lines
    assert f"'{want}'" in _headers_csp(), "хэш скрипта темы в web/_headers устарел; строки на замену:" + lines


def test_csp_forbids_third_party_and_inline_scripts():
    csp = _headers_csp()
    assert "default-src 'self'" in csp
    assert re.search(r"script-src 'self' 'sha256-[A-Za-z0-9+/=]+'(;|$)", csp), "скрипты — только свои и скрипт темы по хэшу"
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp


def test_theme_rule_lives_in_one_place():
    script = _theme_script()
    assert '"x5-850-theme"' in script and '"x5-850-theme-tonight"' in script, "ключи хранилища темы — x5-850-*"
    assert "FROM = 20" in script and "TO = 7" in script, "тёмная тема с 20:00 до 07:00 по часам зрителя"
    assert "getHours" not in (WEB / "app.js").read_text(encoding="utf-8"), "часы зрителя читает только скрипт темы"


def test_index_has_the_theme_script_and_the_app_only():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.count("<script") == 2, "в index.html ровно два скрипта: тема (инлайн) и /app.js"
    assert '<script src="/app.js" defer></script>' in html
    head = html.split("</head>")[0]
    assert head.index("<script>") < head.index('rel="stylesheet"'), "тема ставится до загрузки стилей"


def test_front_weight_within_budget():
    # web/fallback/ — статическая копия выпуска для двери данных (если её
    # кладут при выкладке): это данные, а не витрина, у них свой потолок.
    total = sum(p.stat().st_size for p in WEB.rglob("*") if p.is_file() and "fallback" not in p.relative_to(WEB).parts)
    assert total < FRONT_BUDGET, f"витрина весит {total} байт при потолке {FRONT_BUDGET}"


def test_routes_json_limits_functions_to_api():
    routes = json.loads((WEB / "_routes.json").read_text(encoding="utf-8"))
    assert routes == {"version": 1, "include": ["/api/*"], "exclude": []}


def test_404_is_a_real_page():
    page = (WEB / "404.html").read_text(encoding="utf-8")
    assert "404" in page and 'href="/"' in page and "/styles.css" in page


def test_middleware_allows_only_the_model_under_api():
    src = MIDDLEWARE.read_text(encoding="utf-8")
    assert 'const ALLOWED_API = new Set(["model"]);' in src


def test_front_sources_carry_no_control_characters():
    # Порча при записи файла инструментом («\b» в регулярном выражении
    # становится байтом 0x08) видна здесь, а не в браузере.
    for path in [*WEB.iterdir(), *(ROOT / "functions").rglob("*.js")]:
        if path.suffix not in {".js", ".css", ".html", ".svg", ".json", ""}:
            continue
        data = path.read_bytes()
        bad = [b for b in data if b < 0x20 and b not in (0x0A, 0x0D, 0x09)]
        assert not bad, f"{path.name}: управляющие байты {sorted(set(bad))}"
        assert b"\r\n" not in data, f"{path.name}: переводы строк CRLF (хэш темы считается по LF)"


def test_nothing_is_loaded_from_third_party_addresses():
    allowed = {"http://www.w3.org/2000/svg"}
    for name in ("index.html", "404.html", "app.js", "styles.css", "favicon.svg"):
        text = (WEB / name).read_text(encoding="utf-8")
        found = set(re.findall(r"https?://[^\s\"'`)<>]+", text)) - allowed
        assert not found, f"{name}: сторонние адреса {sorted(found)}"
        assert "@import" not in text and "fonts.googleapis" not in text


def test_screens_have_no_disclaimers():
    # Предпочтение владельца: на витрине никаких дисклеймеров и оговорок.
    for name in ("index.html", "app.js"):
        text = (WEB / name).read_text(encoding="utf-8").lower()
        for phrase in ("инвестиционной рекомендаци", "инвестиционная рекомендация", "не является офертой", "дисклеймер"):
            assert phrase not in text, f"{name}: «{phrase}»"


def test_there_are_six_screens_with_direct_links():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    screens = re.findall(r'data-screen="([a-z]+)"', html)
    assert screens == ["overview", "market", "model", "report", "debt", "book"]
    block = re.search(r"const SCREENS = \{(.*?)\};", app, re.S).group(1)
    assert re.findall(r"^\s*([a-z]+):", block, re.M) == screens
