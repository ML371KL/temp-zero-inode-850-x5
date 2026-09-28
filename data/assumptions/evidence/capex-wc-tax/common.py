"""Общие загрузчики и помощники листа capex-wc-tax (только стандартная библиотека)."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
INP = HERE / "inputs"


def load(name: str) -> dict:
    return json.loads((INP / name).read_text(encoding="utf-8"))


FACTS = load("facts_extract.json")
PRIM = load("primary.json")
CPI = load("cpi.json")
BOOK = load("book_refs.json")
CFI = load("cf_investing.json")      # ОДДС до МСФО 16: выбытие ОС, прочие инвестиционные платежи


def v(x) -> float | None:
    """Значение поля факта {'v': ...}; None — нераскрыто."""
    if x is None:
        return None
    return x["v"] if isinstance(x, dict) else x


def hy(period: str, key: str) -> float | None:
    return v(FACTS["halfyear"].get(period, {}).get(key))


def an(year: str, key: str) -> float | None:
    return v(FACTS["annual"].get(year, {}).get(key))


def bal(date: str, key: str) -> float | None:
    return v(FACTS["balance"].get(date, {}).get(key))


def net_q(q: str, key: str, basis: str = "as_reported") -> float | None:
    row = FACTS["network_q"].get(q, {})
    b = row.get(basis) or row.get("as_reported") or {}
    return v(b.get(key))


# ---------------------------------------------------------------- цены (ИПЦ)

def _levels() -> dict[str, float]:
    """Цепной индекс уровня цен на конец месяца (декабрь 2013 = 1)."""
    lvl, out = 1.0, {}
    for ym, pct in sorted(CPI["mom_pct"].items()):
        lvl *= pct / 100.0
        out[ym] = lvl
    return out


LEVEL = _levels()


def avg_level(months: list[str]) -> float:
    xs = [LEVEL[m] for m in months if m in LEVEL]
    if len(xs) != len(months):
        raise KeyError(f"нет ИПЦ за часть месяцев: {months}")
    return sum(xs) / len(xs)


def period_months(p: str) -> list[str]:
    """'2025' → 12 месяцев; '2025H1' → январь–июнь; '2025H2' → июль–декабрь."""
    y = int(p[:4])
    if len(p) == 4:
        rng = range(1, 13)
    elif p.endswith("H1"):
        rng = range(1, 7)
    else:
        rng = range(7, 13)
    return [f"{y}-{m:02d}" for m in rng]


ANCHOR = "2026H1"


def to_anchor(p: str) -> float:
    """Множитель перевода рублей периода p в цены якоря (средний уровень 1П2026)."""
    return avg_level(period_months(ANCHOR)) / avg_level(period_months(p))


def half(r: float) -> float:
    return (1.0 + r) ** 0.5 - 1.0


def pct(x: float, nd: int = 2) -> str:
    return f"{100 * x:.{nd}f} %"


class Report:
    """Печать листа: одновременно в stdout-текст и в словарь для JSON."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.data: dict = {}

    def h(self, title: str) -> None:
        self.lines += ["", title, "-" * len(title)]

    def p(self, *parts) -> None:
        self.lines.append(" ".join(str(x) for x in parts))

    def table(self, header: list[str], rows: list[list]) -> None:
        cells = [header] + [[fmt(c) for c in r] for r in rows]
        w = [max(len(str(r[i])) for r in cells) for i in range(len(header))]
        for j, r in enumerate(cells):
            self.lines.append(" | ".join(str(c).rjust(w[i]) for i, c in enumerate(r)))
            if j == 0:
                self.lines.append("-+-".join("-" * x for x in w))

    def save(self, stem: str) -> None:
        # перевод строки \n на любой ОС — вывод побайтно одинаков на Windows и Linux
        with open(HERE / f"{stem}_out.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(self.lines).strip() + "\n")
        with open(HERE / f"{stem}_out.json", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def fmt(c) -> str:
    if isinstance(c, float):
        return f"{c:,.3f}".replace(",", " ")
    return str(c)


def r6(x: float) -> float:
    return round(x, 6)
