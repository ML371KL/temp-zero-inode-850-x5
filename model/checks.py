"""Проверки выпуска (docs/MODEL.md §13): инварианты, гейты правдоподобия, флаги.

* **Инвариант** — арифметика; нарушение — поломка, сборка не выходит.
* **Гейт** — экономика; масса — суммарная вероятность клеток слоя «свой
  взгляд», где условие нарушено. Сработавший гейт требует объяснения в
  `data/assumptions/gate_explanations.yaml` со сроком `valid_until` и
  коридором ожидаемой массы `expected_mass`; нет объяснения, срок истёк или
  масса вне коридора — сборка падает.
* **Флаг** — совещательная плашка; публикацию не блокирует.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from model.book import BOOK_DIR, period_of, today as _today
from model.book_schema import CAPEX_LEVELS, REGIMES

fsum = math.fsum

GATE_EXPLANATIONS = BOOK_DIR / "gate_explanations.yaml"
# Допуск инварианта вероятностей (§13.1).
PROB_TOL = 1e-12
# Допуск тождеств строк: относительный, с полом на малых величинах.
IDENTITY_TOL = 1e-9
# Потолок выпуска, байт компактного JSON (§13.1).
PAYLOAD_MAX_BYTES = 500_000
# Базисных пунктов в единице ставки (флаг book_update).
BP = 10_000


# ------------------------------------------------------------ инварианты


@dataclass(frozen=True)
class Invariant:
    name: str
    ok: bool
    detail: str


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= IDENTITY_TOL * max(1.0, abs(a), abs(b))


def _finite_fields(obj: Any, where: str, out: list[str]) -> None:
    """Все числа объекта (dataclass, словарь, список) конечны; `None` — допустим."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return
    if isinstance(obj, (int, float)):
        if not math.isfinite(obj):
            out.append(f"{where} = {obj!r}")
        return
    if is_dataclass(obj):
        for f in fields(obj):
            if f.name not in ("cell", "ctx"):
                _finite_fields(getattr(obj, f.name), f"{where}.{f.name}", out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _finite_fields(v, f"{where}.{k}", out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _finite_fields(v, f"{where}[{i}]", out)


def invariants(grid) -> list[Invariant]:
    """Инварианты сетки: вероятности, тождества FCFF и долга, конечность чисел."""
    A = grid.ctx.A
    out = []
    bad = []
    for layer in ("analytical", "market_implied", "macro_neutral"):
        total = fsum(c.p[layer] for c in grid.cells)
        if abs(total - 1.0) > PROB_TOL:
            bad.append(f"клетки слоя {layer}: {total!r}")
    if abs(fsum(grid.regime_posterior.values()) - 1.0) > PROB_TOL:
        bad.append(f"режимы: {fsum(grid.regime_posterior.values())!r}")
    for r in REGIMES:
        row = A["joint"]["capex_prob_given_regime"][r]
        total = fsum(float(row[c]) for c in CAPEX_LEVELS)
        if abs(total - 1.0) > PROB_TOL:
            bad.append(f"capex | {r}: {total!r}")
    out.append(Invariant("probabilities", not bad, "; ".join(bad) or "суммы = 1"))

    fcff_bad, debt_bad, capex_bad, ev_bad = [], [], [], []
    for c in grid.cells:
        res = c.result
        parts = (res.pv_fcff + res.pv_shield + res.pv_terminal - res.pv_issuance
                 - res.pv_excess_spread - res.pv_buffer_carry)
        if not _close(parts, res.ev):
            ev_bad.append(c.key)
        nd_prev = grid.ctx.facts.net_debt + grid.ctx.facts.dividends_payable
        for r in res.rows:
            rebuilt = (r.adj_ebitda - r.lti - r.tax_unlevered - r.capex - r.nwc_change
                       - r.opcash_change + r.lease + r.proceeds)
            if not _close(rebuilt, r.fcff):
                fcff_bad.append(f"{c.key} {r.period}")
            if not _close(nd_prev - (r.fcff + r.shield - r.interest) + r.dividends, r.net_debt):
                debt_bad.append(f"{c.key} {r.period}")
            if not _close(r.capex_maintenance + r.capex_growth + r.capex_infra, r.capex):
                capex_bad.append(f"{c.key} {r.period}")
            nd_prev = r.net_debt
        for h in res.terminal.halves:
            rebuilt = (h.adj_ebitda - h.lti - h.tax - h.capex - h.nwc_change - h.opcash_change
                       + h.lease + h.proceeds)
            if not _close(rebuilt, h.fcff):
                fcff_bad.append(f"{c.key} {h.period}")
    out.append(Invariant("fcff_identity", not fcff_bad,
                         "; ".join(fcff_bad[:5]) or "FCFF = сумме опубликованных строк"))
    out.append(Invariant("debt_identity", not debt_bad,
                         "; ".join(debt_bad[:5]) or "ND(p) = ND(p−1) − (FCFF + S − I) + div"))
    out.append(Invariant("capex_identity", not capex_bad,
                         "; ".join(capex_bad[:5]) or "capex = поддерживающий + рост + инфраструктура"))
    out.append(Invariant("ev_identity", not ev_bad,
                         "; ".join(ev_bad[:5]) or "EV = PV FCFF + PV щита + PV терминала − вычеты "
                                                  "финансирования"))

    inf = []
    for c in grid.cells:
        _finite_fields(c.result, c.key, inf)
    for name, layer in {**grid.layers, **grid.worlds_only}.items():
        _finite_fields(layer, name, inf)
    _finite_fields(grid.point, "point", inf)
    out.append(Invariant("finite", not inf, "; ".join(inf[:5]) or "все числа конечны"))
    return out


def round_to_step(value: float, step: float) -> float:
    """Печать: округление к шагу, половина — вверх."""
    return math.floor(value / step + 0.5) * step


def printed_ok(value: float, printed: float, step: float) -> bool:
    """Инвариант печати: напечатанное = округлению точного к шагу."""
    return printed == round_to_step(value, step)


def payload_size_ok(n_bytes: int) -> bool:
    return n_bytes <= PAYLOAD_MAX_BYTES


# ----------------------------------------------------------------- гейты

GATES = ("ev_ebitda", "margin_range", "capex_range", "terminal_share", "real_rate",
         "leverage_path", "equity_cushion")
GATE_TITLES = {
    "ev_ebitda": "EV / скорр. EBITDA следующих 12 мес. вне коридора мира",
    "margin_range": "маржа полугодия вне коридора",
    "capex_range": "capex / выручка года вне коридора",
    "terminal_share": "доля PV терминала в EV вне коридора",
    "real_rate": "реальная ставка терминала мира вне коридора",
    "leverage_path": "ЧД / отчётная EBITDA LTM пути выше предела",
    "equity_cushion": "капитал клетки ≤ 0 или V0/D слоя ниже предела",
}


class GateExplanationError(ValueError):
    """Файл объяснений гейтов не читается однозначно."""


def gate_violations(grid) -> dict[str, list[tuple[str, str]]]:
    """Нарушения по гейтам: {гейт: [(клетка или «слой …», что нарушено)]}."""
    A = grid.ctx.A
    K = A["checks"]
    out: dict[str, list[tuple[str, str]]] = {g: [] for g in GATES}
    m_lo, m_hi = K["margin_range"]
    c_lo, c_hi = K["capex_range"]
    t_lo, t_hi = K["terminal_share"]
    r_lo, r_hi = K["real_rate"]
    for c in grid.cells:
        res, cell = c.result, c.result.cell
        lo, hi = K["ev_ebitda"][cell.world]
        x = res.ev_ebitda_fwd
        if x is None or not lo <= x <= hi:
            out["ev_ebitda"].append((c.key, f"{x:.2f}×" if x is not None else "EBITDA ≤ 0"))
        if res.margin_min < m_lo or res.margin_max > m_hi:
            out["margin_range"].append((c.key, f"{res.margin_min:.4f}…{res.margin_max:.4f}"))
        off = [(y, v) for y, v in res.capex_pct_years if not c_lo <= v <= c_hi]
        if off:
            out["capex_range"].append((c.key, ", ".join(f"{y}: {v:.4f}" for y, v in off[:3])))
        if not t_lo <= res.terminal_share <= t_hi:
            out["terminal_share"].append((c.key, f"{res.terminal_share:.3f}"))
        real = res.terminal.rate - float(A["worlds"][cell.world]["lt"]["inflation"])
        if not r_lo <= real <= r_hi:
            out["real_rate"].append((c.key, f"{real:.4f}"))
        if res.max_leverage > K["max_leverage"]:
            out["leverage_path"].append((c.key, f"{res.max_leverage:.2f}×"))
        if res.equity <= 0:
            out["equity_cushion"].append((c.key, f"капитал {res.equity:.1f}"))
    for name, layer in grid.layers.items():
        # D ≤ 0 (чистая касса) — подушка бесконечна, условие не нарушено.
        if layer.v0_to_d is not None and layer.v0_to_d < K["min_v0_to_d"]:
            out["equity_cushion"].append((f"слой {name}", f"V0/D {layer.v0_to_d:.2f}"))
    return out


@dataclass(frozen=True)
class GateResult:
    name: str
    title: str
    fired: bool
    mass: float
    cells: tuple
    detail: str
    explanation: str = ""
    valid_until: dt.date | None = None
    expected_mass: tuple | None = None
    status: str = "ok"          # ok | explained | unexplained | expired | mass_outside
    blocking: bool = False


def gate_masses(grid) -> list[GateResult]:
    """Гейты без объяснений: сработал ли и какой массой (слой «свой взгляд»)."""
    p = {c.key: c.p["analytical"] for c in grid.cells}
    out = []
    for name, hits in gate_violations(grid).items():
        layer_hit = any(key.startswith("слой ") for key, _ in hits)
        mass = 1.0 if layer_hit else fsum(p[key] for key, _ in hits)
        out.append(GateResult(name=name, title=GATE_TITLES[name], fired=bool(hits), mass=mass,
                              cells=tuple(key for key, _ in hits),
                              detail="; ".join(f"{k}: {v}" for k, v in hits[:6])
                              + (f"; и ещё {len(hits[6:])}" if hits[6:] else "")))
    return out


def _parse_date(raw: Any, where: str) -> dt.date:
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    try:
        return dt.date.fromisoformat(str(raw))
    except ValueError:
        raise GateExplanationError(f"{where}: valid_until = {raw!r} — не дата ГГГГ-ММ-ДД") from None


def load_gate_explanations(path: Path | str | None = None) -> dict:
    """Объяснения гейтов: {гейт: {explanation, expected_mass (lo, hi), valid_until}}.

    Нет файла — пустой словарь (сработавший гейт тогда блокирует). Незнакомый
    гейт, незнакомый ключ записи, неполная запись — `GateExplanationError`.
    """
    path = Path(path) if path is not None else GATE_EXPLANATIONS
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise GateExplanationError(f"{path.name}: ожидается словарь {{гейт: объяснение}}")
    out = {}
    for key, spec in raw.items():
        where = f"{path.name}: {key}"
        if key not in GATES:
            raise GateExplanationError(f"{where} — незнакомый гейт (известны: {', '.join(GATES)})")
        if not isinstance(spec, dict):
            raise GateExplanationError(f"{where} — ожидается запись {{explanation, expected_mass, "
                                       "valid_until}}")
        unknown = sorted(set(spec) - {"explanation", "expected_mass", "valid_until"})
        missing = sorted({"explanation", "expected_mass", "valid_until"} - set(spec))
        if unknown or missing:
            raise GateExplanationError(f"{where}: " + "; ".join(
                ([f"незнакомый ключ {', '.join(unknown)}"] if unknown else [])
                + ([f"нет ключа {', '.join(missing)}"] if missing else [])))
        text = spec["explanation"]
        if not isinstance(text, str) or not text.strip():
            raise GateExplanationError(f"{where}: explanation — пустой текст")
        mass = spec["expected_mass"]
        if (not isinstance(mass, list) or len(mass) != 2
                or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in mass)
                or not 0 <= mass[0] <= mass[1] <= 1):
            raise GateExplanationError(f"{where}: expected_mass = {mass!r} — ожидается коридор "
                                       "[нижняя, верхняя] долей массы, 0 ≤ нижняя ≤ верхняя ≤ 1")
        out[key] = {"explanation": text.strip(), "expected_mass": (float(mass[0]), float(mass[1])),
                    "valid_until": _parse_date(spec["valid_until"], where)}
    return out


def gates(grid, explanations: dict | None = None, today: dt.date | None = None) -> list[GateResult]:
    """Гейты с объяснениями на день `today` (по умолчанию — сегодня, `FAKE_TODAY`)."""
    explanations = load_gate_explanations() if explanations is None else explanations
    today = today or _today()
    out = []
    for g in gate_masses(grid):
        spec = explanations.get(g.name)
        status, blocking = "ok", False
        if g.fired:
            if spec is None:
                status, blocking = "unexplained", True
            elif spec["valid_until"] < today:
                status, blocking = "expired", True
            elif not spec["expected_mass"][0] <= g.mass <= spec["expected_mass"][1]:
                status, blocking = "mass_outside", True
            else:
                status = "explained"
        out.append(GateResult(
            name=g.name, title=g.title, fired=g.fired, mass=g.mass, cells=g.cells, detail=g.detail,
            explanation=spec["explanation"] if spec else "",
            valid_until=spec["valid_until"] if spec else None,
            expected_mass=spec["expected_mass"] if spec else None,
            status=status, blocking=blocking))
    return out


def blocking_reasons(invariant_list: Iterable[Invariant], gate_list: Iterable[GateResult]) -> list[str]:
    """Причины, по которым сборка не выходит (пусто — выходит)."""
    out = [f"инвариант {i.name}: {i.detail}" for i in invariant_list if not i.ok]
    reason = {"unexplained": "нет объяснения", "expired": "срок объяснения истёк",
              "mass_outside": "масса вне коридора объяснения"}
    for g in gate_list:
        if g.blocking:
            out.append(f"гейт {g.name} ({g.title}): {reason[g.status]}; масса {g.mass:.4f}; "
                       f"{g.detail}")
    return out


# ----------------------------------------------------------------- флаги


@dataclass(frozen=True)
class Flag:
    name: str
    title: str
    raised: bool
    detail: str


def flag_book_update(A: dict, live_nodes: dict | None, today: dt.date | None = None) -> Flag:
    """`book_update`: живая кривая ОФЗ сдвинулась от кривой книги (мир «рыночный как
    есть») на узлах 5 или 10 лет на ≥ shift_bp, или книге больше max_age_days дней."""
    today = today or _today()
    rule = A["checks"]["book_update"]
    book_curve = A["worlds"][A["joint"]["neutral_world"]]["zero_curve"]
    reasons = []
    for node in ("5", "10"):
        if live_nodes and live_nodes.get(node) is not None:
            shift = (float(live_nodes[node]) - float(book_curve[node])) * BP
            if abs(shift) >= rule["shift_bp"]:
                reasons.append(f"узел {node} лет сдвинулся на {shift:+.0f} б.п.")
    age = (today - dt.date.fromisoformat(A["meta"]["date"])).days
    if age > rule["max_age_days"]:
        reasons.append(f"книге {age} дн.")
    return Flag("book_update", "книгу пора обновить", bool(reasons),
                "; ".join(reasons) or "кривая и возраст книги в пределах")


def flag_dividend_register(expected_ex_dates: Iterable[dt.date], register: Iterable[dict],
                           facts_date: dt.date, today: dt.date | None = None) -> Flag:
    """`dividend_register`: по календарю прошла ожидаемая дата отсечки (позже даты
    фактов), а в реестре нет объявленного дивиденда с отсечкой в том же полугодии."""
    today = today or _today()
    halves = {period_of(dt.date.fromisoformat(str(r["ex_date"])))
              for r in register if r.get("ex_date")}
    missed = [d for d in expected_ex_dates
              if facts_date < d <= today and period_of(d) not in halves]
    return Flag("dividend_register", "ожидаемая отсечка прошла, дивиденда в реестре нет",
                bool(missed), ", ".join(d.isoformat() for d in sorted(missed)) or "реестр полон")


def flag_price_fallback(status: str) -> Flag:
    """`price_fallback`: живая цена не принята, взята последняя принятая."""
    return Flag("price_fallback", "цена — последняя принятая", status == "fallback",
                f"статус цены: {status}")
