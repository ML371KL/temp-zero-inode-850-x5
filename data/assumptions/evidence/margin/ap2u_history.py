"""Правило A-P2u (MODEL §10) на истории X5 2019–2026: какие сдвиги вероятностей режимов дал бы каждый отчёт.

Параметры — книги (σ, ρ, предел сдвига; вероятности и цели — paths_out.json / refclass_out.json).
Три прогона:
  1. «Один отчёт»: для каждого полугодия t = 2019H1…2026H1 книга перезаякорена на t−1 (цель на якоре — уровень
     фильтра series.py на t−1, отклонение якоря — факт t−1 минус уровень), цели режимов на t — уровень + сдвиг
     режима книги на горизонте одного полугодия; априорные вероятности — книги; наблюдение — факт t.
  2. «Подряд»: якорь 2018H2 (и 2021H2 — пик), сдвиги режимов книги по горизонту h, все факты по очереди с пределом
     после каждого.
  3. Предсказательное распределение отчёта 2П2026 по самой модели (Монте-Карло): сколько раскрывает один отчёт.
Каждый прогон — для траекторий книги (линейный сход) и для «быстрых» (по информационной границе paths.py).

Запуск: python -B ap2u_history.py → ap2u_out.json, печать.
"""
from __future__ import annotations

import json
import math
import random
import statistics as st

from common import HERE, REGIMES, half_index, half_name, history, path_value, regime_update, write_json

SIGMA = 0.0053          # A-P2u: joint.regime_update.sigma_pp (series.py, п. 5)
RHO = -0.40             # A-C4 = joint.regime_update.rho
CAP = 0.10              # предел сдвига за наблюдение
MC_N, MC_SEED = 20000, 20260928


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def offsets(targets: dict, t_a: float, h: int) -> dict:
    """Сдвиг цели режима от цели якоря на горизонте h полугодий (по траекториям книги от 2026H1)."""
    per = half_name(half_index("2026H1") + h)
    return {r: path_value(targets[r], per) - t_a for r in REGIMES}


def e_lt(p: dict, lt: dict) -> float:
    return sum(p[r] * lt[r] for r in REGIMES)


def one_report(halves, levels, targets, t_a_book, prior, lt, label):
    print(f"\n1. Один отчёт ({label}): сдвиг вероятностей, п.п. (стресс / дно / частичный / полный), ΔE[m_LT] п.п.")
    off = offsets(targets, t_a_book, 1)
    rows = []
    for t in [p for p in sorted(halves, key=half_index) if "2019H1" <= p <= "2026H1"]:
        prev = half_name(half_index(t) - 1)
        lvl = levels[prev]["level"] / 100
        tg = {r: {prev: lvl, t: lvl + off[r]} for r in REGIMES}
        post = regime_update(prior, tg, prev, halves[prev]["m"], [{"period": t, "value": halves[t]["m"], "se": 0.0}], SIGMA, RHO, CAP)
        sh = {r: post[r] - prior[r] for r in REGIMES}
        de = e_lt(post, lt) - e_lt(prior, lt)
        surprise = halves[t]["m"] - (lvl + RHO * (halves[prev]["m"] - lvl))
        rows.append({"period": t, "fact": halves[t]["m"], "level_prev": lvl, "surprise": surprise, "shift": sh, "d_elt": de})
        print(f"  {t}: факт {halves[t]['m']*100:5.2f} %, ожидание при уровне {lvl*100:5.2f} → сюрприз {surprise*100:+5.2f} п.п.; "
              + " / ".join(f"{sh[r]*100:+5.1f}" for r in REGIMES) + f";  ΔE {de*100:+.3f}")
    mx = max(max(abs(v) for v in r_["shift"].values()) for r_ in rows)
    mean_abs = st.mean(max(abs(v) for v in r_["shift"].values()) for r_ in rows)
    sd_de = st.pstdev([r_["d_elt"] for r_ in rows])
    print(f"  итог: наибольший сдвиг {mx*100:.1f} п.п., средний наибольший {mean_abs*100:.1f} п.п., предел {CAP*100:.0f} п.п. "
          f"{'достигался' if mx >= CAP - 1e-9 else 'не достигался'}; sd ΔE[m_LT] {sd_de*100:.3f} п.п.")
    return {"rows": rows, "max_shift": mx, "mean_max_shift": mean_abs, "sd_d_elt": sd_de}


