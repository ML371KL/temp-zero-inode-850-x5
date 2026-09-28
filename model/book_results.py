"""Таблицы книги — `results.json` и `run_output.txt` — расчётом ядра.

`python -B -m model.book_results [--book ФАЙЛ] [--facts КАТАЛОГ] [--out КАТАЛОГ]`
считает сетку на входах книги (`meta.valuation_date`, `meta.market_price`) и
пишет оба файла (по умолчанию в `data/assumptions/`, перевод строк LF):

* `results.json` — слои, точка, «только этот мир», режимы, 36 клеток,
  требования, ожидаемый путь слоя «свой взгляд», гейты (масса), инварианты;
* раздел `band` — полоса §9 на `valuation.uncertainty.draws` прогонах (квантили,
  среднее, P(центр < рынка), вклады осей), «суждения по цене ошибки»,
  обратный DCF §11 и «что даст отчёт» §12 — те же функции, что у выпуска
  (`model.uncertainty.distribution`, прогоны округлены до 0,1 ₽, как в выпуске);
* `run_output.txt` — те же числа для чтения глазами; печатается из
  `results.json` без нового расчёта (`render_run_output`).

Файлы — функция книги, фактов и кода: объяснения гейтов и «сегодня» в них не
входят. Свежий расчёт обязан воспроизвести файл (rel 1e-12, пол 1e-10 —
`tests/test_book_results.py`; раздел `band` — долгий тест `ci_only`).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from model.book import BOOK_DIR, ROOT, book_warnings, default_book_path, load_book, open_period
from model.book_schema import REGIMES
from model.checks import gate_masses, invariants, round_to_step
from model.facts import Facts, core_facts, default_facts_dir, load_facts
from model.grid import Grid, annual_path, evaluate, expected_path
from model.uncertainty import distribution

# Прогоны полосы — до 0,1 ₽, как в выпуске (model/payload.py: DRAW_DECIMALS).
DRAW_DECIMALS = 1

SCHEMA = "x5-book-results-1"


def _num(x: Any) -> Any:
    """Число для JSON: неконечное — null (инвариант `finite` его уже назвал)."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return _num(obj)


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _layer(L) -> dict:
    return dict(title=L.title, world_weights=L.world_weights, v0=L.v0, d=L.d, equity=L.equity,
                price=L.price, pv_fcff=L.pv_fcff, pv_shield=L.pv_shield,
                pv_terminal=L.pv_terminal, pv_issuance=L.pv_issuance,
                pv_excess_spread=L.pv_excess_spread, pv_buffer_carry=L.pv_buffer_carry,
                treasury_value=L.treasury_value, terminal_share=L.terminal_share,
                ev_ebitda_fwd=L.ev_ebitda_fwd, v0_to_d=L.v0_to_d)


def _cell(ctx, c) -> dict:
    r, cell = c.result, c.result.cell
    return dict(world=cell.world, regime=cell.regime, capex=cell.capex, growth=cell.growth,
                credit=cell.credit, demand=cell.demand, p_analytical=c.p["analytical"],
                p_market_implied=c.p["market_implied"], p_neutral=c.p["macro_neutral"],
                ev=r.ev, pv_fcff=r.pv_fcff, pv_shield=r.pv_shield, pv_terminal=r.pv_terminal,
                pv_issuance=r.pv_issuance, pv_excess_spread=r.pv_excess_spread,
                pv_buffer_carry=r.pv_buffer_carry, tv_da_transition=r.terminal.tv_da_transition,
                terminal_debt_rate=r.terminal.debt_rate,
                terminal_share=r.terminal_share, d=r.claims.total, equity=r.equity,
                price=r.price, ebitda_ntm=r.ebitda_ntm, ev_ebitda_fwd=r.ev_ebitda_fwd,
                margin_lt=ctx.regime(cell.regime).target_lt, terminal_growth=r.terminal.growth,
                r_terminal=r.terminal.rate, max_leverage=r.max_leverage,
                margin_min=r.margin_min, margin_max=r.margin_max)


