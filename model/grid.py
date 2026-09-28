"""Сетка 36 клеток, вероятности режимов (A-P2u), слои, точка и цены (docs/MODEL.md §3, §7–§10).

* Вероятность клетки в слое L: P_L(W) × P(ρ | наблюдения) × P(C | ρ) (§3).
  Вероятности режимов после A-P2u — одни во всех мирах и слоях.
* Слой — набор весов миров на ОДНИХ И ТЕХ ЖЕ клетках (§8): «свой макро-взгляд»
  (`joint.world_prob`), «вменённые рынком» (`joint.market_implied_prob`),
  «рыночные ставки как есть» (100 % `joint.neutral_world`).
* Цена слоя — внутренняя стоимость (§7.2) с казначейским пакетом:
  [(V0 − D) + n·k·P_рынок/1000]·(1 − g_gov)·1000/(N + n) при положительном
  капитале с пакетом, иначе без (1 − g_gov).
* Точка = низ + λ·(верх − низ), низ — «рыночные ставки как есть», верх —
  «свой макро-взгляд» (§8).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from model.book import period_index
from model.book_schema import CAPEX_LEVELS, REGIMES, WORLDS
# v0_from_price и rub_per_1pct_ev живут в ядре рядом с ценой; здесь — для прежних импортов.
from model.core import (CellResult, Context, observations, run_cell, rub_per_1pct_ev,  # noqa: F401
                        v0_from_price)
from model.facts import CoreFacts, Facts

fsum = math.fsum

LAYERS = ("analytical", "market_implied", "macro_neutral")
LAYER_TITLES = {"analytical": "свой макро-взгляд",
                "market_implied": "вменённые рынком",
                "macro_neutral": "рыночные ставки как есть"}
# Допуск сравнения при обрезке сдвига вероятностей (A-P2u, §10).
CAP_TOL = 1e-12


# --------------------------------------------------------- A-P2u (§10)


def cap_shift(prior: dict, posterior: dict, cap: float) -> dict:
    """Предел сдвига вероятностей за одно наблюдение (§10).

    Сдвиг каждого режима обрезается до ±cap, вероятности перенормируются; если
    перенормировка вывела сдвиг за предел — весь вектор сдвигов сжимается
    пропорционально до максимального |сдвига| = cap.
    """
    out = {r: prior[r] + max(-cap, min(cap, posterior[r] - prior[r])) for r in prior}
    total = fsum(out.values())
    out = {r: v / total for r, v in out.items()}
    worst = max(abs(out[r] - prior[r]) for r in prior)
    if worst > cap + CAP_TOL:
        out = {r: prior[r] + cap / worst * (out[r] - prior[r]) for r in prior}
    return out


@dataclass(frozen=True)
class RegimeUpdate:
    """Шаг A-P2u: наблюдение, правдоподобия, вероятности до и после."""

    period: str
    value: float
    se: float
    likelihood: dict
    prior: dict
    posterior: dict


def regime_updates(ctx: Context) -> tuple[dict, list[RegimeUpdate]]:
    """Вероятности режимов после всех наблюдений и шаги обновления (§10).

    Состояние фильтра режима (m, v): до первого наблюдения m = отклонение на
    якоре (факт), v = 0. Для наблюдения: dev = obs − цель − сезонность,
    ошибка = dev − ρ^k·m, P = σ²(1 − ρ^{2k}) + ρ^{2k}·v,
    L = exp(−½·ошибка²/(P + se²)); апостериорные ∝ априорные × L; предел
    сдвига — после каждого наблюдения.
    """
    A = ctx.A
    rho = float(A["margin"]["deviation_persistence"])
    update = A["joint"]["regime_update"]
    sigma2 = float(update["sigma_pp"]) ** 2
    cap = float(update["cap_pp"])
    current = {r: float(A["joint"]["regime_prob"][r]) for r in REGIMES}
    state = {}
    for r in REGIMES:
        RG = ctx.regime(r)
        state[r] = [RG.deviation_anchor, 0.0, period_index(ctx.anchor)]
    steps = []
    for p, value, se in observations(A):
        i = period_index(p) - period_index(ctx.P[0])
        loglik, new_state = {}, {}
        for r in REGIMES:
            RG = ctx.regime(r)
            m, v, last = state[r]
            k = period_index(p) - last
            dev = value - RG.target[i] - RG.season[i]
            error = dev - rho ** k * m
            var = sigma2 * (1.0 - rho ** (2 * k)) + rho ** (2 * k) * v
            loglik[r] = -0.5 * error * error / (var + se * se)
            if se == 0:
                new_state[r] = [dev, 0.0, period_index(p)]
            else:
                w = var / (var + se * se)
                new_state[r] = [rho ** k * m + w * error, (1.0 - w) * var, period_index(p)]
        top = max(loglik.values())
        like = {r: math.exp(loglik[r] - top) for r in REGIMES}
        norm = fsum(current[r] * like[r] for r in REGIMES)
        posterior = {r: current[r] * like[r] / norm for r in REGIMES}
        capped = cap_shift(current, posterior, cap)
        steps.append(RegimeUpdate(period=p, value=value, se=se, likelihood=like,
                                  prior=dict(current), posterior=capped))
        current, state = capped, new_state
    return current, steps


# ------------------------------------------------------------------ сетка


@dataclass(frozen=True)
class GridCell:
    result: CellResult
    p: dict                     # слой → вероятность клетки

    @property
    def key(self) -> str:
        return self.result.cell.key


@dataclass(frozen=True)
class LayerResult:
    """Слой (§7.2, §8): V0 = Σ P·EV, D = Σ P·D, цена по внутренней стоимости.

    Разложение EV: V0 = pv_fcff + pv_shield + pv_terminal − pv_issuance −
    pv_excess_spread − pv_buffer_carry (`ev_parts`). Цена: капитал `equity` = V0 − D
    плюс выручка от продажи казначейского пакета `treasury_value`.
    """

    name: str
    title: str
    world_weights: dict
    v0: float
    d: float
    equity: float
    price: float
    pv_fcff: float
    pv_shield: float
    pv_terminal: float
    terminal_share: float
    ev_ebitda_fwd: float | None
    v0_to_d: float | None
    pv_issuance: float = 0.0
    pv_excess_spread: float = 0.0
    pv_buffer_carry: float = 0.0
    treasury_value: float = 0.0

    @property
    def ev_parts(self) -> tuple:
        """Строки EV со знаком вклада (ключ, млрд ₽); сумма — V0."""
        return (("pv_fcff", self.pv_fcff), ("pv_shield", self.pv_shield),
                ("pv_terminal", self.pv_terminal), ("pv_issuance", -self.pv_issuance),
                ("pv_excess_spread", -self.pv_excess_spread),
                ("pv_buffer_carry", -self.pv_buffer_carry))


@dataclass(frozen=True)
class Point:
    """Точка при центральных значениях суждений (§8) и сравнение с рынком (§7.3)."""

    low: float
    high: float
    central: float
    lam: float
    rates_view: float
    v0_point: float
    v_star: float
    gap_point: float
    rub_per_1pct_ev_point: float
    equity_share_of_ev: float


@dataclass(frozen=True)
class Grid:
    ctx: Context
    cells: tuple                # GridCell в порядке мир × режим × capex
    regime_prior: dict
    regime_posterior: dict
    regime_steps: tuple
    layers: dict                # имя → LayerResult
    worlds_only: dict           # мир → LayerResult «только этот мир»
    point: Point

    def cell(self, world: str, regime: str, capex: str) -> GridCell:
        key = f"{world}|{regime}|{capex}"
        return next(c for c in self.cells if c.key == key)


def layer_world_weights(A: dict) -> dict:
    """Веса миров трёх слоёв (§8)."""
    J = A["joint"]
    return {"analytical": {w: float(J["world_prob"][w]) for w in WORLDS},
            "market_implied": {w: float(J["market_implied_prob"][w]) for w in WORLDS},
            "macro_neutral": {w: 1.0 if w == J["neutral_world"] else 0.0 for w in WORLDS}}


def layer_of(ctx: Context, name: str, weights: dict, cells: list, regime_p: dict) -> LayerResult:
    """Слой на клетках `cells` (CellResult) с весами миров `weights`."""
    A, cf = ctx.A, ctx.facts
    capex_p = A["joint"]["capex_prob_given_regime"]
    probs = [weights[c.cell.world] * regime_p[c.cell.regime]
             * float(capex_p[c.cell.regime][c.cell.capex]) for c in cells]

    def mean(attr) -> float:
        return fsum(p * attr(c) for p, c in zip(probs, cells) if p)

    v0 = mean(lambda c: c.ev)
    d = mean(lambda c: c.claims.total)
    pv_terminal = mean(lambda c: c.pv_terminal)
    terminal_net = mean(lambda c: c.terminal_share * c.ev)
    ntm = mean(lambda c: c.ebitda_ntm)
    equity = v0 - d
    return LayerResult(
        name=name, title=LAYER_TITLES.get(name, name), world_weights=dict(weights), v0=v0, d=d,
        equity=equity, price=ctx.price_of(equity),
        pv_fcff=mean(lambda c: c.pv_fcff), pv_shield=mean(lambda c: c.pv_shield),
        pv_terminal=pv_terminal, terminal_share=terminal_net / v0 if v0 else math.inf,
        ev_ebitda_fwd=v0 / ntm if ntm > 0 else None, v0_to_d=v0 / d if d > 0 else None,
        pv_issuance=mean(lambda c: c.pv_issuance),
        pv_excess_spread=mean(lambda c: c.pv_excess_spread),
        pv_buffer_carry=mean(lambda c: c.pv_buffer_carry), treasury_value=ctx.treasury_value)


def point_of(ctx: Context, layers: dict, lam: float | None = None) -> Point:
    """Точка = низ + λ(верх − низ) и её EV против рыночного V* (§7.3, §8)."""
    lam = float(ctx.A["joint"]["lambda"]) if lam is None else float(lam)
    low, high = layers["macro_neutral"].price, layers["analytical"].price
    central = low + lam * (high - low)
    d = layers["analytical"].d
    v0_point = ctx.v0_of(central, d)
    v_star = ctx.v0_of(ctx.market_price, d)
    return Point(low=low, high=high, central=central, lam=lam, rates_view=high - low,
                 v0_point=v0_point, v_star=v_star, gap_point=v0_point / v_star - 1.0,
                 rub_per_1pct_ev_point=ctx.rub_per_1pct(v0_point),
                 equity_share_of_ev=(v0_point - d) / v0_point if v0_point else math.nan)


def evaluate(A: dict, facts: Facts | CoreFacts, *, valuation_date: dt.date | str | None = None,
             market_price: float | None = None, lam: float | None = None) -> Grid:
    """Вся сетка книги на дату оценки: 36 клеток, слои, «только этот мир», точка."""
    ctx = Context(A, facts, valuation_date=valuation_date, market_price=market_price)
    return evaluate_context(ctx, lam=lam)


def evaluate_context(ctx: Context, lam: float | None = None) -> Grid:
    A = ctx.A
    posterior, steps = regime_updates(ctx)
    results = [run_cell(ctx, ctx.cell(w, r, c))
               for w in WORLDS for r in REGIMES for c in CAPEX_LEVELS]
    weights = layer_world_weights(A)
    layers = {name: layer_of(ctx, name, weights[name], results, posterior) for name in LAYERS}
    capex_p = A["joint"]["capex_prob_given_regime"]
    cells = tuple(GridCell(result=c, p={name: weights[name][c.cell.world] * posterior[c.cell.regime]
                                        * float(capex_p[c.cell.regime][c.cell.capex])
                                        for name in LAYERS}) for c in results)
    only = {w: layer_of(ctx, f"world:{w}", {x: 1.0 if x == w else 0.0 for x in WORLDS}, results,
                        posterior) for w in WORLDS}
    return Grid(ctx=ctx, cells=cells,
                regime_prior={r: float(A["joint"]["regime_prob"][r]) for r in REGIMES},
                regime_posterior=posterior, regime_steps=tuple(steps), layers=layers,
                worlds_only=only, point=point_of(ctx, layers, lam))


def layer_prices(A: dict, facts: Facts | CoreFacts, *, valuation_date=None,
                 market_price=None) -> tuple[float, float]:
    """(низ, верх) — цены слоёв «рыночные ставки как есть» и «свой макро-взгляд».

    Короткий путь для прогонов полосы (§9): те же клетки и слои, без «только
    этот мир» и точки.
    """
    ctx = Context(A, facts, valuation_date=valuation_date, market_price=market_price)
    posterior, _ = regime_updates(ctx)
    weights = layer_world_weights(A)
    need = {w for name in ("macro_neutral", "analytical") for w in WORLDS if weights[name][w]}
    results = [run_cell(ctx, ctx.cell(w, r, c))
               for w in WORLDS if w in need for r in REGIMES for c in CAPEX_LEVELS]
    low = layer_of(ctx, "macro_neutral", weights["macro_neutral"], results, posterior).price
    high = layer_of(ctx, "analytical", weights["analytical"], results, posterior).price
    return low, high


# ------------------------------------------------------- ожидаемый путь слоя

# Поля строки, которые складываются по вероятностям клеток (средние по слою).
PATH_FIELDS = ("revenue", "ticket", "traffic", "area_end", "opened", "closed", "margin",
               "adj_ebitda", "lti", "ebitda_rep", "da", "ebit", "capex", "capex_maintenance",
               "capex_growth", "capex_infra", "nwc", "nwc_change", "opcash", "opcash_change",
               "lease", "proceeds", "tax_unlevered", "tax_actual", "shield", "fcff", "debt_rate",
               "interest", "issuance_cost", "excess_spread", "buffer_carry", "dividends",
               "net_debt", "ebitda_rep_ltm")
FLOW_FIELDS = ("revenue", "adj_ebitda", "lti", "ebitda_rep", "da", "capex", "capex_maintenance",
               "capex_growth", "capex_infra", "nwc_change", "opcash_change", "lease",
               "proceeds", "tax_unlevered", "tax_actual", "shield", "fcff", "interest",
               "issuance_cost", "excess_spread", "buffer_carry", "dividends", "opened", "closed")


def expected_path(grid: Grid, layer: str = "analytical") -> list[dict]:
    """Ожидаемый путь слоя по полугодиям: средние строк клеток по их вероятностям.

    Маржа и рычаг — отношения средних (E[EBITDA]/E[R], E[ND]/E[LTM]), а не
    средние отношений.
    """
    cells = [c for c in grid.cells if c.p[layer]]
    out = []
    for i, p in enumerate(grid.ctx.P):
        row = {"period": p}
        for name in PATH_FIELDS:
            row[name] = fsum(c.p[layer] * getattr(c.result.rows[i], name) for c in cells)
        row["margin"] = row["adj_ebitda"] / row["revenue"]
        # долг компании: ND модели несёт прирост операционной кассы (§4.9)
        row["net_debt_company"] = row["net_debt"] - (row["opcash"] - grid.ctx.opcash_anchor)
        row["leverage"] = row["net_debt_company"] / row["ebitda_rep_ltm"]
        out.append(row)
    return out


def annual_path(grid: Grid, halves: list[dict]) -> list[dict]:
    """Годы ожидаемого пути: потоки — суммы полугодий, запасы — на конец года.

    Год якоря (первый прогнозный год, если якорь — его первое полугодие) —
    факт якоря + прогноз: выручка, скорр. EBITDA, LTI, D&A, capex; поле `fact`
    называет, какие строки года частично факт.
    """
    cf = grid.ctx.facts
    years: dict[int, dict] = {}
    for row in halves:
        y = int(row["period"][:4])
        acc = years.setdefault(y, {"year": y, "halves": 0, "fact": []})
        acc["halves"] += 1
        for name in FLOW_FIELDS:
            acc[name] = acc.get(name, 0.0) + row[name]
        for name in ("area_end", "net_debt", "net_debt_company", "ebitda_rep_ltm", "nwc", "opcash"):
            acc[name] = row[name]
    if cf.anchor[:4] in {str(y) for y in years}:
        acc = years[int(cf.anchor[:4])]
        acc["revenue"] += cf.revenue[cf.anchor]
        acc["adj_ebitda"] += cf.adj_ebitda[cf.anchor]
        acc["ebitda_rep"] += cf.ebitda_rep[cf.anchor]
        acc["da"] += cf.da_anchor
        acc["capex"] += cf.capex_anchor
        acc["halves"] += 1
        acc["fact"] = ["revenue", "adj_ebitda", "ebitda_rep", "da", "capex"]
        if cf.lti_anchor is not None:
            acc["lti"] += cf.lti_anchor
            acc["fact"].append("lti")
    out = []
    for y in sorted(years):
        acc = years[y]
        acc["margin"] = acc["adj_ebitda"] / acc["revenue"]
        acc["capex_pct"] = acc["capex"] / acc["revenue"]
        acc["leverage"] = acc["net_debt_company"] / acc["ebitda_rep_ltm"]
        out.append(acc)
    return out
