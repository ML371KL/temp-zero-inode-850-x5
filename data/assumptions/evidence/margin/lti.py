"""(4) LTI — расход на программу долгосрочного премирования менеджмента, доля выручки (margin.lti_pct, MODEL §4.4).

Скорр. EBITDA X5 не включает LTI; модель вычитает его отдельной денежной строкой. LTI X5 — денежное долгосрочное
вознаграждение (МСФО (IAS) 19, обязательство в мосте), не опционы. Ряд — строка «LTI и прочее вознаграждение»
листа EBITDA databook (с 2023Q1 по кварталам, 2021–2025 по годам; в 2025 г. — «LTI и прочее вознаграждение
управленческого персонала», с 2026 г. — «LTI»).

Запуск: python -B lti.py → lti_out.json, печать.
"""
from __future__ import annotations

import statistics as st

from common import half_index, history, write_json


def main():
    H = history()
    yrs, halves, qs = H["years"], H["halves"], H["quarters"]
    print("LTI, % выручки:")
    ann = {y: yrs[y]["lti"] / yrs[y]["rev"] for y in sorted(yrs) if yrs[y]["lti"] is not None}
    print("  годы: " + ", ".join(f"{y} {v*100:.3f}" for y, v in ann.items()))
    hv = {p: halves[p]["lti"] / halves[p]["rev"] for p in sorted(halves, key=half_index) if halves[p]["lti"] is not None}
    print("  полугодия: " + ", ".join(f"{p} {v*100:.3f}" for p, v in hv.items()))
    qv = {p: qs[p]["lti"] / qs[p]["rev"] for p in sorted(qs) if qs[p]["lti"] is not None}
    print("  кварталы: " + ", ".join(f"{p} {v*100:.3f}" for p, v in qv.items()))
    ltm_l = halves["2025H2"]["lti"] + halves["2026H1"]["lti"]
    ltm_r = halves["2025H2"]["rev"] + halves["2026H1"]["rev"]
    ltm = ltm_l / ltm_r
    last4q = sum(qs[p]["lti"] for p in ("2025Q3", "2025Q4", "2026Q1", "2026Q2")) / sum(qs[p]["rev"] for p in ("2025Q3", "2025Q4", "2026Q1", "2026Q2"))
    mean_21_24 = st.mean(ann[y] for y in (2021, 2022, 2023, 2024))
    h_list = list(hv.values())
    print(f"  LTM 2К2026 {ltm*100:.3f} % ({ltm_l:.2f} млрд ₽); 2021–2024 среднее {mean_21_24*100:.3f} %; 2025 {ann[2025]*100:.3f} %; "
          f"1П2026 {hv['2026H1']*100:.3f} %; 2К2026 {qv['2026Q2']*100:.3f} %")
    print(f"  диапазон полугодий 2023H1–2026H1: {min(h_list)*100:.3f}–{max(h_list)*100:.3f} %")
    center = round(ltm, 4)
    low, high = 0.0012, 0.0026
    print(f"Книга: margin.lti_pct = {center} (LTM после расширения программ в 2025–2026 гг.); ось {low}–{high}: "
          f"низ ≈ среднее 2021–2024 ({mean_21_24*100:.2f} %) — сворачивание программы; верх — продолжение расширения темпом 2024→LTM "
          f"(+{(ltm-ann[2024])*100:.2f} п.п. за 1,5 года), ещё на треть")
    write_json("lti_out.json", {"annual": ann, "halves": hv, "quarters": qv, "ltm": ltm, "ltm_bln": ltm_l,
                                "mean_2021_2024": mean_21_24, "book": center, "axis": [low, high]})
    print("Записано: lti_out.json")


if __name__ == "__main__":
    main()
