"""Журнал прогнозов (model/journal.py): запись, факт, неизменяемость, эталоны из фактов."""

from __future__ import annotations

import copy
import json

import pytest

from model import journal as J
from model.book import open_period, prev_period, prev_same_half

PERIOD = "2026H2"
FORECASTS = {"x5.adj_margin": 0.0612, "x5.revenue_growth": 0.121}
BENCH = {k: {"margin": 0.06 + i / 1000, "revenue_growth": 0.1 + i / 100, "note": k}
         for i, k in enumerate(J.BENCHMARKS)}


def _first():
    journal, new = J.update(None, period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                            recorded_at="2026-09-28", actuals={})
    for e in journal["entries"]:
        e["release_sha"] = "a" * 64            # как ставит выпуск после хэша
    return journal, new


def test_first_release_records_both_targets():
    journal, new = _first()
    assert sorted(new) == sorted(J.entry_id(t, PERIOD) for t in J.TARGETS)
    by = {e["target"]: e for e in journal["entries"]}
    assert by["x5.adj_margin"]["forecast"] == 0.0612
    assert by["x5.adj_margin"]["benchmarks"] == {k: BENCH[k]["margin"] for k in J.BENCHMARKS}
    assert by["x5.revenue_growth"]["benchmarks"] == {k: BENCH[k]["revenue_growth"]
                                                      for k in J.BENCHMARKS}
    assert all(e["actual"] is None and e["errors"] is None for e in journal["entries"])
    assert journal["rule"] and journal["status"].startswith("копим зачёт: 0 из 4")


def test_next_release_keeps_entries_byte_for_byte():
    first, _ = _first()
    moved = {k: v + 0.01 for k, v in FORECASTS.items()}   # книга сменилась — прогноз другой
    second, new = J.update(first, period=PERIOD, forecasts=moved, bench=BENCH,
                           recorded_at="2026-10-15", actuals={})
    assert new == []
    assert [J.canonical(e) for e in second["entries"]] == [J.canonical(e) for e in first["entries"]]
    assert J.immutability_problems(first, second) == []


def test_actual_closes_the_entry_once():
    first, _ = _first()
    actuals = {("x5.adj_margin", PERIOD): 0.063}
    second, _ = J.update(first, period="2027H1", forecasts=FORECASTS, bench=BENCH,
                         recorded_at="2027-03-20", actuals=actuals)
    closed = next(e for e in second["entries"] if e["target"] == "x5.adj_margin"
                  and e["period"] == PERIOD)
    assert closed["actual"] == 0.063
    assert closed["errors"]["forecast"] == pytest.approx(0.0612 - 0.063)
    assert closed["errors"]["benchmarks"]["last_half"] == pytest.approx(BENCH["last_half"]["margin"] - 0.063)
    assert J.immutability_problems(first, second) == []
    # второй факт (правка задним числом) не переписывает первый
    third, _ = J.update(second, period="2027H1", forecasts=FORECASTS, bench=BENCH,
                        recorded_at="2027-03-21", actuals={("x5.adj_margin", PERIOD): 0.07})
    again = next(e for e in third["entries"] if e["id"] == closed["id"])
    assert again["actual"] == 0.063
    assert J.immutability_problems(second, third) == []
    # новый период получил свои записи
    assert {(e["target"], e["period"]) for e in second["entries"]} >= {("x5.adj_margin", "2027H1")}


def test_rewriting_the_past_is_caught():
    first, _ = _first()
    for mutate, word in ((lambda j: j["entries"][0].update(forecast=0.07), "переписана"),
                         (lambda j: j["entries"][0].update(benchmarks={}), "переписана"),
                         (lambda j: j["entries"].pop(0), "пропала"),
                         (lambda j: j["entries"].append(copy.deepcopy(j["entries"][0])),
                          "повторяется")):
        bad = copy.deepcopy(first)
        mutate(bad)
        assert any(word in p for p in J.immutability_problems(first, bad)), word
    closed, _ = J.update(first, period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                         recorded_at="2027-03-20", actuals={("x5.adj_margin", PERIOD): 0.063})
    reopened = copy.deepcopy(closed)
    for e in reopened["entries"]:
        if e["actual"] is not None:
            e["actual"] = 0.05
    assert any("переписана" in p for p in J.immutability_problems(closed, reopened))


def test_no_forecast_after_the_fact():
    journal, new = J.update(None, period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                            recorded_at="2027-03-20", actuals={("x5.adj_margin", PERIOD): 0.063})
    assert new == [J.entry_id("x5.revenue_growth", PERIOD)]


def test_journal_accepts_a_list_and_checks_ids():
    first, _ = _first()
    same, new = J.update(first["entries"], period=PERIOD, forecasts=FORECASTS, bench=BENCH,
                         recorded_at="2026-09-29", actuals={})
    assert new == [] and len(same["entries"]) == 2
    with pytest.raises(J.JournalError):
        J.entries_of([{"target": "x5.adj_margin"}])


def test_status_after_four_closed_halves():
    entries = []
    for i, p in enumerate(("2026H2", "2027H1", "2027H2", "2028H1")):
        e = {"id": J.entry_id("x5.adj_margin", p), "target": "x5.adj_margin", "period": p,
             "forecast": 0.06, "benchmarks": {k: 0.06 + (j + 1) / 1000
                                              for j, k in enumerate(J.BENCHMARKS)},
             "actual": None, "errors": None}
        e["actual"] = 0.0605
        e["errors"] = J.errors_of(e, e["actual"])
        entries.append(e)
    text = J.status(entries)
    assert "MSE" in text and "4 полугодий" in text


def test_benchmarks_come_from_reported_facts(book, facts):
    period = open_period(book)
    acc = facts.data["accounting"]["periods"]
    last, same = prev_period(period), prev_same_half(period)
    bench = J.benchmarks(facts, period)
    m_last = acc[last]["adj_ebitda"] / acc[last]["revenue"]
    m_same = acc[same]["adj_ebitda"] / acc[same]["revenue"]
    assert bench["last_half"]["margin"] == pytest.approx(m_last, abs=1e-12)
    assert bench["same_half_last_year"]["margin"] == pytest.approx(m_same, abs=1e-12)
    assert bench["mean_two_halves"]["margin"] == pytest.approx((m_last + m_same) / 2, abs=1e-12)
    assert all(bench[k]["revenue_growth"] is not None for k in J.BENCHMARKS)


def test_actuals_file_feeds_the_journal(facts):
    """Формат `data/facts/actuals.json`: {"actuals": [{target, period, value: {v, src}}]}."""
    rows = facts.data.get("actuals", {}).get("actuals", [])
    got = J.actuals_of(facts)
    assert len(got) == sum(1 for r in rows if r.get("target") in J.TARGETS)
    raw = json.dumps(facts.raw.get("actuals", {}), ensure_ascii=False)
    assert "actuals" in raw