def sequential(halves, levels, targets, t_a_book, prior, lt, anchor, label):
    print(f"\n2. Подряд с якоря {anchor} ({label}): вероятности после каждого отчёта, %  (стресс / дно / частичный / полный), E[m_LT]")
    lvl = levels[anchor]["level"] / 100
    pers = [p for p in sorted(halves, key=half_index) if half_index(p) > half_index(anchor)]
    tg = {r: {anchor: lvl} for r in REGIMES}
    for p in pers:
        h = half_index(p) - half_index(anchor)
        off = offsets(targets, t_a_book, h)
        for r in REGIMES:
            tg[r][p] = lvl + off[r]
    obs = [{"period": p, "value": halves[p]["m"], "se": 0.0} for p in pers]
    trace = []
    regime_update(prior, tg, anchor, halves[anchor]["m"], obs, SIGMA, RHO, CAP, trace=trace)
    lt_hist = {r: lvl + offsets(targets, t_a_book, 10)[r] for r in REGIMES}
    print(f"  уровень на якоре {lvl*100:.2f} %; цели LT режимов на этой шкале: " + ", ".join(f"{r} {lt_hist[r]*100:.2f}" for r in REGIMES))
    rows = []
    for tr in trace:
        capped = any(abs(tr["post"][r] - tr["prior"][r]) >= CAP - 1e-9 for r in REGIMES)
        rows.append({"period": tr["period"], "fact": tr["value"], "post": tr["post"], "capped": capped,
                     "e_lt_shift_pp": (e_lt(tr["post"], lt) - e_lt(prior, lt)) * 100})
        print(f"  {tr['period']}: {tr['value']*100:5.2f} % → " + " / ".join(f"{tr['post'][r]*100:5.1f}" for r in REGIMES)
              + f"   E[m_LT] книги {e_lt(tr['post'], lt)*100:.2f} %" + ("  (предел)" if capped else ""))
    return {"anchor": anchor, "level": lvl, "rows": rows}


def monte_carlo(targets, t_a_book, dev0, prior, lt, label):
    rng = random.Random(MC_SEED)
    reg = list(REGIMES)
    cum = []
    acc = 0.0
    for r in reg:
        acc += prior[r]
        cum.append(acc)
    innov_sd = SIGMA * math.sqrt(1 - RHO ** 2)
    big, capn, des, mx_list = 0, 0, [], []
    for _ in range(MC_N):
        u = rng.random()
        r0 = reg[next(i for i, c in enumerate(cum) if u <= c)]
        obs = targets[r0]["2026H2"] + RHO * dev0 + rng.gauss(0, innov_sd)
        post = regime_update(prior, targets, "2026H1", t_a_book + dev0, [{"period": "2026H2", "value": obs, "se": 0.0}], SIGMA, RHO, CAP)
        m = max(abs(post[r] - prior[r]) for r in reg)
        mx_list.append(m)
        big += m >= 0.05
        capn += m >= CAP - 1e-9
        des.append(e_lt(post, lt) - e_lt(prior, lt))
    res = {"mean_max_shift": st.mean(mx_list), "p90_max_shift": sorted(mx_list)[int(0.9 * MC_N)], "p_shift_ge_5pp": big / MC_N,
           "p_cap": capn / MC_N, "sd_d_elt": st.pstdev(des)}
    print(f"  {label}: средний наибольший сдвиг {res['mean_max_shift']*100:.2f} п.п., P90 {res['p90_max_shift']*100:.2f}; "
          f"P(сдвиг ≥ 5 п.п.) {res['p_shift_ge_5pp']*100:.1f} %; P(предел) {res['p_cap']*100:.1f} %; sd ΔE[m_LT] {res['sd_d_elt']*100:.3f} п.п.")
    return res


