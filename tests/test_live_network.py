"""Живые проверки источников (метка network: в CI и такте не идут).

    python -m pytest -m network tests/test_live_network.py
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import cbr, iss, live  # noqa: E402

pytestmark = pytest.mark.network


@pytest.fixture(autouse=True)
def raw_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("X5_RAW_DIR", str(tmp_path / "raw"))


def test_iss_quotes_have_x5_and_peers():
    quotes = iss.fetch_quotes()
    assert quotes["X5"]["price"] and quotes["X5"]["price"] > 0
    assert set(iss.PEERS) <= set(quotes)


def test_iss_history_covers_a_year():
    today = live.msk_today()
    rows = iss.fetch_price_history("X5", start=today - timedelta(days=365), till=today)
    assert len(rows) > 200


def test_iss_curve_nodes():
    curve = iss.fetch_zcyc()
    assert set(curve["nodes"]) == set(iss.CURVE_NODES)


def test_iss_x5_bonds():
    bonds = iss.fetch_x5_bonds()
    assert bonds and all(b["name"].startswith("ИКС 5 ФИНАНС") for b in bonds)


def test_cbr_key_rate():
    rate = cbr.fetch_key_rate(live.msk_today())
    assert iss.RATE_MIN <= rate["value"] <= iss.RATE_MAX
    assert rate["source"] == "ЦБ SOAP KeyRate"


def test_cbr_page_fallback():
    today = live.msk_today()
    from indicators.http import fetch
    response = fetch(cbr.page_url(today - timedelta(days=30), today), source="cbr",
                     name="key_rate_page.html", headers={"User-Agent": cbr.CBR_USER_AGENT})
    assert cbr.parse_page(response.text)


def test_collect_live_end_to_end(tmp_path):
    # Ключевая ставка здесь не требуется: её проверяют два теста выше.
    result = live.collect_live(None)
    assert result["price"]["accepted"]
    assert not {"quotes", "price_history", "curve", "bonds"} & set(result["errors"])
    assert list((tmp_path / "raw").rglob("*.meta.json"))