def band_results(A: dict, facts: Facts, grid: Grid, *, n_workers: int | None = None) -> dict:
    """Раздел `band`: полоса §9, суждения, обратный DCF §11, «что даст отчёт» §12."""
    cf = core_facts(facts, A)
    dist = distribution(A, cf, grid, period=open_period(A), n_workers=n_workers,
                        round_draws=DRAW_DECIMALS)
    step = float(A["valuation"]["headline"]["print_step"])
    S = dist.stats
    ctx = grid.ctx
    v0_med = ctx.v0_of(S["median"], grid.layers["analytical"].d)
    return {"draws": dist.band.n, "seed": dist.band.seed, "lambda": dist.band.lam,
            "market_price": dist.band.market_price, "stats": S,
            "printed": {k: round_to_step(S[k], step) for k in ("p10", "p25", "median", "p75", "p90")},
            "center_ev": {"v0_median": v0_med, "gap_median": v0_med / grid.point.v_star - 1.0,
                          "rub_per_1pct_ev_median": ctx.rub_per_1pct(v0_med)},
            "contributions": dist.contributions,
            "judgements": sorted(dist.judgements, key=lambda j: -j["swing"]),
            "subsample": dist.subsample, "delta": dist.delta,
            "reverse_dcf": dist.reverse_dcf, "next_report": dist.next_report}


def book_results(A: dict | None = None, facts: Facts | None = None, *,
                 book_path: Path | None = None, band: bool = True,
                 n_workers: int | None = None) -> dict:
    """Словарь таблиц книги на её входах; `band=False` — без раздела полосы."""
    if A is None:
        book_path = book_path or default_book_path()
        A = load_book(book_path)
    facts = facts if facts is not None else load_facts()
    grid: Grid = evaluate(A, facts)
    ctx, T = grid.ctx, grid.ctx.timing
    step = float(A["valuation"]["headline"]["print_step"])
    P = grid.point
    an = grid.layers["analytical"]
    rolled = math.fsum(c.p["analytical"] * c.result.claims.rolled for c in grid.cells)
    halves = expected_path(grid)
    extra = {"band": band_results(A, facts, grid, n_workers=n_workers)} if band else {}
    return _clean({
        "schema": SCHEMA,
        "book": {"version": A["meta"]["version"], "date": A["meta"]["date"],
                 "file": _rel(book_path) if book_path else None,
                 "worlds_source": A["worlds"]["source"]},
        "facts": {"dir": _rel(facts.root), "fixture": facts.fixture},
        "inputs": {"valuation_date": T.valuation_date.isoformat(),
                   "market_price": ctx.market_price, "curve_as_of": T.curve_as_of.isoformat(),
                   "facts_date": A["meta"]["facts_date"], "anchor_period": ctx.anchor,
                   "closed_periods": T.closed, "elapsed": T.elapsed, "roll_years": T.roll,
                   "t_end": T.t_end, "shares_mln": ctx.facts.shares_mln,
                   "treasury_mln": ctx.treasury_mln,
                   "treasury_sale_price_k": float(A["valuation"]["treasury_sale_price_k"]),
                   "treasury_value": ctx.treasury_value,
                   "governance_discount": ctx.governance, "lambda": P.lam,
                   "print_step": step},
        "warnings": book_warnings(A),
        "regimes": {
            "prior": grid.regime_prior, "posterior": grid.regime_posterior,
            "steps": [dict(period=s.period, value=s.value, se=s.se, likelihood=s.likelihood,
                           prior=s.prior, posterior=s.posterior) for s in grid.regime_steps],
            "margin_anchor_fact": ctx.facts.margin_anchor,
            "deviation_anchor": {r: ctx.regime(r).deviation_anchor for r in REGIMES},
            "target_lt": {r: ctx.regime(r).target_lt for r in REGIMES}},
        "layers": {name: _layer(L) for name, L in grid.layers.items()},
        "point": {"low": P.low, "central": P.central, "high": P.high, "lambda": P.lam,
                  "rates_view": P.rates_view,
                  "printed": {k: round_to_step(v, step)
                              for k, v in (("low", P.low), ("central", P.central),
                                           ("high", P.high))},
                  "v0_point": P.v0_point, "v_star": P.v_star, "gap_point": P.gap_point,
                  "rub_per_1pct_ev_point": P.rub_per_1pct_ev_point,
                  "equity_share_of_ev": P.equity_share_of_ev,
                  "market_price": ctx.market_price},
        "worlds_only": {w: {**_layer(L), "r_terminal": ctx.world(w).r_terminal,
                            "lt_inflation": ctx.world(w).pi_lt,
                            "real_terminal": ctx.world(w).r_terminal - ctx.world(w).pi_lt}
                        for w, L in grid.worlds_only.items()},
        "claims": {"net_debt_fact": ctx.facts.net_debt, "opcash_anchor": ctx.opcash_anchor,
                   "bridge": [dict(key=b.key, label=b.label, amount=b.amount,
                                   included=b.included) for b in ctx.facts.bridge],
                   "bridge_total": ctx.bridge_total, "rolled_analytical": rolled,
                   "dividends_declared": ctx.dividends_declared, "d_analytical": an.d},
        "cells": [_cell(ctx, c) for c in grid.cells],
        "paths": {"halves": halves, "annual": annual_path(grid, halves)},
        "gates": [dict(name=g.name, title=g.title, fired=g.fired, mass=g.mass,
                       cells=list(g.cells), detail=g.detail) for g in gate_masses(grid)],
        "invariants": [dict(name=i.name, ok=i.ok, detail=i.detail) for i in invariants(grid)],
        **extra,
    })


