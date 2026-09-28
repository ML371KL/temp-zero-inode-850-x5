"""Факты X5: загрузка `data/facts/*.json` по `data/facts/SCHEMA.md`.

Значение факта — узел `{"v": …, "src": …}` или `{"v": …, "calc": …}`: ядро
читает `v`. Узел без `src` и без `calc` — отказ `FactsError` (число без
источника не считается). Нераскрытое — `null` → `None`, не 0: ядро требует
значение там, где его читает, и на `None` отказывает с путём факта.

Пока агент фактов не собрал `data/facts/accounting.json`, по умолчанию
читается фикстура `tests/fixtures/facts/` — с предупреждением.
"""

from __future__ import annotations

import datetime as dt
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model.book import ROOT, next_period, period_index, periods, prev_period

FACTS_DIR = ROOT / "data" / "facts"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "facts"
CORE_FILES = ("accounting", "network", "balance", "bridge", "shares", "dividends", "history")


class FactsError(ValueError):
    """Факты не читаются однозначно — считать по ним нельзя."""


class FactsFallbackWarning(UserWarning):
    """`data/facts/accounting.json` нет — читается фикстура тестов."""


def default_facts_dir() -> Path:
    """`data/facts`, если там есть accounting.json, иначе фикстура (с предупреждением)."""
    if (FACTS_DIR / "accounting.json").exists():
        return FACTS_DIR
    warnings.warn("фактов data/facts/accounting.json нет — читается фикстура "
                  "tests/fixtures/facts (значения из research/facts, не выверены агентом фактов)",
                  FactsFallbackWarning, stacklevel=2)
    return FIXTURE_DIR


def unwrap(node: Any, where: str) -> Any:
    """Раскрывает узлы `{"v": …}` в значения; узел без `src` и `calc` — отказ."""
    if isinstance(node, dict):
        if "v" in node:
            # Число без источника — отказ; null (нераскрыто) источника не требует:
            # числа нет, а ядро на None само откажет там, где значение нужно.
            if node["v"] is not None and not (node.get("src") or node.get("calc")):
                raise FactsError(f"факты: {where} — значение без src и без calc")
            return unwrap(node["v"], f"{where}.v")
        return {k: unwrap(v, f"{where}.{k}") for k, v in node.items()}
    if isinstance(node, list):
        return [unwrap(v, f"{where}[{i}]") for i, v in enumerate(node)]
    return node


@dataclass(frozen=True)
class Facts:
    """Факты как есть (`raw`) и с раскрытыми значениями (`data`), по имени файла."""

    root: Path
    fixture: bool
    raw: dict = field(repr=False)
    data: dict = field(repr=False)

    def get(self, path: str) -> Any:
        """Значение по пути "файл.ключ.ключ"; нет пути — `FactsError`."""
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise FactsError(f"факты: нет {path} ({self.root.name})")
            node = node[part]
        return node


def load_facts(path: Path | str | None = None) -> Facts:
    """Читает все `*.json` каталога фактов; нет файла ядра — `FactsError`."""
    root = Path(path) if path is not None else default_facts_dir()
    raw, data = {}, {}
    for file in sorted(root.glob("*.json")):
        try:
            raw[file.stem] = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FactsError(f"факты: {file.name} — не JSON ({exc})") from None
        data[file.stem] = unwrap(raw[file.stem], file.stem)
    missing = [name for name in CORE_FILES if name not in data]
    if missing:
        raise FactsError(f"факты: в {root} нет {', '.join(n + '.json' for n in missing)}")
    return Facts(root=root, fixture=root.resolve() == FIXTURE_DIR.resolve(), raw=raw, data=data)


# ------------------------------------------------------------- что читает ядро


@dataclass(frozen=True)
class BridgeLine:
    key: str
    label: str
    amount: float | None    # None — не раскрыто (только у строк вне моста)
    included: bool


@dataclass(frozen=True)
class DeclaredDividend:
    id: str
    amount: float
    ex_date: dt.date | None
    in_company: bool


