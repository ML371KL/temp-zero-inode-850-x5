"""Правила годности цены X5 (docs/MODEL.md §13.4): возраст ≤ 7 дней, скачок
≤ 30 % к последней принятой, ×10 — чужие единицы; негодная — последняя
принятая из прошлого выпуска; нет запасной — отказ."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import iss  # noqa: E402
from indicators.live import PriceUnavailable, judge_price, last_accepted  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "iss"
TODAY = date(2026, 9, 28)


def history() -> list[dict]:
    rows = []
    for name in ("history_X5_0.json", "history_X5_100.json", "history_X5_200.json"):
        rows += iss.parse_history_page(json.loads((FIX / name).read_text(encoding="utf-8")))[0]
    return rows


HISTORY = history()
REF = {"value": 1808.5, "date": "2026-09-25", "origin": "цена прошлого выпуска"}


def collected(value=1802.5, day="2026-09-28", time="12:45:05", kind="last"):
    return {"value": value, "date": day, "time": time, "kind": kind, "source": "ISS TQBR"}


def test_fresh_price_is_accepted_and_becomes_the_reference():
    price = judge_price(collected(), reference=REF, history=HISTORY, today=TODAY)
    assert price["accepted"] and price["status"] == "live"
    assert (price["value"], price["date"], price["time"]) == (1802.5, "2026-09-28", "12:45:05")
    assert price["reason"] is None
    assert price["last_accepted"] == {"value": 1802.5, "date": "2026-09-28"}
    assert price["reference"] == REF


def test_price_older_than_seven_days_falls_back():
    price = judge_price(collected(day="2026-09-20"), reference=REF, history=HISTORY, today=TODAY)
    assert not price["accepted"] and price["status"] == "fallback"
    assert (price["value"], price["date"]) == (1808.5, "2026-09-25")
    assert "устарела на 8 дн" in price["reason"]
    assert price["last_accepted"] == {"value": 1808.5, "date": "2026-09-25"}
    assert price["collected"]["value"] == 1802.5


def test_seven_days_is_still_fresh():
    older_ref = dict(REF, date="2026-09-18")  # эталон не новее цены: проверяется только возраст
    price = judge_price(collected(value=1810, day="2026-09-21"), reference=older_ref,
                        history=HISTORY, today=TODAY)
    assert price["accepted"]


@pytest.mark.parametrize("factor", [1.31, 0.69])
def test_jump_over_thirty_percent_falls_back(factor):
    price = judge_price(collected(value=1808.5 * factor), reference=REF, history=HISTORY,
                        today=TODAY)
    assert not price["accepted"]
    assert price["value"] == 1808.5
    assert "при пределе 30%" in price["reason"]


def test_jump_just_under_thirty_percent_is_accepted():
    price = judge_price(collected(value=1808.5 * 1.29), reference=REF, history=HISTORY, today=TODAY)
    assert price["accepted"]


@pytest.mark.parametrize("value", [180250.0, 18.025, 18085.0])
def test_foreign_units_never_accepted(value):
    price = judge_price(collected(value=value), reference=REF, history=HISTORY, today=TODAY)
    assert not price["accepted"]
    assert "чужие единицы" in price["reason"]


def test_foreign_units_refused_even_after_downtime():
    old = {"value": 1808.5, "date": "2026-08-01", "origin": "цена прошлого выпуска"}
    price = judge_price(collected(value=18085.0), reference=old, history=HISTORY, today=TODAY)
    assert not price["accepted"] and "чужие единицы" in price["reason"]


def test_jump_after_downtime_confirmed_by_neighbour_day_is_accepted_loudly():
    old = {"value": 1300.0, "date": "2026-09-01", "origin": "цена прошлого выпуска"}
    price = judge_price(collected(), reference=old, history=HISTORY, today=TODAY)
    assert price["accepted"]
    assert "проверка скачка снята" in price["reason"]
    assert "2026-09-25" in price["reason"]
    assert price["last_accepted"] == {"value": 1802.5, "date": "2026-09-28"}


def test_jump_after_downtime_without_confirmation_falls_back():
    old = {"value": 1300.0, "date": "2026-09-01", "origin": "цена прошлого выпуска"}
    shifted = [dict(row, close=row["close"] * 1.2) if row["date"] == "2026-09-25" else row
               for row in HISTORY]
    price = judge_price(collected(), reference=old, history=shifted, today=TODAY)
    assert not price["accepted"]
    assert "ждём подтверждения" in price["reason"]
    assert price["last_accepted"] == {"value": 1300.0, "date": "2026-09-01"}


def test_first_run_checks_against_history_close():
    price = judge_price(collected(), reference=None, history=HISTORY, today=TODAY)
    assert price["accepted"]
    assert price["reference"]["date"] == "2026-09-25"
    assert "истории ISS" in price["reference"]["origin"]


def test_first_run_bad_price_has_no_fallback():
    with pytest.raises(PriceUnavailable, match="запасной цены нет"):
        judge_price(collected(value=18025.0), reference=None, history=HISTORY, today=TODAY)


def test_no_reference_at_all_is_accepted_with_a_note():
    price = judge_price(collected(), reference=None, history=[], today=TODAY)
    assert price["accepted"] and "не проверялся" in price["reason"]


def test_missing_price_falls_back_with_the_reason():
    price = judge_price(None, reference=REF, history=HISTORY, today=TODAY,
                        error="FetchError: HTTP 503")
    assert not price["accepted"] and price["value"] == 1808.5
    assert "не собрана" in price["reason"] and "503" in price["reason"]


def test_missing_price_without_fallback_is_a_failure():
    with pytest.raises(PriceUnavailable):
        judge_price(None, reference=None, history=HISTORY, today=TODAY)


@pytest.mark.parametrize("value", [0, -5, None])
def test_non_positive_price_falls_back(value):
    price = judge_price(collected(value=value), reference=REF, history=HISTORY, today=TODAY)
    assert not price["accepted"]


def test_price_older_than_the_last_accepted_falls_back():
    """Повторный прогон в тот же день: котировки отказали, история дала
    закрытие прошлого дня — цена и эталон назад не откатываются."""
    today_ref = {"value": 1796.0, "date": "2026-09-28", "origin": "последняя принятая, из прошлого выпуска"}
    price = judge_price(collected(value=1808.5, day="2026-09-25", time=None, kind="history_close"),
                        reference=today_ref, history=HISTORY, today=TODAY)
    assert not price["accepted"] and price["status"] == "fallback"
    assert (price["value"], price["date"]) == (1796.0, "2026-09-28")
    assert price["last_accepted"] == {"value": 1796.0, "date": "2026-09-28"}
    assert "старше последней принятой" in price["reason"]


def test_price_of_the_same_day_as_the_reference_is_accepted():
    same_day = {"value": 1796.0, "date": "2026-09-28", "origin": "последняя принятая, из прошлого выпуска"}
    price = judge_price(collected(), reference=same_day, history=HISTORY, today=TODAY)
    assert price["accepted"] and price["last_accepted"] == {"value": 1802.5, "date": "2026-09-28"}


def test_price_from_the_future_falls_back():
    price = judge_price(collected(day="2026-09-29"), reference=REF, history=HISTORY, today=TODAY)
    assert not price["accepted"] and "позже даты прогона" in price["reason"]


def test_last_accepted_prefers_the_carried_reference():
    previous = {"live": {"price": {"value": 1500, "accepted": False,
                                   "last_accepted": {"value": 1790.0, "date": "2026-09-24"}}},
                "market": {"price": 1500, "price_date": "2026-09-25"}}
    assert last_accepted(previous) == {"value": 1790.0, "date": "2026-09-24",
                                       "origin": "последняя принятая, из прошлого выпуска"}


def test_last_accepted_falls_back_to_market_price():
    previous = {"market": {"price": 1808.5, "price_date": "2026-09-25", "price_status": "live"}}
    assert last_accepted(previous)["value"] == 1808.5
    assert last_accepted({"market": {"price": None}}) is None
    assert last_accepted(None) is None