# ------------------------------------------------------------------- печать


def _f(x: Any, nd: int = 1) -> str:
    """Число с разделителем тысяч; None — прочерк."""
    return "—" if x is None else f"{x:,.{nd}f}".replace(",", " ")


def _g(x: Any, spec: str) -> str:
    """Число по формату; None — прочерк."""
    return "—" if x is None else format(x, spec)


def render_run_output(res: dict) -> str:
    """Текст `run_output.txt` из словаря `book_results` (без нового расчёта)."""
    I, B = res["inputs"], res["book"]
    out = [f"Книга допущений X5 {B['version']} от {B['date']} ({B['file']})",
           f"Факты: {res['facts']['dir']}" + ("  — ФИКСТУРА ТЕСТОВ, не выверенные факты"
                                               if res["facts"]["fixture"] else ""),
           f"Дата оценки {I['valuation_date']}, цена рынка {_f(I['market_price'])} ₽, "
           f"кривые миров на {I['curve_as_of']}, факты на {I['facts_date']} (якорь {I['anchor_period']})",
           f"Сетка: закрыто полугодий {I['closed_periods']}, прошло {I['elapsed']:.4f} текущего; "
           f"перекат Δ = {I['roll_years']:.4f} года; срок терминала {I['t_end']:.4f} года",
           f"Акции в обращении {I['shares_mln']:.6f} млн, казначейские {I['treasury_mln']:.6f} млн "
           f"(продажа по {I['treasury_sale_price_k']:.4f} рынка = {_f(I['treasury_value'], 3)} млрд ₽); "
           f"дисконт за управление {I['governance_discount']:.4f}; λ = {I['lambda']:.2f}; "
           f"шаг печати {_f(I['print_step'], 0)} ₽",
           ""]
    if res["warnings"]:
        out.append("Замечания к книге (правило §0.1 читает так):")
        out += [f"  - {w}" for w in res["warnings"]]
        out.append("")
    R = res["regimes"]
    out.append(f"Режимы маржи: факт маржи якоря {R['margin_anchor_fact']:.5f}")
    out.append("  режим      априорная  после набл.  цель LT   отклонение на якоре")
    for r in REGIMES:
        out.append(f"  {r:<9}  {R['prior'][r]:>9.4f}  {R['posterior'][r]:>11.4f}  "
                   f"{R['target_lt'][r]:>7.4f}   {R['deviation_anchor'][r]:>+.5f}")
    for s in R["steps"]:
        out.append(f"  наблюдение {s['period']}: {s['value']:.4f} (se {s['se']:.4f})")
    out.append("")
    out.append("Слои (млрд ₽; цена — ₽/акция)")
    out.append("  слой                        V0         D   капитал      цена   PV FCFF  PV щита"
               "  PV терм  доля терм  EV/EBITDA   V0/D")
    for name, L in res["layers"].items():
        out.append(f"  {L['title']:<24} {_f(L['v0']):>9} {_f(L['d']):>9} {_f(L['equity']):>9} "
                   f"{_f(L['price']):>9} {_f(L['pv_fcff']):>9} {_f(L['pv_shield']):>8} "
                   f"{_f(L['pv_terminal']):>8} {_g(L['terminal_share'], '>10.4f')} "
                   f"{_f(L['ev_ebitda_fwd'], 2):>10} {_f(L['v0_to_d'], 2):>6}")
    out.append("  вычеты из EV (PV, млрд ₽)   издержки размещения  сверх справедл. спреда"
               "  кэрри подушки")
    for name, L in res["layers"].items():
        out.append(f"  {L['title']:<24} {_f(L['pv_issuance'], 2):>22} {_f(L['pv_excess_spread'], 2):>23} "
                   f"{_f(L['pv_buffer_carry'], 2):>14}")
    P = res["point"]
    out += ["",
            f"Точка: низ {_f(P['low'])} · точка {_f(P['central'])} · верх {_f(P['high'])} ₽ "
            f"(печать {_f(P['printed']['low'], 0)} / {_f(P['printed']['central'], 0)} / "
            f"{_f(P['printed']['high'], 0)})",
            f"  вклад взгляда на инфляцию и ставки (верх − низ): {_f(P['rates_view'])} ₽",
            f"  EV точки {_f(P['v0_point'])} против V* {_f(P['v_star'])} млрд ₽: разрыв "
            f"{_g(P['gap_point'], '+.4f')}; цена 1 % EV {_f(P['rub_per_1pct_ev_point'])} ₽; "
            f"капитал / EV {_g(P['equity_share_of_ev'], '.4f')}",
            "", "Только этот мир"]
    for w, L in res["worlds_only"].items():
        out.append(f"  {w}: цена {_f(L['price']):>9} ₽, V0 {_f(L['v0'])}, D {_f(L['d'])}; "
                   f"r терминала {L['r_terminal']:.4f}, π LT {L['lt_inflation']:.4f}, "
                   f"реальная {L['real_terminal']:.4f}")
    C = res["claims"]
    out += ["", "Требования D на дату оценки (слой «свой макро-взгляд»)",
            f"  чистый долг (факт)            {_f(C['net_debt_fact'], 3):>10}",
            f"  операционная касса якоря      {_f(C['opcash_anchor'], 3):>10}"]
    for b in C["bridge"]:
        mark = "" if b["included"] else "  (вне моста)"
        out.append(f"  {b['label']:<30}{_f(b['amount'], 3):>10}{mark}")
    out += [f"  − перекат (денежный результат) {_f(-C['rolled_analytical'], 3):>9}",
            f"  дивиденды с отсечкой ≤ даты   {_f(C['dividends_declared'], 3):>10}",
            f"  итого D                       {_f(C['d_analytical'], 3):>10}", ""]
    out.append("Клетки (P — вероятность в слое «свой взгляд»)")
    out.append("  клетка               P         EV        D   капитал      цена  EV/EBITDA  доля терм"
               "  рычаг max  g терм")
    for c in res["cells"]:
        key = f"{c['world']}|{c['regime']}|{c['capex']}"
        out.append(f"  {key:<18} {c['p_analytical']:.4f} {_f(c['ev']):>9} {_f(c['d']):>8} "
                   f"{_f(c['equity']):>9} {_f(c['price']):>9} {_f(c['ev_ebitda_fwd'], 2):>10} "
                   f"{_g(c['terminal_share'], '>10.4f')} {_f(c['max_leverage'], 2):>10} "
                   f"{_g(c['terminal_growth'], '>7.4f')}")
    out += ["", "Ожидаемый путь слоя «свой макро-взгляд», годы (млрд ₽)",
            "   год    выручка  маржа   EBITDA    capex  capex/R     FCFF     щит  проценты"
            "  дивиденды       ЧД  ЧД/EBITDA"]
    for a in res["paths"]["annual"]:
        mark = "*" if a["fact"] else " "
        out.append(f"  {a['year']}{mark} {_f(a['revenue']):>9} {_g(a['margin'], '.4f')} {_f(a['adj_ebitda']):>8} "
                   f"{_f(a['capex']):>8} {_g(a['capex_pct'], '>8.4f')} {_f(a['fcff']):>8} "
                   f"{_f(a['shield']):>7} {_f(a['interest']):>9} {_f(a['dividends']):>10} "
                   f"{_f(a['net_debt']):>8} {_g(a['leverage'], '>10.2f')}")
    out.append("  * — год якоря: выручка, EBITDA, D&A, capex включают факт якоря; "
               "FCFF, щит, проценты, дивиденды — только прогноз")
    out += ["", "Гейты (масса — вероятность клеток слоя «свой взгляд»)"]
    for g in res["gates"]:
        state = "СРАБОТАЛ" if g["fired"] else "нет"
        out.append(f"  {g['name']:<15} {state:<9} масса {g['mass']:.4f}  клеток {len(g['cells']):>2}"
                   + (f"  {g['detail']}" if g["fired"] else ""))
    out += ["", "Инварианты"]
    for i in res["invariants"]:
        out.append(f"  {i['name']:<15} {'ок' if i['ok'] else 'НАРУШЕН'}  {i['detail']}")
    if "band" in res:
        out += render_band(res["band"])
    return "\n".join(out) + "\n"


