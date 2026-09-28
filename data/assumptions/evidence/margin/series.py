"""(3) σ и ρ отклонения маржи X5 вокруг режимного уровня; сезонность; уровень на якоре 2026H1.

Ряд: скорр. EBITDA до МСФО 16 / выручка по полугодиям 2011H1–2026H1 (31 точка; inputs/x5_margin_history.json).
Модель ряда — та же, что у книги (MODEL §4.3): маржа = уровень режима + сезон + отклонение AR(1).
Уровень режима в истории неизвестен, поэтому он оценивается вместе с отклонением: уровень — случайное
блуждание с шагом q за полугодие (медленный «режим»), отклонение — AR(1) (ρ, стационарное σ).
Оценка — максимум правдоподобия фильтра Калмана на сетке, интервалы — профиль правдоподобия (χ²₁, 95 %).
Проверки устойчивости: другой период, выбросы, скользящие средние вместо фильтра; смещение оценки ρ —
симуляцией (флаг --sim).

Запуск: python -B series.py [--sim]  → series_out.json, печать в консоль (series.out — копия вывода).
"""
from __future__ import annotations

import argparse
import math
import random
import statistics as st

from common import fit_level_ar1, frange, half_index, history, kalman_level_ar1, pct, write_json

RHOS = frange(-0.90, 0.90, 0.05)
SIGS = frange(0.20, 1.20, 0.02)       # п.п.
QS = frange(0.00, 0.40, 0.02)         # п.п. за полугодие
CHI2_95 = 3.841 / 2                   # падение логарифма правдоподобия на границе 95 % профиля


def profile_interval(prof: dict) -> tuple[float, float]:
    mx = max(prof.values())
    ok = [k for k, v in prof.items() if mx - v <= CHI2_95 + 1e-12]
    return min(ok), max(ok)


def fit(y, label, season=None, sig_profile=False):
    best, prof = fit_level_ar1(y, RHOS, SIGS, QS, season)
    lo, hi = profile_interval(prof)
    res = {"label": label, "n": len(y), "ll": best[0], "rho": best[1], "sigma_pp": best[2], "q_pp": best[3],
           "rho_ci95": [lo, hi], "rho_profile": {f"{k:+.2f}": round(v - best[0], 2) for k, v in sorted(prof.items())}}
    if sig_profile:
        sp = {}
        for sg in SIGS:
            b = max(kalman_level_ar1(y, r, sg, q, season)[0] for r in frange(-0.9, 0.9, 0.1) for q in frange(0, 0.4, 0.04))
            sp[sg] = b
        res["sigma_ci95"] = list(profile_interval(sp))
    print(f"{label:<44} n={len(y):2d}  ρ {best[1]:+.2f} (95 %: {lo:+.2f}…{hi:+.2f})  σ {best[2]:.2f} п.п.  q {best[3]:.2f} п.п./полуг.")
    return res


def acf1(x):
    mu = st.mean(x)
    return sum((x[i] - mu) * (x[i - 1] - mu) for i in range(1, len(x))) / sum((v - mu) ** 2 for v in x)


def ma_dev(y, w):
    return [y[i] - st.mean(y[i - w:i + w + 1]) for i in range(w, len(y) - w)]


