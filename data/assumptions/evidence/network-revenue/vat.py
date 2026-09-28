"""A-R3. Поправка на НДС 22 % с 01.01.2026: насколько выручка без НДС растёт медленнее продаж с НДС.

Метод. Средний чек (с НДС, промокодами и бонусами) × число покупателей (чеков) = продажи с НДС; делим на
чистую розничную выручку (без НДС) того же формата и квартала — databook, лист Operating Results.
Отношение годами стабильно (разброс ±0,2 %); скачок в 1 и 2 кв. 2026 г/г и есть клин НДС. Проверка —
структурой ставок: доля товаров по ставке 20→22 % (w) из уровня отношения 2025 г. и тот же скачок по w.

Выход: vat_out.txt / vat_out.json. Книга: revenue.vat_effect = {"2026H2": −клин, "LT": 0}.
"""
from __future__ import annotations

from common import Report, load, r6, v

OQ = load("operating_q.json")["quarterly"]
FORMATS = ("pyaterochka", "perekrestok", "chizhik", "total")


def ratio(q: str, fmt: str) -> float | None:
    """(средний чек × покупатели) / ЧРВ; базис «Скорр.», если есть (КЯ/Слата в «Пятёрочке» — и в чеке)."""
    row = OQ[q].get("restated_KY_Slata") or OQ[q]["as_reported"]
    if q == "2024Q4" and fmt == "pyaterochka":  # «Скорр.» ЧРВ с КЯ/Слатой при чеке без них — не сравнимо
        row = OQ[q]["as_reported"]
    t, c, n = (v(row.get(f"{k}_{fmt}")) for k in ("avg_ticket", "customers", "nrs"))
    if not (t and c and n):
        return None
    return t * c / n  # ₽ × млн чеков / млн ₽


def wedge_2026() -> float:
    """Клин НДС 2026 г.: средний скачок отношения по итогу X5 в 1 и 2 кв. 2026 г/г."""
    return sum(ratio(q, "total") / ratio(q.replace("2026", "2025"), "total") - 1.0 for q in ("2026Q1", "2026Q2")) / 2.0


def main() -> None:
    R = Report()
    R.h("1. Продажи с НДС (средний чек × покупатели) / ЧРВ без НДС, по кварталам")
    qs = [q for q in sorted(OQ) if q >= "2022Q1"]
    rows, ratios = [], {}
    for q in qs:
        ratios[q] = {f: ratio(q, f) for f in FORMATS}
        rows.append([q] + [f"{x:.4f}" if x else "—" for x in ratios[q].values()])
    R.table(["квартал", "Пятёрочка", "Перекрёсток", "Чижик", "Итого X5"], rows)
    R.p("Перекрёсток до 2024Q2 — другое определение в старом databook (скачок 1,10 → 1,14 без смены НДС) — не используется.")

    R.h("2. Скачок отношения г/г (клин НДС) — 2026 против 2025")
    jumps = {}
    for f in FORMATS:
        jumps[f] = {q: ratios[q][f] / ratios[q.replace("2026", "2025")][f] - 1.0 for q in ("2026Q1", "2026Q2")}
    # спокойный год: 2025 к 2024 (итого X5 — со 2 кв.: до 2024Q2 «Перекрёсток» в другом определении)
    calm = {"pyaterochka": ("2025Q1", "2025Q2", "2025Q3", "2025Q4"), "chizhik": ("2025Q1", "2025Q2", "2025Q3", "2025Q4"),
            "total": ("2025Q2", "2025Q3", "2025Q4")}
    stable = {f: [ratios[q][f] / ratios[q.replace("2025", "2024")][f] - 1.0 for q in qq] for f, qq in calm.items()}
    R.table(["формат", "1 кв. 2026 г/г", "2 кв. 2026 г/г", "2025 г/г (спокойный год), мин…макс"],
            [[f, f"{100 * jumps[f]['2026Q1']:+.2f} %", f"{100 * jumps[f]['2026Q2']:+.2f} %",
              (f"{100 * min(stable[f]):+.2f}…{100 * max(stable[f]):+.2f} %" if f in stable else "—")] for f in FORMATS])
    wedge = wedge_2026()
    vat_effect = 1.0 / (1.0 + wedge) - 1.0

    R.h("3. Проверка структурой ставок (10 % и 20→22 %)")
    base = sum(ratios[q]["total"] for q in ("2025Q1", "2025Q2", "2025Q3", "2025Q4")) / 4.0
    # 1/отношение = w/1,20 + (1 − w)/1,10  →  доля товаров по основной ставке (по продажам с НДС)
    w = (1 / 1.10 - 1 / base) / (1 / 1.10 - 1 / 1.20)
    implied = 1.0 / (w / 1.22 + (1 - w) / 1.10) / base - 1.0
    R.p(f"Отношение 2025 г. (среднее кварталов): {base:.4f} → доля товаров по основной ставке w = {100 * w:.1f} % "
        f"(верхняя оценка: в чеке ещё промокоды и бонусы).")
    R.p(f"Скачок при w и ставке 22 %: {100 * implied:+.2f} %; измеренный (итого X5, среднее 1–2 кв.): {100 * wedge:+.2f} %.")
    lo = lambda f: 100 * min(jumps[f].values())
    hi = lambda f: 100 * max(jumps[f].values())
    R.p(f"«Чижик» — меньше ({lo('chizhik'):+.2f}…{hi('chizhik'):+.2f} %): в корзине больше базовых товаров по 10 %; "
        f"«Пятёрочка» {lo('pyaterochka'):+.2f}…{hi('pyaterochka'):+.2f} %, «Перекрёсток» {lo('perekrestok'):+.2f}…{hi('perekrestok'):+.2f} %.")

    R.h("4. Книга")
    R.p(f"revenue.vat_effect 2П2026 = 1/(1 + {100 * wedge:.3f} %) − 1 = {100 * vat_effect:.3f} % → {round(vat_effect, 4)}; "
        "с 2027 г. обе базы при 22 % — 0 (LT).")
    R.p("Смысл: LFL-чек книги (k × прод. ИПЦ + сдвиг) — с НДС, как отчитывается X5 и как меряет Росстат; выручка — без НДС.")
    R.data = {"ratios": {q: {f: (r6(x) if x else None) for f, x in d.items()} for q, d in ratios.items()},
              "jumps": {f: {q: r6(x) for q, x in d.items()} for f, d in jumps.items()},
              "wedge": r6(wedge), "vat_effect": r6(vat_effect), "vat_effect_book": round(vat_effect, 4),
              "w_standard_rate": r6(w), "implied_jump_by_w": r6(implied)}
    R.save("vat")


if __name__ == "__main__":
    main()
