"""HTTP-клиент сборщиков: повторы, паузы, свой User-Agent, сырой архив до разбора."""

from __future__ import annotations

import email.message
import gzip
import hashlib
import io
import json
import re
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import http, iss  # noqa: E402

URL = "https://iss.moex.com/iss/engines/stock/zcyc.json?iss.meta=off"


class FakeReply:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._body, self.status = body, status
        self.headers = email.message.Message()
        for key, value in (headers or {"Content-Type": "application/json"}).items():
            self.headers[key] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def opener(monkeypatch):
    """Очередь ответов вместо сети; паузы — в список, без сна."""
    queue, seen, sleeps = [], [], []

    def urlopen(request, timeout=None, context=None):
        request.tls_context = context
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(http, "_last_call", {})
    return queue, seen, sleeps


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, f"HTTP {code}", email.message.Message(), io.BytesIO(b""))


def test_user_agent_is_ascii_and_without_personal_data():
    assert http.USER_AGENT.isascii()
    assert "@" not in http.USER_AGENT
    assert "x5-850" in http.USER_AGENT


def test_retry_on_503_then_success_saves_raw_with_meta(opener, tmp_path):
    queue, seen, sleeps = opener
    queue += [http_error(503), FakeReply(b'{"ok": 1}')]
    response = http.fetch(URL, source="iss", name="zcyc.json", root=tmp_path)
    assert response.json() == {"ok": 1}
    assert len(seen) == 2 and sleeps[:1] == [http.RETRY_PAUSES[0]]
    assert seen[0].get_header("User-agent") == http.USER_AGENT
    raw = Path(response.raw_path)
    assert raw.read_bytes() == b'{"ok": 1}'
    assert raw.parent.parent.name == "iss"
    meta = json.loads(raw.with_name(raw.name + ".meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == URL and meta["sha256"] == response.sha256
    assert meta["fetched_at"] == response.fetched_at and meta["bytes"] == 9


def test_client_error_is_not_retried(opener, tmp_path):
    queue, seen, _ = opener
    queue += [http_error(404), FakeReply(b"never")]
    with pytest.raises(http.FetchError) as caught:
        http.fetch(URL, source="iss", name="x.json", root=tmp_path)
    assert caught.value.status == 404 and len(seen) == 1


def test_network_failure_gives_up_after_all_attempts(opener, tmp_path):
    queue, seen, sleeps = opener
    queue += [urllib.error.URLError("down")] * http.DEFAULT_ATTEMPTS
    with pytest.raises(http.FetchError, match="попыток"):
        http.fetch(URL, source="iss", name="x.json", root=tmp_path)
    assert len(seen) == http.DEFAULT_ATTEMPTS
    # Паузы повторов (между ними — пауза вежливости к хосту, ≈1 с).
    assert [s for s in sleeps if s in http.RETRY_PAUSES] == \
        list(http.RETRY_PAUSES[:http.DEFAULT_ATTEMPTS - 1])
    assert not list(tmp_path.rglob("*x.json"))


def test_gzip_body_is_unpacked(opener, tmp_path):
    queue, _, _ = opener
    queue.append(FakeReply(gzip.compress(b'{"z": 2}'), headers={"Content-Encoding": "gzip"}))
    assert http.fetch(URL, source="iss", name="z.json", root=tmp_path).json() == {"z": 2}


def test_raw_is_kept_even_when_parsing_fails(opener, tmp_path, monkeypatch):
    queue, _, _ = opener
    queue.append(FakeReply(b"<html>502 Bad Gateway</html>", headers={"Content-Type": "text/html"}))
    monkeypatch.setenv("X5_RAW_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        iss.fetch_zcyc()
    saved = [p for p in tmp_path.rglob("*zcyc.json")]
    assert len(saved) == 1 and saved[0].read_bytes().startswith(b"<html>")


def test_same_second_saves_do_not_overwrite(tmp_path):
    response = http.Response(URL, 200, b"1", "2026-09-28T16:50:00+00:00")
    first = http.save_raw("iss", "a.json", response, root=tmp_path)
    second = http.save_raw("iss", "a.json", http.Response(URL, 200, b"2", response.fetched_at),
                           root=tmp_path)
    assert first != second
    assert first.read_bytes() == b"1" and second.read_bytes() == b"2"
    assert first.parent.name == "2026-09-28"


def test_strict_json_refuses_nan(tmp_path):
    path = tmp_path / "p.json"
    path.write_text('{"x": NaN}', encoding="utf-8")
    with pytest.raises(ValueError):
        http.read_json_source(path)
    path.write_text('{"x": 1}', encoding="utf-8")
    assert http.read_json_source(str(path)) == {"x": 1}


# Корень Минцифры (indicators/tls/russian_trusted_ca.pem — копия файла Сбера, байт в байт).
TRUSTED_CA_SHA256 = "2a7455f829c30f88b6105b41bba3055f1754748b5165fbba8751163f87f1fc9a"
ROOT_SHA256 = "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31"        # Russian Trusted Root CA
SUB_2024_SHA256 = "21557850 36C900DB B5F1BB2A 1569C80C 55595BD6 BF94867A 29BBDDBC 7D88A3F2".replace(" ", "")


def _pem_prints() -> list[str]:
    text = http.TRUSTED_CA.read_text(encoding="utf-8")
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.S)
    return [hashlib.sha256(ssl.PEM_cert_to_DER_cert(b)).hexdigest().upper() for b in blocks]


def test_trusted_ca_file_is_the_pinned_one():
    assert hashlib.sha256(http.TRUSTED_CA.read_bytes()).hexdigest() == TRUSTED_CA_SHA256
    assert _pem_prints() == [ROOT_SHA256, SUB_2024_SHA256]


def test_mintsifry_root_is_added_only_for_iss():
    assert http.EXTRA_CA == {"iss.moex.com": http.TRUSTED_CA}
    ctx = http.tls_context("https://ISS.moex.com:443/iss/engines/stock/zcyc.json")
    assert isinstance(ctx, ssl.SSLContext)
    # Проверка не ослаблена: сертификат обязателен, имя сверяется.
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    loaded = {hashlib.sha256(der).hexdigest().upper() for der in ctx.get_ca_certs(binary_form=True)}
    assert ROOT_SHA256 in loaded
    for url in ("https://www.cbr.ru/scripts/XML_daily.asp", "https://cbr.ru/x", "https://github.com/ML371KL",
                "https://moex.com/", "https://www.moex.com/", "https://iss.moex.com.evil.example/iss",
                "http://iss.moex.com@evil.example/iss"):
        assert http.tls_context(url) is None, url


def test_fetch_passes_the_context_only_to_iss(opener, tmp_path):
    queue, seen, _ = opener
    queue += [FakeReply(b'{"a": 1}'), FakeReply(b'{"b": 2}')]
    http.fetch(URL, source="iss", name="a.json", root=tmp_path)
    http.fetch("https://www.cbr.ru/x.json", source="cbr", name="b.json", root=tmp_path)
    assert isinstance(seen[0].tls_context, ssl.SSLContext)
    assert seen[1].tls_context is None


@pytest.mark.network
def test_live_iss_handshake_with_the_pinned_file_alone():
    """Контекст только с файлом корня, без хранилища машины: файла хватит серверу."""
    import socket

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)          # пустой набор корней, имя сверяется
    ctx.load_verify_locations(cafile=str(http.TRUSTED_CA))
    with socket.create_connection(("iss.moex.com", 443), timeout=20) as sock:
        with ctx.wrap_socket(sock, server_hostname="iss.moex.com") as tls:
            assert tls.version()
