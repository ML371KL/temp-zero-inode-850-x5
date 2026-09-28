"""Ключевая ставка ЦБ: SOAP KeyRate и запасная страница на реальных ответах
(28.09.2026, `tests/fixtures/cbr/`)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import cbr  # noqa: E402
from indicators.http import FetchError, Response  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "cbr"
TODAY = date(2026, 9, 28)
SOAP = (FIX / "key_rate_soap.xml").read_bytes()
PAGE = (FIX / "key_rate_page.html").read_bytes()


def getter_with(soap=None, page=None, calls=None):
    def getter(url, *, source, name, **kw):
        if calls is not None:
            calls.append({"url": url, "name": name, **kw})
        value = soap if name == "key_rate_soap.xml" else page
        if isinstance(value, Exception):
            raise value
        return Response(url, 200, value, "2026-09-28T10:00:00+00:00")
    return getter


def test_soap_parsed_and_checked():
    rows = cbr.parse_soap(SOAP)
    assert len(rows) == 254
    assert rows[0] == ("2025-09-29", 0.17)
    assert rows[-1] == ("2026-09-28", 0.14)
    assert [d for d, _ in rows] == sorted(d for d, _ in rows)


def test_page_gives_the_same_series_as_soap():
    assert cbr.parse_page(PAGE.decode("utf-8")) == cbr.parse_soap(SOAP)


def test_changes_are_decision_days():
    history = cbr.changes(cbr.parse_soap(SOAP))
    assert history[0] == {"date": "2025-09-29", "value": 0.17}
    assert history[-1] == {"date": "2026-07-27", "value": 0.14}
    assert [h["value"] for h in history] == [0.17, 0.165, 0.16, 0.155, 0.15, 0.145, 0.1425, 0.14]


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b"not xml at all",
                                  b"<?xml version='1.0'?><KeyRate/>"])
def test_soap_garbage_is_an_error(body):
    with pytest.raises(cbr.CbrError):
        cbr.parse_soap(body)


def test_rate_in_wrong_units_is_refused():
    broken = SOAP.replace(b"<Rate>14.00</Rate>", b"<Rate>1400</Rate>", 1)
    with pytest.raises(cbr.CbrError, match="единицы"):
        cbr.parse_soap(broken)


def test_page_without_table_is_an_error():
    with pytest.raises(cbr.CbrError):
        cbr.parse_page("<html><body>нет таблицы</body></html>")


def test_fetch_key_rate_by_soap():
    calls = []
    rate = cbr.fetch_key_rate(TODAY, getter=getter_with(soap=SOAP, calls=calls))
    assert (rate["value"], rate["date"], rate["source"], rate["note"]) == \
        (0.14, "2026-09-28", "ЦБ SOAP KeyRate", None)
    assert rate["history"][-1] == {"date": "2026-07-27", "value": 0.14}
    assert len(calls) == 1
    assert calls[0]["url"] == cbr.SOAP_URL
    assert calls[0]["headers"]["SOAPAction"] == "http://web.cbr.ru/KeyRate"
    assert calls[0]["headers"]["User-Agent"].startswith("Python-urllib/")
    assert b"<fromDate>2025-09-27T00:00:00</fromDate>" in calls[0]["data"]
    assert b"<ToDate>2026-09-28T00:00:00</ToDate>" in calls[0]["data"]


@pytest.mark.parametrize("soap", [FetchError(cbr.SOAP_URL, 500, "HTTP 500"),
                                  b"<html>maintenance</html>"])
def test_fetch_key_rate_falls_back_to_page(soap):
    calls = []
    rate = cbr.fetch_key_rate(TODAY, getter=getter_with(soap=soap, page=PAGE, calls=calls))
    assert (rate["value"], rate["date"], rate["source"]) == (0.14, "2026-09-28", "ЦБ hd_base/KeyRate")
    assert "SOAP" in rate["note"]
    assert calls[-1]["url"].startswith(cbr.PAGE_URL)
    assert "UniDbQuery.From=27.09.2025" in calls[-1]["url"]
    assert "UniDbQuery.To=28.09.2026" in calls[-1]["url"]
    # DDoS-Guard cbr.ru пропускает родной заголовок urllib, а не свой.
    assert calls[-1]["headers"]["User-Agent"] == cbr.CBR_USER_AGENT


def test_both_paths_down_is_an_error_naming_both():
    getter = getter_with(soap=FetchError(cbr.SOAP_URL, None, "timeout"),
                         page=FetchError(cbr.PAGE_URL, 503, "HTTP 503"))
    with pytest.raises(cbr.CbrError) as caught:
        cbr.fetch_key_rate(TODAY, getter=getter)
    assert "SOAP" in str(caught.value) and "страница" in str(caught.value)
