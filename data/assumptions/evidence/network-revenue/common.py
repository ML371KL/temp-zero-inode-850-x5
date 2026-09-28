"""Общие функции листа «Сеть и выручка»: входы, полугодия, продовольственный ИПЦ, история сети,
эффективная площадь по правилам docs/MODEL.md §4.1 и траектории §0.1.

Только стандартная библиотека. Доли — доли единицы (0,05 = 5 %), площадь — тыс. м², деньги — млн ₽
(как в databook) там, где не сказано иное.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"


def load(name: str) -> dict:
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))


def v(x):
    """Значение факта {'v': …}; None — нераскрыто."""
    if x is None:
        return None
    return x["v"] if isinstance(x, dict) else x


# ------------------------------------------------------------------ полугодия
def half_index(p: str) -> int:
    return int(p[:4]) * 2 + int(p[-1]) - 1


def half_name(i: int) -> str:
    return f"{i // 2}H{i % 2 + 1}"


def halves(first: str, last: str) -> list[str]:
    return [half_name(i) for i in range(half_index(first), half_index(last) + 1)]


def shift_half(p: str, k: int) -> str:
    return half_name(half_index(p) + k)


def quarters_of(p: str) -> list[str]:
    y, h = p[:4], p[-1]
    return [f"{y}Q1", f"{y}Q2"] if h == "1" else [f"{y}Q3", f"{y}Q4"]


# ------------------------------------------------------------------ квартальная панель
def panel() -> dict[str, dict]:
    """research/indicators/x5_quarterly_panel.csv: квартал → числа (None — пусто)."""
    out = {}
    with open(INPUTS / "quarterly_panel.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["quarter"]] = {k: (float(x) if x not in ("", None) and k not in ("quarter", "src") else x)
                                   for k, x in row.items()}
    return out


def half_panel() -> dict[str, dict]:
    """Полугодия: ЧРВ и выручка (млн ₽), рост г/г; LFL продажи/трафик/чек и прод. ИПЦ г/г —
    среднее кварталов с весом ЧРВ базы (LFL сравнивает с базой прошлого года)."""
    q = panel()
    out = {}
    for y in range(2012, 2027):
        for h in (1, 2):
            p = f"{y}H{h}"
            qs, qb = quarters_of(p), quarters_of(f"{y - 1}H{h}")
            if not all(k in q for k in qs + qb):
                continue
            w = [q[k]["nrs"] for k in qb]
            wav = lambda col: sum(wi * q[k][col] for wi, k in zip(w, qs)) / sum(w)
            nrs, nrs_b = sum(q[k]["nrs"] for k in qs), sum(q[k]["nrs"] for k in qb)
            rev, rev_b = sum(q[k]["rev"] for k in qs), sum(q[k]["rev"] for k in qb)
            out[p] = {"nrs": nrs, "rev": rev, "g_nrs": nrs / nrs_b - 1, "g_rev": rev / rev_b - 1,
                      "lfl": wav("lfl"), "traffic": wav("traffic"), "ticket": wav("basket"),
                      "cpi_panel": wav("cpi")}
    return out


# ------------------------------------------------------------------ продовольственный ИПЦ
def _food_levels() -> dict[str, float]:
    mom = load("food_cpi_monthly.json")["mom_pct"]
    lvl, out = 1.0, {}
    for ym in sorted(mom):
        lvl *= v(mom[ym]) / 100.0
        out[ym] = lvl
    return out


FOOD = _food_levels()


def months_of(period: str) -> list[str]:
    """'2025' → 12 месяцев; '2025H1' / '2025Q3' → месяцы периода."""
    y = int(period[:4])
    if len(period) == 4:
        ms = range(1, 13)
    elif period[4] == "H":
        ms = range(1, 7) if period[-1] == "1" else range(7, 13)
    else:
        qn = int(period[-1])
        ms = range(3 * qn - 2, 3 * qn + 1)
    return [f"{y}-{m:02d}" for m in ms]


def food_yoy(period: str, months: list[str] | None = None) -> float | None:
    """Рост г/г среднего уровня прод. ИПЦ за период (или за его часть `months`)."""
    ms = months or months_of(period)
    prev = [f"{int(m[:4]) - 1}{m[4:]}" for m in ms]
    if not all(m in FOOD for m in ms + prev):
        return None
    return sum(FOOD[m] for m in ms) / sum(FOOD[m] for m in prev) - 1.0


# ------------------------------------------------------------------ траектории книги (MODEL §0.1)
def path_value(spec: dict, p: str) -> float:
    if p in spec:
        return float(spec[p])
    year = int(p[:4])
    pts = sorted((int(k), float(x)) for k, x in spec.items() if len(k) == 4 and k.isdigit())
    lt = spec.get("LT")
    if not pts:
        if lt is not None:
            return float(lt)
        hs = sorted((k for k in spec if k not in ("LT", "LT_from")), key=half_index)
        return float(spec[hs[-1]])
    if year <= pts[0][0]:
        return pts[0][1]
    ly, lv = pts[-1]
    if year > ly:
        if lt is None:
            return lv
        lf = int(spec.get("LT_from", ly + 1))
        return float(lt) if year >= lf else lv + (float(lt) - lv) * (year - ly) / (lf - ly)
    for (y0, v0), (y1, v1) in zip(pts, pts[1:]):
        if y0 <= year <= y1:
            return v0 + (v1 - v0) * (year - y0) / (y1 - y0)
    raise AssertionError


# ------------------------------------------------------------------ история сети (сопоставимый базис)
def network_history() -> dict:
    """Площадь на конец полугодий 2022H2–2026H1, валовые открытия и закрытия площади 2022H1–2026H1.

    2023H2–2026H1 — факты (data/facts/network.json: закрытая площадь = закрытые магазины × средний
    магазин формата на начало полугодия, открытия = чистый прирост + закрытия). 2022H2 и 2023H1 — оценка
    листа тем же правилом по трейдинг-апдейтам 4 кв. 2022 – 4 кв. 2023 (inputs/tu_2022_2023.json) и
    остаткам databook «как отчитано» + площадь «Красного Яра» и «Слаты» (в сопоставимом базисе они
    внутри «Пятёрочки»). Открытия 2022H1 и 2022H2 нужны только для незрелой части стартового индекса.
    """
    nf, oq, tu = load("network_facts.json"), load("operating_q.json")["quarterly"], load("tu_2022_2023.json")
    A = {p: v(x) for p, x in nf["area_end"].items()}
    C = {p: v(x) for p, x in nf["closed_area_est"].items()}
    O = {p: v(x) for p, x in nf["gross_opened_hist"].items()}
    note = {p: "факт (data/facts/network.json)" for p in A}

    def rep(q, key):
        return v(oq[q]["as_reported"][key])

    # «Красный Яр» и «Слата»: площадь на 31.12.2023 = «Пятёрочка» сопоставимая − «как отчитано»
    ky_end23 = v(nf["area_end_by_format"]["2023H2"]["pyaterochka"]) - rep("2023Q4", "space_pyaterochka")
    ky_per_store = ky_end23 / v(tu["ky_slata_stores_2023_end"])
    T = tu["quarters"]
    ky_h2_23 = v(T["2023Q3"]["ky_slata_net"]) + v(T["2023Q4"]["ky_slata_net"])
    ky_h1_23 = v(T["2023Q1"]["ky_slata_net"]) + v(T["2023Q2"]["ky_slata_net"])
    ky = {"2023H1": ky_end23 - ky_h2_23 * ky_per_store,
          "2022H2": ky_end23 - (ky_h2_23 + ky_h1_23) * ky_per_store}
    for p, q in (("2022H2", "2022Q4"), ("2023H1", "2023Q2")):
        A[p] = (rep(q, "space_pyaterochka") + rep(q, "space_perekrestok") + rep(q, "space_chizhik") + ky[p])
        note[p] = "оценка листа: databook «как отчитано» (П + Пер + Ч) + «Красный Яр»/«Слата»"

    def avg_store(q, fmt):
        return rep(q, f"space_{fmt}") / rep(q, f"stores_{fmt}")

    # закрытия 2023: магазины из ТА × средний магазин формата на начало полугодия
    for p, qs, q0 in (("2023H1", ("2023Q1", "2023Q2"), "2022Q4"), ("2023H2", ("2023Q3", "2023Q4"), "2023Q2")):
        cp = sum(v(T[q]["pyaterochka_closed"]) for q in qs) * avg_store(q0, "pyaterochka")
        ce = sum(v(T[q]["perekrestok_closed"]) for q in qs) * avg_store(q0, "perekrestok")
        C.setdefault(p, cp + ce)
    O["2023H1"] = A["2023H1"] - A["2022H2"] + C["2023H1"]
    if "2023H2" not in O:
        O["2023H2"] = A["2023H2"] - A["2023H1"] + C["2023H2"]
    # 2022: органический чистый прирост трёх форматов (без «Карусели» и без покупки КЯ/Слаты)
    # + закрытия; 3 кв. 2022 не раскрыт (ТА на x5.ru нет) — закрытия полугодий приняты = 2 × 4 кв. 2022
    c22 = 2 * (v(T["2022Q4"]["pyaterochka_closed"]) * avg_store("2022Q2", "pyaterochka")
               + v(T["2022Q4"]["perekrestok_closed"]) * avg_store("2022Q2", "perekrestok"))
    net22 = {}
    for p, q1, q0 in (("2022H2", "2022Q4", "2022Q2"), ("2022H1", "2022Q2", "2021Q4")):
        net22[p] = sum(rep(q1, f"space_{f}") - rep(q0, f"space_{f}") for f in ("pyaterochka", "perekrestok", "chizhik"))
        O[p] = net22[p] + c22
        C[p] = c22
    return {"A": dict(sorted(A.items(), key=lambda t: half_index(t[0]))),
            "O": dict(sorted(O.items(), key=lambda t: half_index(t[0]))),
            "C": dict(sorted(C.items(), key=lambda t: half_index(t[0]))),
            "note": note, "ky_slata_area_2023_end": ky_end23, "ky_per_store": ky_per_store,
            "closed_2022_half_est": c22, "net_2022_organic": net22}


# ------------------------------------------------------------------ эффективная площадь (MODEL §4.1)
def mu_at(mu: list[float], age: int) -> float:
    """Доля зрелой продуктивности когорты на конец полугодия возраста `age` (0 — полугодие открытия)."""
    if age < 0:
        return 0.0
    return mu[age] if age < len(mu) else 1.0


def mu_avg(mu: list[float], age: int) -> float:
    """Вклад когорты в средний индекс полугодия (среднее концов), возраст на конец = age."""
    return (mu_at(mu, age - 1) + mu_at(mu, age)) / 2.0


def eff_consistent(order: list[str], A0: float, O: dict, C: dict, mu: list[float], d: float,
                   kappa: float) -> dict:
    """Индекс эффективной площади, когда ВСЕ когорты (и до старта) созревают до плотности d.

    Старт — конец order[0]: A_eff = A − Σ незрелой части последних когорт (с плотностью d);
    дальше рекурсия §4.1. Возвращает концы, средние и разложение роста г/г на части «вне LFL»
    (когорты моложе 12 полных месяцев и закрытия) и «созревание в отчётном LFL».
    """
    idx = {p: i for i, p in enumerate(order)}
    start = order[0]
    first_known = min(O, key=half_index)
    cohorts = {p: O[p] for p in O}

    def older(p, a):
        return half_name(half_index(p) - a)

    eff = {start: A0 - sum(cohorts[older(start, a)] * d * (1.0 - mu_at(mu, a))
                           for a in range(len(mu)) if older(start, a) in cohorts)}
    for p in order[1:]:
        mat = sum(cohorts[older(p, a)] * d * (mu_at(mu, a) - mu_at(mu, a - 1))
                  for a in range(1, len(mu)) if older(p, a) in cohorts)
        eff[p] = eff[order[idx[p] - 1]] - kappa * C[p] + O[p] * d * mu_at(mu, 0) + mat
    avg = {p: (eff[order[idx[p] - 1]] + eff[p]) / 2.0 for p in order[1:]}
    out = {}
    for p in order[3:]:
        base = avg[older(p, 2)]
        g = avg[p] / base - 1.0

        def coh(a):
            q = older(p, a)
            return cohorts.get(q, 0.0) if half_index(q) >= half_index(first_known) else 0.0

        new = d * (coh(0) * mu_avg(mu, 0) + coh(1) * mu_avg(mu, 1) + 0.5 * coh(2) * mu_avg(mu, 2))
        lfl = d * (coh(2) * (0.5 * mu_avg(mu, 2) - mu_avg(mu, 0))
                   + sum(coh(a) * (mu_avg(mu, a) - mu_avg(mu, a - 2)) for a in range(3, len(mu) + 3)))
        clo = -kappa * (C[p] / 2.0 + C[older(p, 1)] + C[older(p, 2)] / 2.0)
        out[p] = {"g": g, "nonlfl": (new + clo) / base, "m": lfl / base}
    return {"eff": eff, "avg": avg, "yoy": out}


def eff_model_rule(anchor: str, P: list[str], A_hist: dict, O_hist: dict, O: dict, C: dict,
                   mu: list[float], d: float, kappa: float) -> dict:
    """Правило ядра (MODEL §4.1, core._make_network): история — площадь минус незрелость последних
    n когорт с d = 1; прогнозные когорты — с плотностью d. Возвращает средние и рост г/г."""
    n = len(mu) - 1

    def immature(q):
        return sum(O_hist[half_name(half_index(q) - a)] * (1.0 - mu[a]) for a in range(n))

    b1, b2 = shift_half(anchor, -1), shift_half(anchor, -2)
    eh = {q: A_hist[q] - immature(q) for q in (b2, b1, anchor)}
    avg = {b1: (eh[b2] + eh[b1]) / 2.0, anchor: (eh[b1] + eh[anchor]) / 2.0}
    cohorts = [(O_hist[q], 1.0) for q in sorted(O_hist, key=half_index) if half_index(q) <= half_index(anchor)]
    eff = eh[anchor]
    for p in P:
        mat = sum(cohorts[-a][0] * cohorts[-a][1] * (mu[a] - mu[a - 1]) for a in range(1, min(n, len(cohorts)) + 1))
        new = eff - C[p] * kappa + O[p] * d * mu[0] + mat
        avg[p] = (eff + new) / 2.0
        cohorts.append((O[p], d))
        eff = new
    yoy = {p: avg[p] / avg[shift_half(p, -2)] - 1.0 for p in P}
    return {"avg": avg, "yoy": yoy}


def forward_network(A0: float, P: list[str], growth: dict, close: dict) -> tuple[dict, dict, dict]:
    """Площадь, открытия и закрытия прогноза по §4.1: closed = A·cl/2, opened = A·g/2 + closed."""
    A, O, C = {}, {}, {}
    a = A0
    for p in P:
        c = a * path_value(close, p) / 2.0
        o = a * path_value(growth, p) / 2.0 + c
        a = a + o - c
        A[p], O[p], C[p] = a, o, c
    return A, O, C


# ------------------------------------------------------------------ печать листа
class Report:
    """Лист: текст (…_out.txt) и числа (…_out.json); перевод строки \\n на любой ОС."""

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
        text = "\n".join(self.lines).strip() + "\n"
        (HERE / f"{stem}_out.txt").write_text(text, encoding="utf-8", newline="\n")
        (HERE / f"{stem}_out.json").write_text(
            json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        print(text)


def fmt(c) -> str:
    if isinstance(c, float):
        return f"{c:,.3f}".replace(",", " ")
    return str(c)


def pc(x: float | None, nd: int = 2) -> str:
    return "н/р" if x is None else f"{100 * x:.{nd}f}"


def r6(x: float) -> float:
    return round(x, 6)


def ols(xs: list[float], ys: list[float]) -> dict:
    """y = a + k·x: оценки, стандартные ошибки, RMSE, R²."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    k = sxy / sxx
    a = my - k * mx
    e = [y - a - k * x for x, y in zip(xs, ys)]
    s2 = sum(z * z for z in e) / (n - 2)
    se_k = math.sqrt(s2 / sxx)
    se_a = math.sqrt(s2 * (1.0 / n + mx * mx / sxx))
    syy = sum((y - my) ** 2 for y in ys)
    return {"n": n, "a": a, "k": k, "se_a": se_a, "se_k": se_k, "rmse": math.sqrt(s2),
            "r2": 1.0 - sum(z * z for z in e) / syy, "mean_x": mx, "mean_y": my}
