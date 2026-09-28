"""Разбор ответов MOEX ISS на сохранённых реальных ответах (28.09.2026).

Фикстуры — `tests/fixtures/iss/`, адреса и хэши — `tests/fixtures/live_sources.json`.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import iss  # noqa: E402
from indicators.http import Response  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
TODAY = date(2026, 9, 28)
YEAR_AGO = date(2025, 9, 28)


def load(name: str) -> dict:
    return json.loads((FIX / "iss" / name).read_text(encoding="utf-8"))


def manifest() -> dict:
    return json.loads((FIX / "live_sources.json").read_text(encoding="utf-8"))


def fixture_getter(calls: list | None = None, overrides: dict | None = None):
    """Подмена `indicators.http.fetch`: отдаёт фикстуру по имени ответа."""
    def getter(url, *, source, name, **kw):
        if calls is not None:
            calls.append({"url": url, "source": source, "name": name, **kw})
        if overrides and name in overrides:
            value = overrides[name]
            if isinstance(value, Exception):
                raise value
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
        else:
            body = (FIX / source / name).read_bytes()
        return Response(url=url, status=200, body=body, fetched_at="2026-09-28T10:00:00+00:00")
    return getter


# ------------------------------------------------------------------ котировки

def test_quotes_last_trade_with_date_and_time():
    quotes = iss.parse_quotes(load("quotes_tqbr.json"))
    x5 = quotes["X5"]
    assert x5["price"] == 1802.5
    assert x5["date"] == "2026-09-28"
    assert x5["time"] == "12:45:05"
    assert x5["kind"] == "last"
    assert x5["source"] == "ISS TQBR"
    assert x5["isin"] == "RU000A108X38"
    assert {"MGNT", "LENT", "FIXR", "OKEY"} <= set(quotes)
    assert quotes["MGNT"]["price"] == 1628.5
    assert quotes["FIXR"]["price"] == 0.3864


def test_quotes_outside_trading_take_previous_day_with_its_date():
    payload = load("quotes_tqbr.json")
    cols = payload["marketdata"]["columns"]
    for row in payload["marketdata"]["data"]:
        if row[cols.index("SECID")] == "X5":
            row[cols.index("LAST")] = None
    x5 = iss.parse_quotes(payload)["X5"]
    assert (x5["price"], x5["date"], x5["time"], x5["kind"]) == (1809.5, "2026-09-25", None, "prev")


def test_quotes_previous_close_when_no_previous_last_price():
    payload = load("quotes_tqbr.json")
    mcols, scols = payload["marketdata"]["columns"], payload["securities"]["columns"]
    for row in payload["marketdata"]["data"]:
        row[mcols.index("LAST")] = None
    for row in payload["securities"]["data"]:
        row[scols.index("PREVPRICE")] = None
    x5 = iss.parse_quotes(payload)["X5"]
    assert (x5["price"], x5["date"], x5["kind"]) == (1808.5, "2026-09-25", "prev")


def test_quotes_without_any_price_are_none_not_zero():
    payload = load("quotes_tqbr.json")
    mcols, scols = payload["marketdata"]["columns"], payload["securities"]["columns"]
    for row in payload["marketdata"]["data"]:
        row[mcols.index("LAST")] = 0
    for row in payload["securities"]["data"]:
        row[scols.index("PREVPRICE")] = None
        row[scols.index("PREVLEGALCLOSEPRICE")] = None
    assert iss.parse_quotes(payload)["X5"]["price"] is None


def test_quotes_garbage_is_an_error():
    with pytest.raises(iss.IssError):
        iss.parse_quotes({"error": "html instead of json"})


def test_quotes_url_is_the_recorded_one():
    assert iss.quotes_url(("X5", *iss.PEERS)) == manifest()["iss/quotes_tqbr.json"]["url"]


# ------------------------------------------------------------------ история

def test_history_all_pages_in_order():
    calls = []
    rows = iss.fetch_price_history("X5", start=YEAR_AGO, till=TODAY,
                                   getter=fixture_getter(calls))
    assert [c["name"] for c in calls] == ["history_X5_0.json", "history_X5_100.json",
                                          "history_X5_200.json"]
    recorded = manifest()
    assert [c["url"] for c in calls] == [recorded[f"iss/{c['name']}"]["url"] for c in calls]
    assert len(rows) == 253
    assert rows[0] == {"date": "2025-09-29", "close": 2756.0}
    assert rows[-1] == {"date": "2026-09-25", "close": 1808.5}
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)


def test_history_close_is_official_close_then_last_trade():
    page = {"history": {"columns": ["TRADEDATE", "LEGALCLOSEPRICE", "CLOSE", "VOLUME"],
                        "data": [["2026-09-24", 1827.5, 1840, 1],
                                 ["2026-09-25", None, 1809.5, 1],
                                 ["2026-09-26", None, None, 0]]}}
    rows, cursor = iss.parse_history_page(page)
    assert rows == [{"date": "2026-09-24", "close": 1827.5},
                    {"date": "2026-09-25", "close": 1809.5}]
    assert cursor is None


def test_history_endless_cursor_is_cut():
    endless = {"history": {"columns": ["TRADEDATE", "LEGALCLOSEPRICE", "CLOSE", "VOLUME"],
                           "data": [["2026-09-25", 1808.5, 1809.5, 1]]},
               "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"],
                                  "data": [[0, 10 ** 9, 100]]}}

    def getter(url, *, source, name, **kw):
        return Response(url, 200, json.dumps(endless).encode(), "2026-09-28T10:00:00+00:00")

    with pytest.raises(iss.IssError):
        iss.fetch_price_history("X5", start=YEAR_AGO, till=TODAY, getter=getter)


# ------------------------------------------------------------------ кривая

def test_zcyc_nodes_are_fractions_on_latest_date():
    curve = iss.fetch_zcyc(getter=fixture_getter())
    assert curve["as_of"] == "2026-09-28"
    assert curve["time"] == "13:00:09"
    assert curve["nodes"] == {"1": 0.136441, "3": 0.156189, "5": 0.162421, "10": 0.167439}
    assert curve["source"] == "ISS zcyc"


def test_zcyc_in_wrong_units_is_refused():
    payload = load("zcyc.json")
    cols = payload["yearyields"]["columns"]
    for row in payload["yearyields"]["data"]:
        row[cols.index("value")] = row[cols.index("value")] / 100  # доли вместо процентов
    with pytest.raises(iss.IssError, match="единицы"):
        iss.parse_zcyc(payload)


def test_zcyc_missing_node_is_refused():
    payload = load("zcyc.json")
    cols = payload["yearyields"]["columns"]
    payload["yearyields"]["data"] = [r for r in payload["yearyields"]["data"]
                                     if r[cols.index("period")] != 10]
    with pytest.raises(iss.IssError, match="10"):
        iss.parse_zcyc(payload)


def test_zcyc_takes_only_the_latest_date():
    payload = load("zcyc.json")
    old = copy.deepcopy(payload["yearyields"]["data"])
    cols = payload["yearyields"]["columns"]
    for row in old:
        row[cols.index("tradedate")] = "2026-09-25"
        row[cols.index("value")] = 20.0
    payload["yearyields"]["data"] = old + payload["yearyields"]["data"]
    assert iss.parse_zcyc(payload)["nodes"]["5"] == 0.162421


# ------------------------------------------------------------------ облигации

def test_emitter_bonds_traded_on_tqcb():
    secids = iss.parse_emitter_bonds(load("emitter_securities.json"))
    assert len(secids) == 16
    assert "RU000A109KC0" in secids and "RU000A1075S4" in secids


def test_bonds_fields_and_units():
    calls = []
    bonds = iss.fetch_x5_bonds(getter=fixture_getter(calls))
    assert [c["name"] for c in calls] == ["emitter_securities.json", "bonds_tqcb.json"]
    assert calls[0]["url"] == manifest()["iss/emitter_securities.json"]["url"]
    assert calls[1]["url"] == manifest()["iss/bonds_tqcb.json"]["url"]
    assert len(bonds) == 16
    by_isin = {b["isin"]: b for b in bonds}
    fixed = by_isin["RU000A109KC0"]
    assert fixed["coupon_type"] == "fixed"
    assert fixed["coupon"] == 0.144
    assert fixed["put_date"] == "2027-11-19"
    assert fixed["maturity"] == "2035-11-04"
    assert fixed["price"] == 100.1 and fixed["price_date"] == "2026-09-28"
    assert fixed["ytm"] == 0.1529
    assert fixed["outstanding"] == 26.0
    assert 1.0 < fixed["duration_years"] < 1.1
    floater = by_isin["RU000A1075S4"]
    assert floater["coupon_type"] == "floating"
    assert floater["put_date"] is None and floater["maturity"] == "2026-10-17"
    for bond in bonds:
        assert bond["ytm"] is None or 0 < bond["ytm"] < 1
        assert bond["name"].startswith("ИКС 5 ФИНАНС")
    # по ближайшей оферте / погашению
    keys = [b["put_date"] or b["maturity"] for b in bonds]
    assert keys == sorted(keys)


def test_bond_without_trade_today_takes_previous_day():
    payload = load("bonds_tqcb.json")
    mcols, scols = payload["marketdata"]["columns"], payload["securities"]["columns"]
    for row in payload["marketdata"]["data"]:
        row[mcols.index("LAST")] = None
    bond = next(b for b in iss.parse_bonds(payload) if b["isin"] == "RU000A109KC0")
    sec = next(r for r in payload["securities"]["data"] if r[scols.index("SECID")] == "RU000A109KC0")
    assert bond["price"] == sec[scols.index("PREVPRICE")]
    assert bond["price_date"] == sec[scols.index("PREVDATE")]
    assert bond["ytm"] == round(sec[scols.index("YIELDATPREVWAPRICE")] / 100, 10)
    assert bond["turnover"] is None


def test_no_traded_bonds_is_an_error():
    empty = {"securities": {"columns": ["SECID", "IS_TRADED", "PRIMARY_BOARDID"], "data": []}}
    with pytest.raises(iss.IssError):
        iss.fetch_x5_bonds(getter=fixture_getter(overrides={"emitter_securities.json": empty}))
