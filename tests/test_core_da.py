"""Амортизация по когортам (docs/MODEL.md §4.5, §6).

* база D&A якоря выбывает по своим когортам S(k)/S(0) — capex 2L полугодий до якоря;
* установившаяся D&A терминала (T1 и T2, части g и π) — предел того же правила когорт:
  на чисто геометрическом пути capex она равна сумме 2L когорт — и при целом L, и при
  полуцелом (6,5; 7,5 …: в окне T1 на одну когорту второго полугодия больше, у T2 — первого);
* правило когорт, продолженное capex терминала, совпадает с установившейся D&A, как
  только все живые когорты терминальные, — переходный член конечен (2L полугодий).
"""

from __future__ import annotations

import math

import pytest

from model.book import override, prev_period
from model.core import Context, annuity_ratio, run_cell, steady_da


def test_anchor_base_runs_off_by_its_cohorts(book, facts):
    ctx = Context(book, facts)
    H = ctx.half_life
    hist = {h["period"]: h["revenue"] * h["capex_pct"] for h in facts.data["history"]["halves"]}
    old, p = [], ctx.anchor
    for _ in range(H):
        p = prev_period(p)
        old.append(hist[p])                      # якорь − 1, якорь − 2, …
    total = math.fsum(old)
    for k in range(1, ctx.N + H + 1):
        want = math.fsum(old[:max(0, H - k)]) / total
        assert ctx.da_runoff[k - 1] == pytest.approx(want, abs=1e-15), k
    runoff = ctx.da_runoff
    assert all(a >= b for a, b in zip(runoff, runoff[1:]))
    assert runoff[H - 1] == 0.0 and runoff[0] < 1.0


def _cohort_sum(c1: float, c2: float, x: float, life: float) -> tuple[float, float]:
    """Прямая сумма 2L когорт: capex полугодий c1·(1+x)^y и c2·(1+x)^y, каждая когорта —
    1/(2L) в 2L следующих полугодиях; D&A первого (T1) и второго (T2) полугодия года 0."""
    H = int(round(2 * life))
    # полугодия от года −(⌈L⌉ + 1) до года 0: индекс 2y + h, h = 0 — первое полугодие
    capex = {}
    for y in range(-math.ceil(life) - 2, 1):
        capex[2 * y] = c1 * (1 + x) ** y
        capex[2 * y + 1] = c2 * (1 + x) ** y
    t1 = math.fsum(capex[-i] for i in range(1, H + 1)) / H            # H1 года 0 (индекс 0)
    t2 = math.fsum(capex[1 - i] for i in range(1, H + 1)) / H         # H2 года 0 (индекс 1)
    return t1, t2


@pytest.mark.parametrize("life", [6.5, 7.0, 7.5])
@pytest.mark.parametrize("c1, c2", [(70.0, 95.0), (60.0, 140.0), (140.0, 60.0)])
@pytest.mark.parametrize("x", [0.0, 0.04, 0.098, -0.01])
def test_steady_da_is_the_cohort_sum_on_a_geometric_path(x, c1, c2, life):
    """Capex полугодий c1·(1+x)^n и c2·(1+x)^n; каждая когорта — 1/(2L) в 2L следующих."""
    t1, t2 = _cohort_sum(c1, c2, x, life)
    da1, da2 = steady_da(c1, c2, x, life)
    assert da1 == pytest.approx(t1, rel=1e-13)
    assert da2 == pytest.approx(t2, rel=1e-13)


def test_steady_da_at_half_year_life_is_the_cohort_sum_not_the_ratio_formula():
    """Контрпример аудита 30.09.2026 (пункт 2): capex 60/140, рост 10 %, L = 7,5. Прямая
    сумма 15 когорт — 69,26632 / 68,91225; запись (c1 + c2)·ratio/2, (c1(1 + x) + c2)·ratio/2
    при нечётном 2L даёт 68,09639 / 70,13928 — в окне T1 когорт второго полугодия на одну
    больше, чем первого, у T2 — наоборот."""
    c1, c2, x, life = 60.0, 140.0, 0.10, 7.5
    t1, t2 = _cohort_sum(c1, c2, x, life)
    assert (t1, t2) == (pytest.approx(69.26632, abs=5e-6), pytest.approx(68.91225, abs=5e-6))
    da1, da2 = steady_da(c1, c2, x, life)
    assert da1 == pytest.approx(t1, rel=1e-13)
    assert da2 == pytest.approx(t2, rel=1e-13)
    ratio = annuity_ratio(x, life)
    assert (c1 + c2) * ratio / 2 == pytest.approx(68.09639, abs=5e-6)
    assert (c1 * (1 + x) + c2) * ratio / 2 == pytest.approx(70.13928, abs=5e-6)


def test_steady_da_at_whole_life_is_the_ratio_formula_bit_for_bit():
    """При целом L установившаяся D&A — прежняя запись через ratio(x, L) бит в бит: таблицы
    книги с L = 7 от точной суммы когорт не сдвигаются."""
    for life in (6.0, 7.0, 8.0):
        for x in (0.0, 0.04, 0.098, -0.01):
            ratio = annuity_ratio(x, life)
            want = ((70.0 + 95.0) * ratio / 2.0, (70.0 * (1.0 + x) + 95.0) * ratio / 2.0)
            assert steady_da(70.0, 95.0, x, life) == want, (life, x)


@pytest.mark.parametrize("life", [6.5, 7.0, 7.5])
@pytest.mark.parametrize("key", ["H|floor|base", "M|stress|high", "N|full|low"])
def test_cohort_rule_meets_steady_da_after_2l_terminal_halves(book, facts, key, life):
    """D&A по правилу §4.5 (capex явного участка, дальше — терминала) = установившейся
    D&A терминала с полугодия 2L + 1; до того разница — переходный член. Срок службы — и
    книжный (7 лет), и полуцелый: нечётное 2L (6,5; 7,5) книга допускает (A-K6)."""
    ctx = Context(override(book, ["capex.asset_life_years"], "value", life), facts)
    assert ctx.half_life == round(2 * life)
    res = run_cell(ctx, ctx.cell(*key.split("|")))
    cf, H, N = ctx.facts, ctx.half_life, ctx.N
    T = res.terminal
    g, pi = T.growth, T.pi
    cg = [h.capex - h.capex_pi for h in T.halves]
    cp = [h.capex_pi for h in T.halves]
    dp = [h.da_pi for h in T.halves]
    dg = [h.da - h.da_pi for h in T.halves]
    seq = [cf.capex_anchor] + [r.capex for r in res.rows]
    diffs = []
    for j in range(1, 2 * H + 3):
        n, h = divmod(j - 1, 2)
        k = N + j
        base = ctx.da_runoff[k - 1] if k - 1 < len(ctx.da_runoff) else 0.0
        rule = cf.da_anchor * base + math.fsum(seq[k - min(k, H):k]) / H
        steady = dg[h] * (1 + g) ** n + dp[h] * (1 + pi) ** n
        diffs.append(rule - steady)
        seq.append(cg[h] * (1 + g) ** n + cp[h] * (1 + pi) ** n)
    scale = max(abs(h.da) for h in T.halves)
    assert all(abs(d) <= 1e-12 * scale for d in diffs[H:]), diffs[H:]
    assert any(abs(d) > 1e-6 * scale for d in diffs[:H])
    # D&A первого полугодия терминала по правилу когорт продолжает D&A явного участка без
    # скачка вниз при растущем capex
    first_rule = diffs[0] + T.halves[0].da
    assert first_rule >= res.rows[-1].da * 0.98
