"""(5) «Что даст отчёт» за 2П2026: ожидание модели, предсказательный разброс, значения demo_values (MODEL §12).

Ожидание маржи 2П2026 = Σ P(режим)·цель(режим, 2026H2) + ρ·отклонение якоря (MODEL §4.3, сезон 0);
предсказательная дисперсия = σ²(1 − ρ²) + разброс целей режимов на 2026H2. Сетка demo_values — равный шаг
вокруг ожидания (урок Магнита: таблица, центрированная не на ожидании, вводила в заблуждение), покрытие ≈ ±2 sd.
Для каждого значения — вероятности режимов по правилу A-P2u (§10) и сдвиг E[m_LT]; наивные эталоны.
Дополнительно: ошибка частичного наблюдения по факту 3-го квартала (se для joint.regime_update.observations).

Запуск: python -B next_report.py → next_report_out.json, печать. Нужны paths_out.json и refclass_out.json.
"""
from __future__ import annotations

import json
import math
import statistics as st

from common import HERE, REGIMES, book_rule_params, history, pct, regime_update, write_json

SIGMA, RHO = book_rule_params()          # A-P2u σ и A-C4 ρ (series.py)
CAP = 0.10
STEP = 0.003
N_VALUES = 7


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def main():
    H = history()
    halves, qs = H["halves"], H["quarters"]
    pa, rc = load("paths_out.json"), load("refclass_out.json")
    prior = rc["prob_book"]
    lt = {r: rc["lt_pp"][r] / 100 for r in REGIMES}
    tg = pa["targets"]
    t_a, dev0 = pa["t_a"], pa["dev_anchor"]
    fact = t_a + dev0
    e_t = sum(prior[r] * tg[r]["2026H2"] for r in REGIMES)
    var_t = sum(prior[r] * (tg[r]["2026H2"] - e_t) ** 2 for r in REGIMES)
    expect = e_t + RHO * dev0
    sd = math.sqrt(SIGMA ** 2 * (1 - RHO ** 2) + var_t)
    print(f"Ожидание модели 2П2026: {pct(e_t, 3)} % (цели) {RHO*dev0*100:+.3f} п.п. (хвост якоря) = {pct(expect, 3)} %; "
          f"предсказательный sd {sd*100:.3f} п.п.")
    by_regime = {r: tg[r]["2026H2"] + RHO * dev0 for r in REGIMES}
    print("  по режимам: " + ", ".join(f"{r} {pct(v, 3)}" for r, v in by_regime.items()))

    c = round(expect / 0.001) * 0.001
    vals = [round(c + (i - N_VALUES // 2) * STEP, 4) for i in range(N_VALUES)]
    print(f"demo_values: {vals}  (центр {pct(c, 1)} %, шаг {STEP*100:.1f} п.п., края ±{(N_VALUES//2)*STEP/sd:.2f} sd)")

    rows = []
    e_prior = sum(prior[r] * lt[r] for r in REGIMES)
    print("  факт 2П2026 → вероятности режимов, % (стресс / дно / частичный / полный); E[m_LT]; год 2026 при росте 2П 10,5 %")
    r_h2 = halves["2025H2"]["rev"] * (halves["2026H1"]["rev"] / halves["2025H1"]["rev"])
    for v in vals:
        post = regime_update(prior, tg, "2026H1", fact, [{"period": "2026H2", "value": v, "se": 0.0}], SIGMA, RHO, CAP)
        e_post = sum(post[r] * lt[r] for r in REGIMES)
        year = (halves["2026H1"]["ebitda"] + v * r_h2) / (halves["2026H1"]["rev"] + r_h2)
        rows.append({"margin": v, "posterior": post, "e_lt": e_post, "d_e_lt": e_post - e_prior, "year_2026": year})
        print(f"  {pct(v, 1)} % → " + " / ".join(f"{post[r]*100:5.1f}" for r in REGIMES)
              + f";  E[m_LT] {pct(e_post, 3)} ({(e_post-e_prior)*100:+.3f});  год {pct(year)} %")
    # «среднее двух полугодий» — простое среднее маржей, как в выпуске (model/journal.py, next_report.benchmarks)
    bench = {"как 2П2025": halves["2025H2"]["m"], "среднее 2П2025 и 1П2026": (halves["2025H2"]["m"] + halves["2026H1"]["m"]) / 2,
             "как 1П2026": halves["2026H1"]["m"]}
    print("Наивные эталоны: " + ", ".join(f"{k} {pct(v)} %" for k, v in bench.items()))
    need = (0.06 * (halves["2026H1"]["rev"] + r_h2) - halves["2026H1"]["ebitda"]) / r_h2
    print(f"Прогноз компании «6+ %» на 2026 г. требует 2П ≥ {pct(need)} % (при росте выручки 2П как в 1П, 10,5 %)")

    # частичное наблюдение по 3-му кварталу: маржа 2П = w3·m3 + w4·m4, неизвестна m4
    print("\nЧастичное наблюдение 2П по факту 3-го квартала (se для observations):")
    d, w4s = [], []
    for y in range(2011, 2026):
        q3, q4 = qs[f"{y}Q3"], qs[f"{y}Q4"]
        d.append((q4["m"] - q3["m"]) * 100)
        w4s.append(q4["rev"] / (q3["rev"] + q4["rev"]))
    recent = d[-9:]
    w4 = st.mean(w4s[-5:])
    for lab, arr in (("2011–2025", d), ("2017–2025", recent)):
        print(f"  4 кв. − 3 кв., п.п., {lab}: среднее {st.mean(arr):+.2f}, sd {st.stdev(arr):.2f}")
    se_q3 = w4 * st.stdev(recent) / 100
    print(f"  доля 4 кв. в выручке 2П (2021–2025) {w4:.3f}; наблюдение = m3 + {w4:.3f} × ({st.mean(recent):+.2f} п.п.), se = {w4:.3f} × {st.stdev(recent):.2f} = {se_q3*100:.2f} п.п. "
          f"— сопоставимо с σ шага правила ({SIGMA*math.sqrt(1-RHO**2)*100:.2f} п.п.): вес такого наблюдения w ≈ "
          f"{(SIGMA**2*(1-RHO**2))/((SIGMA**2*(1-RHO**2))+se_q3**2):.2f}")
    write_json("next_report_out.json", {"expectation": expect, "expectation_targets": e_t, "tail": RHO * dev0, "sd": sd,
                                        "by_regime": by_regime, "demo_values": vals, "rows": rows, "benchmarks": bench,
                                        "h2_needed_for_6pct": need,
                                        "q3_partial": {"w4": w4, "mean_q4_minus_q3_pp_2017_2025": st.mean(recent),
                                                       "sd_pp_2017_2025": st.stdev(recent), "se": se_q3}})
    print("Записано: next_report_out.json")


if __name__ == "__main__":
    main()