def kalman_gain(rho, sig, q):
    """Установившийся коэффициент усиления уровня фильтра series.py: какая доля сюрприза считается сдвигом уровня."""
    P = [[1.0, 0.0], [0.0, sig * sig]]
    for _ in range(500):
        P = [[P[0][0] + q * q, rho * P[0][1]], [rho * P[1][0], rho * rho * P[1][1] + sig * sig * (1 - rho * rho)]]
        F = P[0][0] + P[0][1] + P[1][0] + P[1][1]
        K = [(P[0][0] + P[0][1]) / F, (P[1][0] + P[1][1]) / F]
        P = [[P[0][0] - K[0] * (P[0][0] + P[1][0]), P[0][1] - K[0] * (P[0][1] + P[1][1])],
             [P[1][0] - K[1] * (P[0][0] + P[1][0]), P[1][1] - K[1] * (P[0][1] + P[1][1])]]
    P1 = [[P[0][0] + q * q, rho * P[0][1]], [rho * P[1][0], rho * rho * P[1][1] + sig * sig * (1 - rho * rho)]]
    F = P1[0][0] + P1[0][1] + P1[1][0] + P1[1][1]
    return (P1[0][0] + P1[0][1]) / F, math.sqrt(F)


def main():
    H = history()
    halves = H["halves"]
    ser, rc, pa = load("series_out.json"), load("refclass_out.json"), load("paths_out.json")
    levels = ser["levels"]
    prior = rc["prob_book"]
    lt = {r: rc["lt_pp"][r] / 100 for r in REGIMES}
    t_a = pa["t_a"]
    book, fast = pa["targets"], pa["fast_targets"]
    print(f"Параметры правила: σ {SIGMA*100:.2f} п.п., ρ {RHO:+.2f}, предел {CAP*100:.0f} п.п.; априорные {', '.join(f'{r} {prior[r]*100:.1f}' for r in REGIMES)} %")
    for lab, tg in (("книга", book), ("быстрые", fast)):
        off = offsets(tg, t_a, 1)
        print(f"  сдвиги целей на 2026H2 от цели якоря ({lab}), п.п.: " + ", ".join(f"{r} {off[r]*100:+.3f}" for r in REGIMES))

    out = {"params": {"sigma": SIGMA, "rho": RHO, "cap": CAP}, "one_report": {}, "sequential": {}, "mc": {}}
    for lab, tg in (("книга", book), ("быстрые", fast)):
        out["one_report"][lab] = one_report(halves, levels, tg, t_a, prior, lt, lab)
    for lab, tg in (("книга", book), ("быстрые", fast)):
        for anc in ("2018H2", "2021H2"):
            out["sequential"][f"{lab} {anc}"] = sequential(halves, levels, tg, t_a, prior, lt, anc, lab)

    print("\n3. Отчёт 2П2026 по предсказательному распределению самой модели (Монте-Карло, 20 000):")
    for lab, tg in (("книга", book), ("быстрые", fast)):
        out["mc"][lab] = monte_carlo(tg, t_a, pa["dev_anchor"], prior, lt, lab)
    kl, fsd = kalman_gain(ser["base"]["rho"], ser["base"]["sigma_pp"] / 100, ser["base"]["q_pp"] / 100)
    info = kl * fsd
    out["kf"] = {"level_gain": kl, "innov_sd": fsd, "sd_level_revision": info}
    print(f"  информационная граница: фильтр истории переносит в уровень долю {kl:.2f} сюрприза (sd сюрприза {fsd*100:.2f} п.п.) — "
          f"sd пересмотра уровня за отчёт {info*100:.3f} п.п.; у правила: книга {out['mc']['книга']['sd_d_elt']*100:.3f}, быстрые {out['mc']['быстрые']['sd_d_elt']*100:.3f} п.п.")
    write_json("ap2u_out.json", out)
    print("\nЗаписано: ap2u_out.json")


if __name__ == "__main__":
    main()