@dataclass(frozen=True)
class CoreFacts:
    """Факты, которые читает проход клетки, — проверенные и без `None`."""

    anchor: str
    revenue: dict           # полугодие → выручка (якорь и полугодие до него)
    adj_ebitda: dict        # якорь → скорр. EBITDA
    ebitda_rep: dict        # якорь → отчётная EBITDA
    da_anchor: float        # D&A якоря до МСФО 16
    capex_anchor: float     # денежный capex якоря
    capex_hist: dict        # полугодие до якоря → capex (выручка × capex/выручку, history.json)
    lti_anchor: float | None  # расход LTI якоря (справочно, ядро его не читает)
    eff_start: str          # S — первое полугодие индекса эффективной площади (§4.1)
    area_end: dict          # полугодие → площадь на конец (S и якорь)
    gross_opened: dict      # полугодие → валовые открытия (исторические когорты S − n + 1 … якорь)
    closed_area: dict       # полугодие → закрытая площадь (S + 1 … якорь)
    net_debt: float
    dividends_payable: float
    nwc: float
    bridge: tuple           # BridgeLine
    shares_mln: float       # в обращении (выпущенные − казначейские)
    treasury_mln: float     # казначейские (с акциями у дочерних обществ)
    dividends: tuple        # DeclaredDividend

    @property
    def margin_anchor(self) -> float:
        """Факт маржи якоря: скорр. EBITDA / выручка."""
        return self.adj_ebitda[self.anchor] / self.revenue[self.anchor]


_READ = object()


def _need(F: Facts, path: str, value: Any = _READ) -> float:
    """Число факта по пути (или уже прочитанное `value` с этим путём для сообщения)."""
    if value is _READ:
        value = F.get(path)
    if value is None:
        raise FactsError(f"факты: {path} не раскрыт (null) — ядро не считает его нулём")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FactsError(f"факты: {path} = {value!r} — ожидается число")
    return float(value)


def _date(value: Any, where: str) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise FactsError(f"факты: {where} = {value!r} — не дата ГГГГ-ММ-ДД") from None


