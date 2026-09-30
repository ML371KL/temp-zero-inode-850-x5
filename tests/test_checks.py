"""Инварианты, гейты с массой и объяснениями, флаги (docs/MODEL.md §13)."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import math

import pytest

from model.checks import (GATES, GATE_EXPLANATIONS, GateExplanationError, blocking_reasons,
                          flag_book_update, flag_dividend_register, flag_price_fallback,
                          flag_report_fact, gate_masses, gate_violations, gates, invariants,
                          load_gate_explanations, printed_ok, round_to_step)
from model.grid import evaluate


def test_invariants_hold_on_book(grid):
    bad = [i for i in invariants(grid) if not i.ok]
    assert not bad, bad


def test_fcff_identity_catches_tampering(grid):
    cell = grid.cells[0]
    from model.core import HalfRow

    at = [f.name for f in dataclasses.fields(HalfRow)].index("fcff")
    data = [list(t) for t in cell.result.row_data]
    data[3][at] += 1.0
    bad_cell = dataclasses.replace(
        cell, result=dataclasses.replace(cell.result, row_data=tuple(map(tuple, data))))
    broken = dataclasses.replace(grid, cells=(bad_cell, *grid.cells[1:]))
    names = {i.name for i in invariants(broken) if not i.ok}
    assert "fcff_identity" in names and "debt_identity" in names


def test_probability_invariant_catches_bad_weights(grid):
    cells = tuple(dataclasses.replace(c, p={**c.p, "analytical": c.p["analytical"] * 1.001})
                  for c in grid.cells)
    broken = dataclasses.replace(grid, cells=cells)
    assert "probabilities" in {i.name for i in invariants(broken) if not i.ok}


def test_gate_mass_is_analytical_probability(book, facts):
    B = copy.deepcopy(book)
    grid = evaluate(B, facts)
    margins = sorted(c.result.margin_max for c in grid.cells)
    B["checks"]["margin_range"] = [0.0, margins[len(margins) // 2]]
    grid = evaluate(B, facts)
    hits = {key for key, _ in gate_violations(grid)["margin_range"]}
    expect = math.fsum(c.p["analytical"] for c in grid.cells
                       if c.result.margin_max > B["checks"]["margin_range"][1])
    g = next(g for g in gate_masses(grid) if g.name == "margin_range")
    assert g.fired and hits and g.mass == pytest.approx(expect, abs=1e-15)


def test_layer_level_cushion_gives_full_mass(book, facts):
    B = copy.deepcopy(book)
    B["checks"]["min_v0_to_d"] = 1e6
    g = next(g for g in gate_masses(evaluate(B, facts)) if g.name == "equity_cushion")
    assert g.fired and g.mass == 1.0


def _explained(mass, until):
    return {"explanation": "текст", "expected_mass": mass, "valid_until": until}


def test_gate_explanation_states(grid, book_date):
    fired = [g for g in gate_masses(grid) if g.fired]
    assert fired, "на книге должен срабатывать хотя бы один гейт — иначе тест пуст"
    g = fired[0]
    later = book_date + dt.timedelta(days=30)
    cases = {
        "unexplained": {},
        "explained": {g.name: _explained((0.0, 1.0), later)},
        "expired": {g.name: _explained((0.0, 1.0), book_date - dt.timedelta(days=1))},
        "mass_outside": {g.name: _explained((min(1.0, g.mass + 0.01), 1.0), later)},
    }
    for status, explanations in cases.items():
        result = next(x for x in gates(grid, explanations, today=book_date) if x.name == g.name)
        assert result.status == status
        assert result.blocking == (status != "explained")
    ok = gates(grid, {x.name: _explained((0.0, 1.0), later) for x in fired}, today=book_date)
    assert not blocking_reasons(invariants(grid), ok)
    assert all(x.status == "ok" for x in ok if not x.fired)


def test_valid_until_is_inclusive(grid, book_date):
    g = next(g for g in gate_masses(grid) if g.fired)
    res = gates(grid, {g.name: _explained((0.0, 1.0), book_date)}, today=book_date)
    assert next(x for x in res if x.name == g.name).status == "explained"


def test_book_explanations_load_and_cover_fired_gates(grid, book_date):
    """Файл объяснений книги читается, и на входах книги ни один гейт не блокирует сборку."""
    loaded = load_gate_explanations(GATE_EXPLANATIONS)
    fired = {g.name for g in gate_masses(grid) if g.fired}
    assert fired <= set(loaded)
    res = gates(grid, loaded, today=book_date)
    assert all(x.status in ("ok", "explained") for x in res)


@pytest.mark.parametrize("text,word", [
    ("nope:\n  explanation: x\n  expected_mass: [0, 1]\n  valid_until: 2027-01-01\n", "незнакомый гейт"),
    ("ev_ebitda:\n  explanation: x\n  expected_mass: [0, 1]\n", "нет ключа"),
    ("ev_ebitda:\n  explanation: x\n  expected_mass: 0.2\n  valid_until: 2027-01-01\n", "коридор"),
    ("ev_ebitda:\n  explanation: x\n  expected_mass: [0.3, 0.1]\n  valid_until: 2027-01-01\n", "коридор"),
    ("ev_ebitda:\n  explanation: ''\n  expected_mass: [0, 1]\n  valid_until: 2027-01-01\n", "пустой"),
    ("ev_ebitda:\n  explanation: x\n  expected_mass: [0, 1]\n  valid_until: завтра\n", "не дата"),
    ("ev_ebitda:\n  explanation: x\n  expected_mass: [0, 1]\n  valid_until: 2027-01-01\n  mass: 1\n", "незнакомый ключ"),
])
def test_explanation_format_errors(tmp_path, text, word):
    path = tmp_path / "gate_explanations.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(GateExplanationError, match=word):
        load_gate_explanations(path)


def test_all_gates_reported(grid):
    assert [g.name for g in gate_masses(grid)] == list(GATES)


def test_flag_book_update(book, book_date):
    curve = book["worlds"][book["joint"]["neutral_world"]]["zero_curve"]
    rule = book["checks"]["book_update"]
    quiet = flag_book_update(book, {"5": curve["5"], "10": curve["10"]}, today=book_date)
    assert not quiet.raised
    moved = {"5": curve["5"] + rule["shift_bp"] / 10_000, "10": curve["10"]}
    assert flag_book_update(book, moved, today=book_date).raised
    old = book_date + dt.timedelta(days=rule["max_age_days"] + 1)
    assert flag_book_update(book, None, today=old).raised
    assert not flag_book_update(book, None, today=book_date).raised


def test_flag_dividend_register(book_date):
    facts_date = book_date - dt.timedelta(days=90)
    expected = [book_date - dt.timedelta(days=10)]
    empty = flag_dividend_register(expected, [], facts_date, today=book_date)
    assert empty.raised
    register = [{"ex_date": expected[0].isoformat()}]
    assert not flag_dividend_register(expected, register, facts_date, today=book_date).raised
    future = [book_date + dt.timedelta(days=10)]
    assert not flag_dividend_register(future, [], facts_date, today=book_date).raised


def test_flag_report_fact():
    """`report_fact`: закрывающее МСФО открытого полугодия по календарю вышло (дата не позже
    даты оценки, включительно — отчёт выходит утром), а книга полугодие не закрыла."""
    out = dt.date(2027, 3, 19)
    title = "Финансовые результаты X5 за 2026 г. (МСФО)"
    assert not flag_report_fact("2026H2", out, title, out - dt.timedelta(days=1)).raised
    on_day = flag_report_fact("2026H2", out, title, out)
    assert on_day.raised and on_day.name == "report_fact"
    later = flag_report_fact("2026H2", out, title, out + dt.timedelta(days=3))
    assert later.raised and title in later.detail and "2027-03-19" in later.detail
    assert "2П 2026" in later.detail
    # деталь идёт на витрину через ruText: без десятичных чисел вроде «10.3» (стали бы «10,3»)
    assert not any(a.isdigit() and b == "." and c.isdigit()
                   for a, b, c in zip(later.detail, later.detail[1:], later.detail[2:]))
    assert not flag_report_fact("2026H2", None, None, out).raised
    assert not flag_report_fact(None, None, None, out).raised


def test_flag_price_fallback():
    assert flag_price_fallback("fallback").raised
    assert not flag_price_fallback("live").raised
    assert not flag_price_fallback("book").raised
    # деталь идёт на витрину — словами, а не ключом статуса
    assert flag_price_fallback("live").detail == "цена рынка: живая цена"
    assert flag_price_fallback("book").detail == "цена рынка: цена книги"
    assert flag_price_fallback("fallback").detail == "цена рынка: запасная цена"


def test_printing_rounds_half_up():
    assert round_to_step(1824.99, 50) == 1800
    assert round_to_step(1825.0, 50) == 1850
    assert round_to_step(-1825.0, 50) == -1800
    assert printed_ok(1812.4, 1800, 50) and not printed_ok(1812.4, 1850, 50)