def simulate(n_rep=40, seed=20260928):
    """Смещение оценки ρ на рядах той же длины: уровень — блуждание q = 0,24, σ = 0,46 п.п."""
    rng = random.Random(seed)
    rh, sg, qq = frange(-0.9, 0.9, 0.1), frange(0.2, 1.0, 0.05), frange(0.0, 0.4, 0.04)
    out = {}
    for rho0 in (0.3, 0.0, -0.4):
        est = []
        for _ in range(n_rep):
            L, d, y = 6.0, rng.gauss(0, 0.46), []
            for t in range(31):
                if t:
                    L += rng.gauss(0, 0.24)
                    d = rho0 * d + rng.gauss(0, 0.46 * math.sqrt(1 - rho0 * rho0))
                y.append(L + d)
            est.append(fit_level_ar1(y, rh, sg, qq)[0][1])
        out[f"{rho0:+.1f}"] = {"mean": st.mean(est), "median": st.median(est), "sd": st.stdev(est)}
        print(f"  истинное ρ {rho0:+.1f}: средняя оценка {st.mean(est):+.3f}, медиана {st.median(est):+.3f}, sd {st.stdev(est):.3f} ({n_rep} рядов)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="симуляция смещения оценки ρ (≈2–4 мин)")
    a = ap.parse_args()
    H = history()
    halves = H["halves"]
    per = [p for p in sorted(halves, key=half_index) if p >= "2011H1"]
    y = [halves[p]["m"] * 100 for p in per]                      # п.п.
    print("Полугодия, скорр. маржа до МСФО 16, %:")
    for i in range(0, len(per), 2):
        row = [f"{per[j]} {y[j]:.2f}" for j in range(i, min(i + 2, len(per)))]
        print("  " + "   ".join(row))

    print("\n1. Уровень (блуждание) + отклонение AR(1), максимум правдоподобия:")
    res = {"base": fit(y, "2011H1–2026H1, сезон 0", sig_profile=True)}
    best_s = None
    for s in frange(-0.30, 0.30, 0.05):
        sea = [s if p.endswith("H1") else -s for p in per]
        b, _ = fit_level_ar1(y, frange(-0.9, 0.9, 0.05), frange(0.3, 0.7, 0.02), frange(0.1, 0.35, 0.02), sea)
        if best_s is None or b[0] > best_s[0]:
            best_s = (b[0], s, b[1], b[2], b[3])
    sprof = {}
    for s in frange(-0.40, 0.40, 0.05):
        sea = [s if p.endswith("H1") else -s for p in per]
        sprof[s] = fit_level_ar1(y, frange(-0.9, 0.9, 0.1), frange(0.3, 0.7, 0.04), frange(0.1, 0.35, 0.05), sea)[0][0]
    s_lo, s_hi = profile_interval(sprof)
    res["season_free"] = {"s_pp": best_s[1], "rho": best_s[2], "sigma_pp": best_s[3], "q_pp": best_s[4],
                          "ll_gain_vs_s0": best_s[0] - res["base"]["ll"], "s_ci95": [s_lo, s_hi]}
    print(f"{'сезон свободен (+s в 1П, −s во 2П)':<44} s {best_s[1]:+.2f} п.п. (95 %: {s_lo:+.2f}…{s_hi:+.2f}), ρ {best_s[2]:+.2f}, σ {best_s[3]:.2f}, прирост ll {best_s[0]-res['base']['ll']:.2f}")
    i17 = per.index("2017H1")
    res["from_2017"] = fit(y[i17:], "2017H1–2026H1 (как лист Магнита)")
    y2 = y[:]
    j = per.index("2015H2")
    y2[j] = (y[j - 1] + y[j + 1]) / 2
    res["no_2015H2"] = fit(y2, "2015H2 (8,78 %) заменён средним соседей")
    y3 = y[:]
    for p in ("2022H1", "2022H2"):
        k = per.index(p)
        y3[k] = (y[k - 1] + y[k + 1]) / 2
    res["no_2022"] = fit(y3, "2022H1 и 2022H2 заменены средним соседей")

    print("\n2. Проверка без фильтра: отклонение от центрированного скользящего среднего:")
    res["ma"] = {}
    for w in (2, 3):
        dv = ma_dev(y, w)
        res["ma"][f"window_{2*w+1}"] = {"sd_pp": st.pstdev(dv), "acf1": acf1(dv), "n": len(dv)}
        print(f"  окно {2*w+1} полугодий: sd {st.pstdev(dv):.2f} п.п., автокорреляция 1-го порядка {acf1(dv):+.2f} (n = {len(dv)})")

    print("\n3. Сезонность «2П − 1П», п.п.:")
    diffs = {yy: (halves[f'{yy}H2']['m'] - halves[f'{yy}H1']['m']) * 100 for yy in range(2011, 2026)}
    vals = list(diffs.values())
    se = st.stdev(vals) / math.sqrt(len(vals))
    eras = {"2011–2016": range(2011, 2017), "2017–2024": range(2017, 2025), "2023–2025": range(2023, 2026)}
    res["season_h2_minus_h1"] = {"by_year": {str(k): round(v, 3) for k, v in diffs.items()}, "mean": st.mean(vals),
                                 "median": st.median(vals), "sd": st.stdev(vals), "se": se, "t": st.mean(vals) / se,
                                 "eras": {k: st.mean(diffs[yy] for yy in r) for k, r in eras.items()}}
    print("  " + ", ".join(f"{k}: {v:+.2f}" for k, v in diffs.items()))
    print(f"  среднее {st.mean(vals):+.2f}, медиана {st.median(vals):+.2f}, sd {st.stdev(vals):.2f}, t = {st.mean(vals)/se:+.2f}; "
          + "; ".join(f"{k}: {v:+.2f}" for k, v in res['season_h2_minus_h1']['eras'].items()))

    print("\n4. Уровень на якоре 2026H1 (фильтр при оценках п. 1):")
    b = res["base"]
    ll, path = kalman_level_ar1(y, b["rho"], b["sigma_pp"], b["q_pp"])
    lvl = {p: {"level": path[i][0], "dev": path[i][1], "sd_level": path[i][2], "innov": path[i][3], "F": path[i][4]}
           for i, p in enumerate(per)}
    for p in per[-8:]:
        print(f"  {p}: факт {y[per.index(p)]:.2f}, уровень {lvl[p]['level']:.3f} (±{lvl[p]['sd_level']:.2f}), отклонение {lvl[p]['dev']:+.3f}")
    h1, h2 = halves["2025H2"], halves["2026H1"]
    ltm = (h1["ebitda"] + h2["ebitda"]) / (h1["rev"] + h2["rev"])
    innov = [lvl[p]["innov"] for p in per[4:]]
    res["anchor"] = {"level_pp": lvl["2026H1"]["level"], "level_sd_pp": lvl["2026H1"]["sd_level"],
                     "dev_pp": y[-1] - lvl["2026H1"]["level"], "fact_pp": y[-1], "ltm_pp": ltm * 100,
                     "kf_innovation_sd_pp": st.pstdev(innov)}
    print(f"  LTM 2К2026 = {pct(ltm)} %; уровень − LTM = {lvl['2026H1']['level'] - ltm*100:+.3f} п.п.; "
          f"sd инноваций фильтра (без 4 стартовых) {st.pstdev(innov):.2f} п.п.")
    res["levels"] = {p: {k: round(v, 4) for k, v in lvl[p].items()} for p in per}

    print("\n5. σ правила A-P2u (стационарное отклонение от ПУТИ режима, MODEL §10):")
    rho, sd, q = b["rho"], b["sigma_pp"], b["q_pp"]
    # путь режима детерминирован, реальный уровень внутри режима блуждает: дисперсия шага прогноза = σ_d²(1−ρ²) + q²
    one_step = math.sqrt(sd * sd * (1 - rho * rho) + q * q)
    sigma_rule = one_step / math.sqrt(1 - rho * rho)
    res["rule"] = {"one_step_sd_pp": one_step, "sigma_rule_pp": sigma_rule}
    print(f"  шаг прогноза при известном уровне: √(σ²(1−ρ²) + q²) = {one_step:.3f} п.п.; σ правила = шаг / √(1−ρ²) = {sigma_rule:.3f} п.п. "
          f"(чистое AR(1) без блуждания уровня — {sd:.2f})")

    if a.sim:
        print("\n6. Смещение оценки ρ (симуляция, та же длина ряда):")
        res["sim_bias"] = simulate()
    write_json("series_out.json", res)
    print("\nЗаписано: series_out.json")


if __name__ == "__main__":
    main()
