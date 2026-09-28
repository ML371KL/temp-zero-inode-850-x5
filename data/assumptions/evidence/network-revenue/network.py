"""A-R7 (рост сети) и A-R7c (закрытия): чистый рост площади по тарифам low/mid/high и доля закрываемой площади.

Что считает:
  1. История сопоставимой площади 2022H2–2026H1: чистый прирост, валовые открытия и закрытия (магазины и
     площадь), годовые темпы; размер открываемого и закрываемого магазина «Пятёрочки» — МНК по кварталам.
  2. 2П2026 тремя способами: минимум по планам компании (годовой отчёт 2025, «более 2 000» открытий),
     сезонность 2025 г. (2П/1П), темп г/г 1П2026 к 1П2025 — отсюда low/mid/high на 2П2026.
  3. Стратегия-2028 (29 500+ «Пятёрочек», 5 000 «Чижиков») → чистый прирост площади 2027–2028 (тариф high).
  4. Тарифы 2027–2030 и сход к нулю к 2036 г. (терминал — нулевой чистый рост, MODEL §6); площадь 2036 г.
  5. Веса тарифов в слое «свой взгляд» (миры × режимы: joint.world_links, stress_growth) и ожидаемый путь.
  6. Доля закрываемой площади: история и путь книги.

Выход: network_out.txt / network_out.json (траектории книги network.net_growth и network.close_rate).
"""
from __future__ import annotations

from common import (Report, half_index, halves, load, network_history, path_value, pc, r6, shift_half, v)

NF = load("network_facts.json")
PL = load("plans.json")
BR = load("book_refs.json")
OC = NF["openings_closures_stores"]
FMT = ("pyaterochka", "perekrestok", "chizhik")

# --- суждения листа (значения книги; обоснование — в тексте раздела и в печати ниже) ---
NET_GROWTH = {
    "low":  {"2026H2": 0.042, "2027": 0.030, "2028": 0.025, "2029": 0.020, "2030": 0.016, "LT": 0.0, "LT_from": 2036},
    "mid":  {"2026H2": 0.048, "2027": 0.044, "2028": 0.039, "2029": 0.033, "2030": 0.027, "LT": 0.0, "LT_from": 2036},
    "high": {"2026H2": 0.055, "2027": 0.050, "2028": 0.048, "2029": 0.040, "2030": 0.034, "LT": 0.0, "LT_from": 2036},
}
CLOSE_RATE = {"2026H2": 0.025, "2027": 0.022, "LT": 0.020, "LT_from": 2028}


def q_sum(fmt: str, key: str, qs: list[str]) -> float | None:
    xs = [v(OC[q][fmt][key]) for q in qs]
    return None if any(x is None for x in xs) else float(sum(xs))


