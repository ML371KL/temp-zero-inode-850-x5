"""A-R8. Рост прочей (нерозничной) выручки сверх площади и LFL: revenue.other_growth.

Прочая выручка = выручка − чистая розничная выручка (ЧРВ форматов, в которой уже сидят экспресс-доставка и
онлайн «из магазинов»): опт франчайзи «ОКОЛО», 5Post, «X5 Транспорт», реклама и прочие услуги, с мая–июля
2026 г. — дистрибьюторы «ВКТ» и «Город 77». В модели (MODEL §4.2) это множитель (1 + other) к выручке:
(1 + рост выручки)/(1 + рост ЧРВ) − 1. Цифровые продажи внутри форматов отдельно не прибавляются.

Выход: other_out.txt / other_out.json.
"""
from __future__ import annotations

from common import Report, half_panel, pc, r6

OTHER_GROWTH = {"2026H2": 0.003, "2027": 0.0015, "LT": 0.0, "LT_from": 2029}


def main() -> None:
    R = Report()
    hp = half_panel()
    R.h("1. Прочая выручка (выручка − ЧРВ) по полугодиям, млрд ₽")
    rows, data = [], {}
    for p in sorted(hp):
        if p < "2023H1":
            continue
        h = hp[p]
        oth = (h["rev"] - h["nrs"]) / 1000
        base = f"{int(p[:4]) - 1}{p[4:]}"
        g_oth = oth / ((hp[base]["rev"] - hp[base]["nrs"]) / 1000) - 1 if base in hp else None
        factor = (1 + h["g_rev"]) / (1 + h["g_nrs"]) - 1
        data[p] = {"other": oth, "share": oth / (h["rev"] / 1000), "g_other": g_oth, "factor": factor}
        rows.append([p, round(oth, 1), pc(oth * 1000 / h["rev"]), pc(g_oth) if g_oth is not None else "—",
                     pc(h["g_nrs"]), pc(h["g_rev"]), f"{100 * factor:+.2f}"])
    R.table(["полугодие", "прочая", "доля выручки, %", "рост г/г, %", "рост ЧРВ, %", "рост выручки, %", "вклад (1+g)/(1+g_ЧРВ)−1, п.п."], rows)
    R.p("Старый databook (до 2024Q1) и новый считают ЧРВ по-разному (в старом прочее меньше): ряд однороден с 2024H1.")

    R.h("2. Путь книги")
    s25 = data["2025H2"]["share"]
    g_nrs = 0.085
    for g_o in (0.15, 0.25, 0.35):
        f = (1 + s25 * (g_o - g_nrs) / (1 + g_nrs))
        R.p(f"2П2026: доля прочей 2П2025 {pc(s25)} %, её рост {pc(g_o, 0)} % при росте ЧРВ {pc(g_nrs, 1)} % → вклад {100 * (f - 1):+.2f} п.п.")
    R.p("Рост прочей выручки тормозит (+96 % → +48 % → +30 % г/г по полугодиям 2025H1–2026H1), но в 2П2026 добавляются "
        "«ВКТ» (май 2026) и «Город 77» (закрыта 20.07.2026) — их размер не раскрыт.")
    R.p(f"Книга: {OTHER_GROWTH} — 2П2026 +0,3 п.п. (рост прочей ≈25 %), 2027 +0,15 п.п. (доля ≈1,8 %, рост ≈15 % при ЧРВ ≈8 %), "
        "затухание к нулю к 2029 г.; в терминал не входит (MODEL §6: g — без прочего).")
    R.data = {"other_growth": OTHER_GROWTH, "history": {p: {k: (r6(x) if x is not None else None) for k, x in d.items()} for p, d in data.items()}}
    R.save("other")


if __name__ == "__main__":
    main()