def _v(x: Any, kind: str) -> str:
    """Значение суждения: сдвиг — п.п., веса — словарём, прочее — числом."""
    if isinstance(x, dict):
        return "/".join(f"{k} {v:.2f}" for k, v in x.items())
    if x is None:
        return "—"
    return f"{x * 100:+.2f} п.п." if kind == "shift" else f"{x:.4g}"


def render_band(B: dict) -> list[str]:
    """Раздел «полоса» run_output.txt."""
    S, P = B["stats"], B["printed"]
    out = ["", f"Полоса по суждениям книги (§9): {B['draws']} прогонов, seed {B['seed']}, "
               f"λ = {B['lambda']:.2f}; прогоны до 0,1 ₽",
           f"  медиана {_f(S['median'])} ₽ (печать {_f(P['median'], 0)}); P10–P90 "
           f"{_f(S['p10'])}–{_f(S['p90'])} (печать {_f(P['p10'], 0)}–{_f(P['p90'], 0)}); "
           f"P25–P75 {_f(S['p25'])}–{_f(S['p75'])}",
           f"  среднее {_f(S['mean'])} ₽; P(центр < рынка {_f(B['market_price'])} ₽) = "
           f"{S['p_below']:.4f}",
           f"  EV медианы {_f(B['center_ev']['v0_median'])} млрд ₽, разрыв к V* "
           f"{B['center_ev']['gap_median']:+.4f}; цена 1 % EV "
           f"{_f(B['center_ev']['rub_per_1pct_ev_median'])} ₽",
           "", "Вклады осей в полосу (доля квадрата ранговой корреляции с центром)"]
    for c in sorted(B["contributions"], key=lambda c: -c["share"]):
        out.append(f"  {c['share']:.4f}  ρ {c['rank_corr']:+.3f}  {c['axis']}")
    out += ["", "Суждения по цене ошибки: точка при low и high оси (остальные — книга)",
            "   при low   при high    размах  ось (книга; low … high)"]
    for j in B["judgements"]:
        out.append(f"  {_f(j['price_low']):>8} {_f(j['price_high']):>9} {_f(j['swing']):>9}  "
                   f"{j['name']} ({_v(j['book'], j['kind'])}; {_v(j['low'], j['kind'])} … "
                   f"{_v(j['high'], j['kind'])})")
    out += ["", f"Обратный DCF (§11): медиана = рынку {_f(B['market_price'])} ₽; поиск на подвыборке "
                f"{B['subsample']} прогонов со сдвигом δ = {B['delta']:+.2f} ₽, уточнение секущей "
                "на полной полосе",
            "  ось: решение для медианы (невязка полной полосы; поиск) | для точки | диапазон книги"]
    for r in B["reverse_dcf"]:
        where = "внутри диапазона" if r["in_range"] else "вне диапазона"
        med = ("недостижимо в поиске" if r["solved"] is None else
               f"{r['solved']:.6g} ({where}; невязка {r['gap_full']:+.1f} ₽; поиск "
               f"{r['search_value']:.6g})")
        pt = "недостижимо" if r["point_solved"] is None else f"{r['point_solved']:.6g}"
        out.append(f"  {r['name']}: {med} | {pt} | {r['range'][0]:g} … {r['range'][1]:g} "
                   f"(книга {r['book']:g}; поиск {r['search'][0]:g} … {r['search'][1]:g})")
    N = B["next_report"]
    out += ["", f"Что даст отчёт (§12): открытое полугодие {N['period']}"]
    if N["table"]:
        out.append("   маржа      точка   Δточки    медиана  Δмедианы   stress  floor  partial  full")
        for r in N["table"]:
            q = r["posterior"]
            out.append(f"  {r['margin']:.4f} {_f(r['point']):>10} {r['d_point']:>+8.1f} "
                       f"{_f(r['median']):>10} {r['d_median']:>+9.1f}   {q['stress']:.3f}  "
                       f"{q['floor']:.3f}  {q['partial']:.3f}    {q['full']:.3f}")
        nm, npnt = N["neutral"]["median"], N["neutral"]["point"]
        gap = N["neutral_gap"]["median"]
        out.append(f"  медианы — полной полосы; нейтральная маржа: медианы {_g(nm, '.5f')} "
                   f"(невязка {_g(gap, '+.1f')} ₽), точки {_g(npnt, '.5f')}; "
                   f"медиана на 0,1 п.п. маржи {_f(N['rub_per_01pp'])} ₽")
    return out


def write(res: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, txt_path = out_dir / "results.json", out_dir / "run_output.txt"
    json_path.write_text(json.dumps(res, ensure_ascii=False, indent=1, allow_nan=False) + "\n",
                         encoding="utf-8", newline="\n")
    txt_path.write_text(render_run_output(res), encoding="utf-8", newline="\n")
    return json_path, txt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Таблицы книги X5 расчётом ядра")
    parser.add_argument("--book", type=Path, default=None, help="файл книги (YAML)")
    parser.add_argument("--facts", type=Path, default=None, help="каталог фактов")
    parser.add_argument("--out", type=Path, default=BOOK_DIR, help="куда писать")
    parser.add_argument("--no-band", action="store_true", help="без раздела полосы (быстро)")
    args = parser.parse_args(argv)
    book_path = args.book or default_book_path()
    facts = load_facts(args.facts) if args.facts else load_facts(default_facts_dir())
    res = book_results(load_book(book_path), facts, book_path=book_path, band=not args.no_band)
    paths = write(res, args.out)
    for p in paths:
        print(f"записано: {_rel(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