def store_size_ols() -> dict:
    """ΔS_П(кв.) = a·открыто − b·закрыто (без свободного члена), 2024Q1–2026Q2, сопоставимый базис."""
    oq = load("operating_q.json")["quarterly"]
    abf, sbf = NF["area_end_by_format"], NF["stores_end_by_format"]

    def area(q):
        # концы полугодий — факты; 1 и 3 кв. — databook («Скорр.», где есть), 2024Q1 — «как отчитано» + КЯ/Слата
        h = {"Q2": "H1", "Q4": "H2"}.get(q[4:])
        if h:
            return v(abf[q[:4] + h]["pyaterochka"])
        row = oq[q].get("restated_KY_Slata") or oq[q]["as_reported"]
        a = v(row["space_pyaterochka"])
        if q == "2024Q1":  # КЯ/Слата вне «Пятёрочки»: добавить их площадь 30.06.2024
            a += v(abf["2024H1"]["pyaterochka"]) - v(oq["2024Q2"]["as_reported"]["space_pyaterochka"])
        return a

    qs = ["2023Q4", "2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
    rows = []
    for q0, q1 in zip(qs, qs[1:]):
        rows.append((q1, area(q1) - area(q0), v(OC[q1]["pyaterochka"]["opened"]), v(OC[q1]["pyaterochka"]["closed"])))
    x1 = [r[2] for r in rows]
    x2 = [-r[3] for r in rows]
    y = [r[1] for r in rows]
    s11, s22, s12 = sum(a * a for a in x1), sum(b * b for b in x2), sum(a * b for a, b in zip(x1, x2))
    s1y, s2y = sum(a * c for a, c in zip(x1, y)), sum(b * c for b, c in zip(x2, y))
    det = s11 * s22 - s12 * s12
    a = (s1y * s22 - s2y * s12) / det
    b = (s11 * s2y - s12 * s1y) / det
    res = [c - a * p - b * q for p, q, c in zip(x1, x2, y)]
    s2 = sum(e * e for e in res) / (len(y) - 2)
    return {"rows": rows, "a": a, "b": b, "se_a": (s2 * s22 / det) ** 0.5, "se_b": (s2 * s11 / det) ** 0.5}


def main() -> None:
    R = Report()
    H = network_history()
    A, O, C = H["A"], H["O"], H["C"]
    hist = [p for p in A if half_index(p) >= half_index("2023H1")]

    R.h("1. Сопоставимая площадь, валовые открытия и закрытия (тыс. м²), годовые темпы")
    rows = []
    for p in hist:
        a0 = A[shift_half(p, -1)]
        rows.append([p, round(A[p], 1), round(A[p] - a0, 1), round(O[p], 1), round(C[p], 1),
                     pc(2 * (A[p] - a0) / a0), pc(2 * C[p] / a0), H["note"][p]])
    R.table(["полугодие", "площадь", "чистый", "валовые откр.", "закрыто", "чистый рост, год. %",
             "закрытия, год. %", "источник площади"], rows)
    yoy = {p: A[p] / A[shift_half(p, -2)] - 1 for p in hist if shift_half(p, -2) in A}
    R.p("Рост площади г/г на конец полугодия, %:", ", ".join(f"{p} {pc(x)}" for p, x in yoy.items()))
    R.p(f"«Красный Яр» и «Слата» на 31.12.2023: {H['ky_slata_area_2023_end']:.1f} тыс. м² ({1000 * H['ky_per_store']:.0f} м² на магазин); "
        f"закрытия полугодий 2022 г. — оценка {H['closed_2022_half_est']:.1f} тыс. м² (2 × 4 кв. 2022).")

    R.h("1а. Магазины: валовые открытия и закрытия по кварталам (трейдинг-апдейты)")
    qrows = []
    tu = load("tu_2022_2023.json")["quarters"]
    for q, d in tu.items():
        qrows.append([q, v(d["pyaterochka_opened"]), v(d["pyaterochka_closed"]), v(d["perekrestok_opened"]),
                      v(d["perekrestok_closed"]), v(d["chizhik_opened"]), "0 (не раскр.)"])
    for q in OC:
        qrows.append([q] + [v(OC[q][f][k]) if v(OC[q][f][k]) is not None else "н/р" for f in FMT for k in ("opened", "closed")])
    R.table(["квартал", "П откр.", "П закр.", "Пер откр.", "Пер закр.", "Ч откр.", "Ч закр."], qrows)

    ss = store_size_ols()
    avg_p = v(NF["area_end_by_format"]["2026H1"]["pyaterochka"]) / v(NF["stores_end_by_format"]["2026H1"]["pyaterochka"])
    avg_e = v(NF["area_end_by_format"]["2026H1"]["perekrestok"]) / v(NF["stores_end_by_format"]["2026H1"]["perekrestok"])
    ch_net = {p: (v(NF["area_end_by_format"][p]["chizhik"]) - v(NF["area_end_by_format"][shift_half(p, -2 if p.endswith("H2") else -1)]["chizhik"]),
                  v(NF["stores_end_by_format"][p]["chizhik"]) - v(NF["stores_end_by_format"][shift_half(p, -2 if p.endswith("H2") else -1)]["chizhik"]))
              for p in ("2024H2", "2025H2", "2026H1")}
    size_ch = sum(a for a, _ in ch_net.values()) / sum(n for _, n in ch_net.values())
    R.p(f"«Пятёрочка», МНК ΔS = a·открыто − b·закрыто по 10 кварталам 2024Q1–2026Q2: открываемый магазин "
        f"{1000 * ss['a']:.0f} м² (СО {1000 * ss['se_a']:.0f}), закрываемый {1000 * ss['b']:.0f} м² (СО {1000 * ss['se_b']:.0f}); "
        f"средний магазин 30.06.2026 — {1000 * avg_p:.0f} м². Закрываемые не меньше среднего → оценка закрытой площади "
        "«закрытые × средний магазин» (факты и лист capex) не завышает закрытия.")
    R.p(f"«Чижик»: чистая площадь на чистый магазин {1000 * size_ch:.0f} м² (2024 г., 2025 г., 1П2026); «Перекрёсток» — средний {1000 * avg_e:.0f} м².")

    R.h("2. 2П2026: три оценки чистого прироста")
    h1 = {"pyaterochka": q_sum("pyaterochka", "opened", ["2026Q1", "2026Q2"]),
          "perekrestok": q_sum("perekrestok", "opened", ["2026Q1", "2026Q2"]),
          "chizhik": q_sum("chizhik", "net", ["2026Q1", "2026Q2"])}  # у «Чижика» раскрыт только чистый прирост
    closed_h1 = {f: q_sum(f, "closed", ["2026Q1", "2026Q2"]) or 0.0 for f in ("pyaterochka", "perekrestok")}
    g25 = {"pyaterochka": (q_sum("pyaterochka", "opened", ["2025Q1", "2025Q2"]), q_sum("pyaterochka", "opened", ["2025Q3", "2025Q4"])),
           "perekrestok": (q_sum("perekrestok", "opened", ["2025Q1", "2025Q2"]), q_sum("perekrestok", "opened", ["2025Q3", "2025Q4"])),
           "chizhik": (q_sum("chizhik", "net", ["2025Q1", "2025Q2"]), q_sum("chizhik", "net", ["2025Q3", "2025Q4"]))}
    size = {"pyaterochka": ss["a"], "perekrestok": avg_e, "chizhik": size_ch}
    closed_area_h2 = closed_h1["pyaterochka"] * avg_p + closed_h1["perekrestok"] * avg_e  # закрытия 2П — как в 1П2026
    A0 = A["2026H1"]
    plan_p = v(PL["pyaterochka_openings_2026_plan"]) + v(PL["pyaterochka_reverse_franchise_2026_plan"])
    plan = {"pyaterochka": max(plan_p - h1["pyaterochka"], 0.0),
            "chizhik": max(v(PL["chizhik_openings_2026_plan"]) - h1["chizhik"], 0.0),
            "perekrestok": g25["perekrestok"][1]}
    seas = {f: h1[f] * g25[f][1] / g25[f][0] for f in FMT}

    def net_area(st):
        return sum(st[f] * size[f] for f in FMT) - closed_area_h2

    est = {"план (минимум по годовому отчёту)": net_area(plan),
           "сезонность 2025 г. (2П/1П)": net_area(seas),
           "темп г/г 1П2026 к 1П2025": (A["2026H1"] - A["2025H2"]) / (A["2025H1"] - A["2024H2"]) * (A["2025H2"] - A["2025H1"])}
    R.table(["способ", "магазины валовые (П/Пер/Ч)", "чистый прирост, тыс. м²", "год. темп, %"],
            [["план (минимум по годовому отчёту)", f"{plan['pyaterochka']:.0f}/{plan['perekrestok']:.0f}/{plan['chizhik']:.0f}",
              round(est["план (минимум по годовому отчёту)"], 1), pc(2 * est["план (минимум по годовому отчёту)"] / A0)],
             ["сезонность 2025 г. (2П/1П)", f"{seas['pyaterochka']:.0f}/{seas['perekrestok']:.0f}/{seas['chizhik']:.0f}",
              round(est["сезонность 2025 г. (2П/1П)"], 1), pc(2 * est["сезонность 2025 г. (2П/1П)"] / A0)],
             ["темп г/г 1П2026 к 1П2025", "—", round(est["темп г/г 1П2026 к 1П2025"], 1), pc(2 * est["темп г/г 1П2026 к 1П2025"] / A0)]])
    total_2026 = h1["pyaterochka"] + h1["perekrestok"] + h1["chizhik"] + sum(plan.values())
    R.p(f"1П2026 валовые открытия: П {h1['pyaterochka']:.0f}, Пер {h1['perekrestok']:.0f}, Ч (чистые) {h1['chizhik']:.0f}; "
        f"по плану 2П — ещё {sum(plan.values()):.0f}, итого 2026 ≈ {total_2026:.0f} (гайденс «более {v(PL['openings_2026_min']):.0f}»). "
        f"Закрытия 2П приняты как в 1П2026: {closed_area_h2:.1f} тыс. м².")
    R.p(f"Книга 2П2026 (год. темп): low {pc(NET_GROWTH['low']['2026H2'])} ≈ план-минимум; mid {pc(NET_GROWTH['mid']['2026H2'])} — между "
        f"планом и темпом г/г; high {pc(NET_GROWTH['high']['2026H2'])} — к сезонности 2025 г. (верх).")

    R.h("3. Стратегия-2028 → чистый прирост площади 2027–2028 (тариф high)")
    p_end26 = v(NF["stores_end_by_format"]["2026H1"]["pyaterochka"]) + seas["pyaterochka"] - closed_h1["pyaterochka"]
    c_end26 = v(NF["stores_end_by_format"]["2026H1"]["chizhik"]) + seas["chizhik"]
    need_p = (v(PL["target_2028_pyaterochka_stores"]) - p_end26) / 2
    need_c = (v(PL["target_2028_chizhik_stores"]) - c_end26) / 2
    closed_p_year = 2 * closed_h1["pyaterochka"]
    net_area_year = ((need_p + closed_p_year) * ss["a"] - closed_p_year * avg_p) + need_c * size_ch
    a_end26 = A0 + est["сезонность 2025 г. (2П/1П)"]
    g27, g28 = net_area_year / a_end26, net_area_year / (a_end26 + net_area_year)
    R.p(f"Конец 2026 г. при сезонности: П {p_end26:.0f}, Ч {c_end26:.0f} магазинов. До целей 2028 г. нужно в год: П +{need_p:.0f} чистыми "
        f"(валовые {need_p + closed_p_year:.0f} при закрытиях {closed_p_year:.0f}/год), Ч +{need_c:.0f}.")
    R.p(f"Чистый прирост площади ≈ {net_area_year:.0f} тыс. м²/год → {pc(g27)} (2027), {pc(g28)} (2028). "
        f"Книга high: 2027 {pc(NET_GROWTH['high']['2027'])}, 2028 {pc(NET_GROWTH['high']['2028'])}.")
    R.p("Цель «Чижика» (5 000 в 2028 г.) достигается уже при ≈400 открытиях в год — вдвое меньше темпа 2025 г. (907 чистыми): "
        "в плане компании рост «Чижика» замедляется сам.")

    R.h("4. Тарифы книги и площадь к 2036 г.")
    P = halves("2026H2", "2036H2")
    rows, area36 = [], {}
    for t, spec in NET_GROWTH.items():
        a = A0
        for p in P:
            a *= 1 + path_value(spec, p) / 2
        area36[t] = a
        rows.append([t] + [pc(path_value(spec, f"{y}H2"), 1) for y in range(2026, 2037)] + [round(a, 0), pc((a / A0) ** (1 / 10.5) - 1)])
    R.table(["тариф"] + [str(y) for y in range(2026, 2037)] + ["площадь 2036H2", "СГТР"], rows)
    R.p("2031–2035 — линейный сход к LT = 0 к 2036 г. (правило траекторий §0.1); терминал — нулевой чистый рост (§6).")
    R.p(f"Для сравнения: 2025 г. +{pc(A['2025H2'] / A['2024H2'] - 1)} (сопоставимо), 1П2026 +{pc(2 * (A['2026H1'] / A['2025H2'] - 1))} год.; "
        f"low 2027 г. — на {pc(1 - NET_GROWTH['low']['2027'] / (A['2025H2'] / A['2024H2'] - 1), 0)} % ниже темпа 2025 г. "
        f"(Магнит сократил открытия в 1П2026 на {pc(-v(PL['magnit_openings_1h2026_yoy']), 0)} %), mid — на "
        f"{pc(1 - NET_GROWTH['mid']['2027'] / (A['2025H2'] / A['2024H2'] - 1), 0)} %.")

    R.h("5. Веса тарифов в слое «свой взгляд» и ожидаемый путь")
    wp, rp = BR["world_prob"], BR["regime_prob"]
    links = {"N": "high", "H": "mid", "M": "low"}
    stress = "low"
    w = {"low": 0.0, "mid": 0.0, "high": 0.0}
    for W, pw in wp.items():
        for reg, pr in rp.items():
            w[stress if reg == "stress" else links[W]] += pw * pr
    R.p(f"Миры {wp}, режимы (фрагмент «маржа») {rp}; привязка N→high, H→mid, M→low, stress→low во всех мирах.")
    R.p("Вес тарифа:", ", ".join(f"{t} {pc(x, 1)} %" for t, x in w.items()))
    exp = {y: sum(w[t] * path_value(NET_GROWTH[t], f"{y}H2") for t in w) for y in range(2026, 2031)}
    R.p("Ожидаемый чистый рост:", ", ".join(f"{y} {pc(x)} %" for y, x in exp.items()))
    w_mid_stress = {"low": 0.0, "mid": 0.0, "high": 0.0}
    for W, pw in wp.items():
        for reg, pr in rp.items():
            w_mid_stress["mid" if reg == "stress" else links[W]] += pw * pr
    exp2 = {y: sum(w_mid_stress[t] * path_value(NET_GROWTH[t], f"{y}H2") for t in w) for y in range(2026, 2031)}
    R.p("Для сравнения, при stress_growth = mid:", ", ".join(f"{y} {pc(x)} %" for y, x in exp2.items()))

    R.h("6. Доля закрываемой площади (годовая)")
    cr = {p: 2 * C[p] / A[shift_half(p, -1)] for p in hist}
    R.p(", ".join(f"{p} {pc(x)} %" for p, x in cr.items()))
    avg_24_26 = 2 * sum(C[p] for p in hist if p >= "2024H1") / sum(A[shift_half(p, -1)] for p in hist if p >= "2024H1")
    R.p(f"Среднее 2024H1–2026H1 (взвешенно по площади): {pc(avg_24_26)} %; 2023 г.: {pc((C['2023H1'] + C['2023H2']) / A['2022H2'])} %.")
    R.p(f"Книга: {CLOSE_RATE} — 2П2026 на уровне 2П2025–1П2026 (оптимизация сети), затем к долгосрочным 2 % "
        f"(в среднем 2024–2026 гг. {pc(avg_24_26)} %; у Магнита 1,6–2,0 %).")

    R.data = {"net_growth": NET_GROWTH, "close_rate": CLOSE_RATE,
              "history": {p: {"A": r6(A[p]), "O": r6(O.get(p, 0)), "C": r6(C.get(p, 0))} for p in A},
              "store_size": {"p_opened": r6(ss["a"]), "p_closed": r6(ss["b"]), "se_opened": r6(ss["se_a"]),
                             "se_closed": r6(ss["se_b"]), "p_avg": r6(avg_p), "ch_net": r6(size_ch), "per_avg": r6(avg_e)},
              "h2_2026": {k: r6(2 * x / A0) for k, x in est.items()},
              "high_2027_2028": {"2027": r6(g27), "2028": r6(g28), "need_p": r6(need_p), "need_c": r6(need_c)},
              "area_2036": {t: r6(x) for t, x in area36.items()},
              "tariff_weights": {t: r6(x) for t, x in w.items()}, "expected": {str(y): r6(x) for y, x in exp.items()},
              "expected_if_stress_mid": {str(y): r6(x) for y, x in exp2.items()},
              "close_rate_hist": {p: r6(x) for p, x in cr.items()}, "close_rate_avg_2024_2026": r6(avg_24_26)}
    R.save("network")


if __name__ == "__main__":
    main()
