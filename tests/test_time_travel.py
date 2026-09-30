"""Прогон «в будущем» (`FAKE_TODAY`): книга заморожена, дата идёт вперёд.

* точка растёт с датой оценки плавно — без скачков на границах полугодий и
  на датах отсечки (§13.4: это ожидаемая доходность капитала, не новость);
* флаги поднимаются по календарю: `book_update` — книге больше
  `checks.book_update.max_age_days` дней, `dividend_register` — ожидаемая
  отсечка прошла, а объявленного дивиденда в реестре нет, `report_fact` —
  МСФО, закрывающее открытое полугодие, вышло, а книга его не закрыла (на +180
  дней от книги 1.1.3 — после годового МСФО ≈19.03.2027);
* сборка на входах книги с датой оценки +30/+90/+180 дней проходит: контракт
  цел, и от одной только даты не появляется новых блокирующих причин
  (истёкшее объяснение гейта или масса, уехавшая из коридора, — провал здесь,
  а не в конвейере в тот день).
"""

from __future__ import annotations

import datetime as dt

import pytest

from model import payload as P
from model.checks import flag_book_update, flag_dividend_register
from model.facts import core_facts
from model.grid import evaluate

OFFSETS = (30, 90, 180)


@pytest.fixture(scope="module")
def cf(book, facts):
    return core_facts(facts, book)


def test_point_grows_smoothly_with_the_valuation_date(book, cf):
    start = dt.date.fromisoformat(book["meta"]["valuation_date"])
    points = [evaluate(book, cf, valuation_date=start + dt.timedelta(days=k)).point.central
              for k in range(max(OFFSETS) + 1)]
    steps = [b - a for a, b in zip(points, points[1:])]
    assert min(steps) > 0, "при замороженной книге точка с датой только растёт"
    assert max(steps) <= 1.5 * min(steps), f"скачок дневного шага: {min(steps):.3f}…{max(steps):.3f} ₽"


def test_flags_rise_on_the_calendar(book, facts, book_date):
    age = book["checks"]["book_update"]["max_age_days"]
    assert not flag_book_update(book, None, book_date).raised
    assert not flag_book_update(book, None, book_date + dt.timedelta(days=age)).raised
    assert flag_book_update(book, None, book_date + dt.timedelta(days=age + 1)).raised
    expected = P._expected_ex_dates()
    reg = facts.data["dividends"]["register"]
    facts_date = dt.date.fromisoformat(book["meta"]["facts_date"])
    ahead = [d for d in expected if d > book_date]
    if ahead:
        first = min(ahead)
        assert not flag_dividend_register(expected, reg, facts_date, first - dt.timedelta(days=1)).raised
        assert flag_dividend_register(expected, reg, facts_date, first).raised


def _live_on(book, day: dt.date) -> dict:
    """Живые входы дня: цена книги с датой `day` (кривая и прочее не пришли)."""
    return {"price": {"value": float(book["meta"]["market_price"]), "date": day.isoformat(),
                      "time": "19:00:00", "source": "ISS TQBR", "status": "live",
                      "accepted": True, "reason": None,
                      "last_accepted": {"value": float(book["meta"]["market_price"]),
                                        "date": day.isoformat()}},
            "curve": None, "key_rate": None, "peers": {}, "bonds": [], "price_history": [],
            "errors": {}, "fetched_at": f"{day.isoformat()}T16:50:00+00:00"}


def _blocking(payload) -> set:
    return {g["name"] for g in payload["checks"]["gates"] if g["blocking"]}


@pytest.mark.ci_only
def test_release_builds_in_the_future(book, facts, book_date, monkeypatch):
    monkeypatch.setenv("FAKE_TODAY", book_date.isoformat())
    base = P.build_payload(book=book, facts=facts, live=_live_on(book, book_date), fast=True,
                           strict=False, n_workers=1)
    assert P.validate(base) == []
    points = [base["fair_value"]["central"]]
    age = book["checks"]["book_update"]["max_age_days"]
    for k in OFFSETS:
        day = book_date + dt.timedelta(days=k)
        monkeypatch.setenv("FAKE_TODAY", day.isoformat())
        payload = P.build_payload(book=book, facts=facts, live=_live_on(book, day), fast=True,
                                  strict=False, n_workers=1, previous=base)
        assert P.validate(payload) == [], k
        assert payload["meta"]["valuation_date"] == day.isoformat()
        assert payload["meta"]["generated_at"].startswith(day.isoformat())
        new = _blocking(payload) - _blocking(base)
        assert not new, f"+{k} дн.: новые блокирующие гейты {sorted(new)}"
        assert all(i["ok"] for i in payload["checks"]["invariants"])
        flags = {f["name"]: f["raised"] for f in payload["checks"]["flags"]}
        assert flags["book_update"] == (k > age)
        passed = [d for d in P._expected_ex_dates() if book_date < d <= day]
        assert flags["dividend_register"] == bool(passed)
        closing = P.closing_event(payload["next_report"]["period"])
        out = closing is not None and dt.date.fromisoformat(closing["date"]) <= day
        assert flags["report_fact"] == out
        assert (payload["next_report"]["closing"] or {}).get("published", False) == out
        assert not flags["price_fallback"]
        ch = payload["changes"]["vs_previous"]
        roll = next(r["rub"] for r in ch["rows"] if r["component"] == "valuation_date")
        assert roll > 0, "перекат даты оценки поднимает точку"
        points.append(payload["fair_value"]["central"])
    assert points == sorted(points) and len(set(points)) == len(points)