def core_facts(F: Facts, A: dict) -> CoreFacts:
    """Факты для прохода клетки по книге `A` (якорь, кривая созревания, мост)."""
    anchor = A["meta"]["anchor_period"]
    for name in ("accounting", "balance"):
        as_of = F.data[name].get("as_of")
        if as_of is not None and as_of != A["meta"]["facts_date"]:
            raise FactsError(f"факты: {name}.as_of {as_of} ≠ meta.facts_date "
                             f"{A['meta']['facts_date']}")
    back1 = prev_period(anchor)
    back2 = prev_period(back1)
    acc = "accounting.periods"
    # Выручка якоря и полугодия до него — база «год к году» двух первых полугодий
    # и скользящая годовая выручка якоря; EBITDA — якоря (маржа якоря, LTM).
    revenue = {p: _need(F, f"{acc}.{p}.revenue") for p in (back1, anchor)}
    adj = {anchor: _need(F, f"{acc}.{anchor}.adj_ebitda")}
    rep = {anchor: _need(F, f"{acc}.{anchor}.ebitda_rep")}
    # Индекс эффективной площади (§4.1) ведётся рекурсией от первого полугодия S, на конец
    # которого в фактах есть площадь (факт network.area_end или оценка листа сети
    # network.area_end_est), — так же, как при подборе плотности d. Нужны площадь на конец S
    # и якоря, валовые открытия S − n + 1 … якорь (n = длина кривой созревания − 1) и
    # закрытия S + 1 … якорь; S — не позже якоря − 2 (база «год к году» двух первых полугодий).
    net = F.get("network")
    fact_area, est_area = net.get("area_end") or {}, net.get("area_end_est") or {}
    known = sorted({*fact_area, *est_area}, key=period_index)
    if not known:
        raise FactsError("факты: network.area_end пуст — индекс эффективной площади не начать")
    start = known[0]
    if period_index(start) > period_index(back2):
        raise FactsError(f"факты: площади на конец {back2} нет (network.area_end, "
                         "network.area_end_est) — индекс эффективной площади начинается не позже "
                         "якоря − 2 (docs/MODEL.md §4.1)")
    area = {p: _need(F, f"network.{'area_end' if p in fact_area else 'area_end_est'}.{p}")
            for p in (start, anchor)}
    n = len(A["network"]["maturity_curve"]) - 1
    first_cohort = next_period(start)                   # S − n + 1
    for _ in range(n):
        first_cohort = prev_period(first_cohort)
    opened = {p: _need(F, f"network.gross_opened_hist.{p}") for p in periods(first_cohort, anchor)}
    closed = {p: _need(F, f"network.closed_area_est.{p}")
              for p in periods(next_period(start), anchor)}

    # Капвложения 2L полугодий до якоря — база D&A якоря выбывает по когортам (§4.5):
    # capex полугодия = выручка × (ОС + НМА)/выручка из history.json.
    halves = {row.get("period"): (i, row) for i, row in enumerate(F.get("history.halves"))}
    capex_hist = {}
    p = anchor
    for _ in range(int(round(2 * float(A["capex"]["asset_life_years"])))):
        p = prev_period(p)
        if p not in halves:
            raise FactsError(f"факты: в history.halves нет {p} — база D&A якоря выбывает по "
                             "когортам 2L полугодий до якоря (docs/MODEL.md §4.5)")
        i, row = halves[p]
        capex_hist[p] = (_need(F, f"history.halves.{i}.revenue", row.get("revenue"))
                         * _need(F, f"history.halves.{i}.capex_pct", row.get("capex_pct")))

    include = list(A["bridge"]["include"])
    lines = []
    known = set()
    for i, line in enumerate(F.get("bridge.lines")):
        key = line.get("key")
        known.add(key)
        included = key in include
        amount = line.get("amount")
        if included and amount is None:
            raise FactsError(f"факты: bridge.lines[{i}] {key} не раскрыт (null), а книга "
                             "включает строку в мост")
        lines.append(BridgeLine(key=key, label=str(line.get("label", key)),
                                amount=float(amount) if amount is not None else None,
                                included=included))
    lost = [k for k in include if k not in known]
    if lost:
        raise FactsError(f"факты: строк моста {', '.join(lost)} (bridge.include книги) нет в "
                         "bridge.json")

    dividends = []
    for i, row in enumerate(F.get("dividends.register")):
        where = f"dividends.register[{i}]"
        in_company = row.get("cash_in_company_at_facts_date")
        if not isinstance(in_company, bool):
            raise FactsError(f"факты: {where}.cash_in_company_at_facts_date — ожидается "
                             f"true/false, а не {in_company!r}")
        amount = row.get("amount")
        if in_company and amount is None:
            raise FactsError(f"факты: {where}.amount не раскрыт (null)")
        dividends.append(DeclaredDividend(
            id=str(row.get("id", i)), amount=float(amount) if amount is not None else 0.0,
            ex_date=_date(row.get("ex_date"), f"{where}.ex_date"), in_company=in_company))

    return CoreFacts(
        anchor=anchor, revenue=revenue, adj_ebitda=adj, ebitda_rep=rep,
        da_anchor=_need(F, f"{acc}.{anchor}.da"), capex_anchor=_need(F, f"{acc}.{anchor}.capex"),
        capex_hist=dict(sorted(capex_hist.items(), key=lambda kv: period_index(kv[0]))),
        lti_anchor=(_need(F, f"{acc}.{anchor}.lti")
                    if F.data["accounting"]["periods"][anchor].get("lti") is not None else None),
        eff_start=start, area_end=area, gross_opened=opened, closed_area=closed,
        net_debt=_need(F, "balance.net_debt"),
        dividends_payable=_need(F, "balance.dividends_payable"),
        nwc=_need(F, "balance.nwc"), bridge=tuple(lines),
        shares_mln=_need(F, "shares.outstanding_mln"), treasury_mln=_need(F, "shares.treasury"),
        dividends=tuple(dividends))
