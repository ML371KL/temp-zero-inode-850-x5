"""A-R5 (созревание и плотность новой площади d) и A-R6 (продуктивность закрываемой площади κ).

Что считает:
  1. Вклад не-LFL в рост ЧРВ по полугодиям 2024H1–2026H1: NL = (1 + рост ЧРВ)/(1 + LFL) − 1 (LFL с НДС,
     2026H1 — за вычетом клина НДС, vat.py) против роста средней физической площади; отношение ≈ 0,81
     у X1 (годы 2020–2025) — здесь на полугодиях и сопоставимой площади.
  2. Подбор d по правилу §4.1 (все когорты созревают до d, закрытия — с κ): часть роста эффективной
     площади, которую отчётность относит к «не-LFL» (когорты моложе 12 полных месяцев и закрытия),
     приравнивается к NL. Созревание когорт старше 12 месяцев X5 показывает внутри LFL (магазин входит в
     LFL со всей выручкой с даты открытия) — это «m», вклад созревания в отчётный LFL.
     Варианты кривой созревания и κ — чувствительность; книга — μ = [0,75; 0,90; 1], κ = 0,6.
  3. Смесь форматов открытий: продуктивность «Чижика» 522, «Пятёрочки» 380, «Перекрёстка» 484 тыс. ₽/м² при
     среднем 398 (2025) — плотность новой площади при средней продуктивности формата и отсюда — отношение
     «новый магазин / средний магазин своего формата».
  4. Прогноз по тарифу mid книги: NL, m, и ошибка правила ядра (исторические когорты с d = 1) против
     согласованного расчёта на 2П2026–2036 (цена ошибки — valuation_effect.py, разд. 3).
  5. Терминал: подъём от ротации (d − κ)·cl и его доля «вне LFL» и «в LFL».

Выход: density_out.txt / density_out.json.
"""
from __future__ import annotations

import json

from common import (HERE, Report, eff_consistent, eff_model_rule, forward_network, half_panel, halves,
                    load, network_history, pc, r6, shift_half, v)
from vat import wedge_2026

MU_BOOK = [0.75, 0.90, 1.0]
KAPPA_BOOK = 0.6
CALIB = ["2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]
VARIANTS = [[1.0], [0.85, 1.0], [0.80, 0.93, 1.0], [0.75, 0.90, 1.0], [0.72, 0.82, 1.0]]


def nl_targets() -> dict:
    hp = half_panel()
    wedge = wedge_2026()
    out = {}
    for p in CALIB:
        h = hp[p]
        lfl_ex_vat = (1 + h["lfl"]) / (1 + wedge) - 1 if p >= "2026" else h["lfl"]
        out[p] = {"g_nrs": h["g_nrs"], "lfl": h["lfl"], "lfl_ex_vat": lfl_ex_vat,
                  "nl": (1 + h["g_nrs"]) / (1 + lfl_ex_vat) - 1}
    return out


def fit(H: dict, T: dict, mu: list[float], kappa: float) -> dict:
    order = [p for p in H["A"] if p >= "2022H2"]
    best = None
    for i in range(500, 1301):
        d = i / 1000
        r = eff_consistent(order, H["A"]["2022H2"], H["O"], H["C"], mu, d, kappa)["yoy"]
        sse = sum((r[p]["nonlfl"] - T[p]["nl"]) ** 2 for p in CALIB)
        if best is None or sse < best[0]:
            best = (sse, d, r)
    sse, d, r = best
    return {"d": d, "rmse": (sse / len(CALIB)) ** 0.5, "yoy": r}


def core_index(H: dict, mu: list[float], q: str) -> float:
    """Индекс ядра на конец q (MODEL §4.1): площадь минус незрелая часть последних n когорт с плотностью 1."""
    return H["A"][q] - sum(H["O"][shift_half(q, -a)] * (1.0 - mu[a]) for a in range(len(mu) - 1))


def fit_anchor_units(H: dict, T: dict, mu: list[float], kappa: float) -> tuple[float, float, float]:
    """Подбор той же модели (eff_consistent) с d и κ в единицах ядра — к средней по сети на якоре 2026H1:
    в единицах старой сети это d·r и κ·r, r = согласованный индекс / индекс ядра на якоре (неподвижная точка)."""
    order = [p for p in H["A"] if p >= "2022H2"]
    best = None
    for i in range(500, 1301):
        d, r = i / 1000, 1.0
        for _ in range(100):
            ec = eff_consistent(order, H["A"]["2022H2"], H["O"], H["C"], mu, d * r, kappa * r)
            r_new = ec["eff"]["2026H1"] / core_index(H, mu, "2026H1")
            if abs(r_new - r) < 1e-12:
                break
            r = r_new
        sse = sum((ec["yoy"][p]["nonlfl"] - T[p]["nl"]) ** 2 for p in CALIB)
        if best is None or sse < best[0]:
            best = (sse, d, r)
    return best[1], (best[0] / len(CALIB)) ** 0.5, best[2]


def fit_core_rolling(H: dict, T: dict, mu: list[float], kappa: float) -> tuple[float, float]:
    """Правило ядра, заякоренное на b = p − 2 (история — физическая площадь, когорты ≤ b с плотностью 1,
    закрытие b — физическое), когорты p − 1 и p — с d, закрытия — с κ; часть роста «вне LFL» против NL."""
    from common import mu_avg
    O, C = H["O"], H["C"]

    def nonlfl(p, d):
        b = shift_half(p, -2)
        base = (core_index(H, mu, shift_half(b, -1)) + core_index(H, mu, b)) / 2.0
        new = d * (O[p] * mu_avg(mu, 0) + O[shift_half(p, -1)] * mu_avg(mu, 1)) + 0.5 * O[b] * mu_avg(mu, 2)
        clo = -(C[b] / 2.0 + kappa * C[shift_half(p, -1)] + kappa * C[p] / 2.0)
        return (new + clo) / base
    best = None
    for i in range(500, 1301):
        d = i / 1000
        sse = sum((nonlfl(p, d) - T[p]["nl"]) ** 2 for p in CALIB)
        if best is None or sse < best[0]:
            best = (sse, d)
    return best[1], (best[0] / len(CALIB)) ** 0.5


def jackknife(H: dict, T: dict, mu: list[float], kappa: float) -> float:
    ds = []
    for drop in CALIB:
        keep = [p for p in CALIB if p != drop]
        order = [p for p in H["A"] if p >= "2022H2"]
        best = None
        for i in range(500, 1301, 2):
            d = i / 1000
            r = eff_consistent(order, H["A"]["2022H2"], H["O"], H["C"], mu, d, kappa)["yoy"]
            sse = sum((r[p]["nonlfl"] - T[p]["nl"]) ** 2 for p in keep)
            if best is None or sse < best[0]:
                best = (sse, d)
        ds.append(best[1])
    n = len(ds)
    mean = sum(ds) / n
    return ((n - 1) / n * sum((x - mean) ** 2 for x in ds)) ** 0.5


def main() -> None:
    R = Report()
    H = network_history()
    T = nl_targets()
    A = H["A"]

    R.h("1. Вклад не-LFL в рост ЧРВ против роста средней площади (полугодия г/г)")
    rows = []
    for p in CALIB:
        avg = (A[shift_half(p, -1)] + A[p]) / 2
        avg0 = (A[shift_half(p, -3)] + A[shift_half(p, -2)]) / 2
        g_area = avg / avg0 - 1
        T[p]["g_area"] = g_area
        rows.append([p, pc(T[p]["g_nrs"]), pc(T[p]["lfl"]), pc(T[p]["lfl_ex_vat"]), pc(T[p]["nl"]), pc(g_area),
                     f"{T[p]['nl'] / g_area:.2f}"])
    R.table(["полугодие", "рост ЧРВ", "LFL (с НДС)", "LFL без клина НДС", "не-LFL (NL)", "рост средней площади", "отношение"], rows)
    ratio_mean = sum(T[p]["nl"] for p in CALIB) / sum(T[p]["g_area"] for p in CALIB)
    R.p(f"Отношение суммарно по 5 полугодиям: {ratio_mean:.2f} (X1 §4.5 по годам 2020–2025: медиана 0,81, среднее 0,84; "
        "годы грубее: средняя площадь 2025 г. занижена исключением дарксторов). Клин НДС 2026 г. — "
        f"{pc(wedge_2026())} % (vat.py).")

    R.h("2. Подбор d по правилу §4.1: вариант кривой созревания × κ")
    fits = {}
    rows = []
    for mu in VARIANTS:
        for kappa in (0.4, 0.6, 0.8):
            f = fit(H, T, mu, kappa)
            fits[(tuple(mu), kappa)] = f
            m = [f["yoy"][p]["m"] for p in CALIB]
            rows.append([str(mu), kappa, f"{f['d']:.3f}", f"{100 * f['rmse']:.2f}", f"{100 * min(m):.2f}…{100 * max(m):.2f}",
                         f"{f['d'] - kappa:+.2f}"])
    R.table(["кривая μ", "κ", "d*", "RMSE, п.п.", "m (созревание в LFL), п.п.", "d − κ"], rows)
    R.p("Качество подгонки от формы кривой почти не зависит (RMSE 0,6–0,7 п.п.): отчётность различает только "
        "продуктивность магазина в первый год (≈ d × средняя μ первого года ≈ 0,70), а не путь к зрелости. "
        "Круче кривая — выше зрелая d и больше доля созревания, уходящая в отчётный LFL (m).")

    fb = fits[(tuple(MU_BOOK), KAPPA_BOOK)]
    d_book = round(fb["d"], 2)
    se = jackknife(H, T, MU_BOOK, KAPPA_BOOK)
    R.h(f"3. Книга: μ = {MU_BOOK}, κ = {KAPPA_BOOK}: d* = {fb['d']:.3f} → {d_book}")
    R.table(["полугодие", "NL факт", "NL модели", "m (в отчётном LFL)", "рост эфф. площади (NL + m)"],
            [[p, pc(T[p]["nl"]), pc(fb["yoy"][p]["nonlfl"]), pc(fb["yoy"][p]["m"]), pc(fb["yoy"][p]["g"])] for p in CALIB])
    R.p(f"Ошибка складного ножа (без одного полугодия) для d: {se:.3f}; с неопределённостью формы кривой и κ (±0,04…0,05, "
        f"таблица 2) — ось полосы 0,74–0,94.")

    R.h("4. Смесь форматов открытий и зрелая плотность")
    prod = load("operating_q.json")["productivity"]
    pr = {f: v(prod["annual"]["2025"][f]) for f in ("pyaterochka", "perekrestok", "chizhik", "total")}
    ltm = {f: v(prod["ltm_2026-06-30"][f]) for f in ("pyaterochka", "perekrestok", "chizhik", "total")}
    nf = load("network_facts.json")
    oc = nf["openings_closures_stores"]
    netw = json.loads((HERE / "network_out.json").read_text(encoding="utf-8"))
    sz = netw["store_size"]
    gross = {"pyaterochka": 0.0, "perekrestok": 0.0, "chizhik": 0.0}
    for q in oc:
        if q < "2024Q1":
            continue
        gp, ge = v(oc[q]["pyaterochka"]["opened"]), v(oc[q]["perekrestok"]["opened"])
        gc = v(oc[q]["chizhik"]["opened"]) if v(oc[q]["chizhik"]["opened"]) is not None else v(oc[q]["chizhik"]["net"])
        gross["pyaterochka"] += gp * sz["p_opened"]
        gross["perekrestok"] += ge * sz["per_avg"]
        gross["chizhik"] += gc * sz["ch_net"]
    tot = sum(gross.values())
    share = {f: x / tot for f, x in gross.items()}
    mix = sum(share[f] * pr[f] / pr["total"] for f in share)
    mix_ltm = sum(share[f] * ltm[f] / ltm["total"] for f in share)
    R.p(f"Продуктивность 2025 г., тыс. ₽/м²: «Пятёрочка» {pr['pyaterochka']:.0f}, «Перекрёсток» {pr['perekrestok']:.0f}, "
        f"«Чижик» {pr['chizhik']:.0f}, X5 {pr['total']:.0f}; LTM 2К2026: {ltm['pyaterochka']:.0f} / {ltm['perekrestok']:.0f} / "
        f"{ltm['chizhik']:.0f} / {ltm['total']:.0f}.")
    R.p("Валовые открытия площади 2024Q1–2026Q2 по форматам (магазины × размер, network.py): " +
        ", ".join(f"{f} {pc(s, 0)} %" for f, s in share.items()) + ".")
    R.p(f"Площадь открытий со средней продуктивностью своего формата дала бы {mix:.3f} средней по X5 (LTM: {mix_ltm:.3f}); "
        f"книга d = {d_book} → новый магазин выходит на ≈{d_book / mix:.2f} средней продуктивности своего формата "
        "(новые точки — в менее плотных локациях и регионах экспансии; каннибализация соседних магазинов).")
    plan_share = {"pyaterochka": 1409 * sz["p_opened"] + 0, "chizhik": 402 * sz["ch_net"], "perekrestok": 20 * sz["per_avg"]}
    ps = sum(plan_share.values())
    mix_plan = sum(plan_share[f] / ps * pr[f] / pr["total"] for f in plan_share)
    R.p(f"Смесь по стратегии-2028 (П +1 409, Ч +402, Пер ≈+20 в год): {mix_plan:.3f} — смесь меняется мало, d держится.")

    R.h("5. Прогноз по тарифу mid: NL, m и ошибка правила ядра (история с d = 1)")
    P = halves("2026H2", "2036H2")
    A_f, O_f, C_f = forward_network(A["2026H1"], P, netw["net_growth"]["mid"], netw["close_rate"])
    O_all, C_all = {**H["O"], **O_f}, {**H["C"], **C_f}
    order = [p for p in H["A"] if p >= "2022H2"] + P
    cons = eff_consistent(order, A["2022H2"], O_all, C_all, MU_BOOK, d_book, KAPPA_BOOK)["yoy"]
    rule = eff_model_rule("2026H1", P, H["A"], H["O"], O_f, C_f, MU_BOOK, d_book, KAPPA_BOOK)["yoy"]
    rows = []
    for p in P:
        rows.append([p, pc(cons[p]["nonlfl"]), pc(cons[p]["m"]), pc(cons[p]["g"]), pc(rule[p]), f"{100 * (rule[p] - cons[p]['g']):+.2f}"])
    R.table(["полугодие", "NL (вне LFL)", "m (в LFL)", "эфф. площадь, согласованно", "эфф. площадь, правило ядра", "разница, п.п."], rows)
    ec = eff_consistent([p for p in H["A"] if p >= "2022H2"], A["2022H2"], H["O"], H["C"], MU_BOOK, d_book, KAPPA_BOOK)["eff"]
    core_idx = core_index(H, MU_BOOK, "2026H1")
    ratio = ec["2026H1"] / core_idx
    R.p("Правило ядра (MODEL §4.1) строит индекс на якоре по физической площади с плотностью 1 для исторических когорт, "
        f"а d подобрана на индексе, где все когорты с 2022H1 созревают до d, закрытия — с κ; на 30.06.2026 согласованный "
        f"индекс ниже индекса ядра на {100 * (1 - ratio):.1f} % ({core_idx - ec['2026H1']:.0f} тыс. м²). Отсюда две ошибки "
        "разного знака: в 2П2026 рост эффективной площади завышен (история 2025H1–2026H1 — на физической площади), с 2028 г. "
        "занижен (новая площадь с d к индексу, который выше согласованного), к 2036H2 разница сходит к нулю медленно. "
        "Вместе правило занижает стоимость (valuation_effect.py, разд. 3). Исправляется только в ядре: стартовый индекс "
        "как в подборе (когорты 2022H1–2026H1 с плотностью d, закрытия с κ); подбор d под правило ядра не помогает — "
        "см. разд. 7.")
    variants_art = {}
    for mu in VARIANTS:
        dd = round(fits[(tuple(mu), KAPPA_BOOK)]["d"], 2)
        c2 = eff_consistent(order, A["2022H2"], O_all, C_all, mu, dd, KAPPA_BOOK)["yoy"]
        r2 = eff_model_rule("2026H1", P, H["A"], H["O"], O_f, C_f, mu, dd, KAPPA_BOOK)["yoy"]
        variants_art[str(mu)] = {"d": dd, "art_2026H2": r6(r2["2026H2"] - c2["2026H2"]["g"]),
                                 "m_2026H2": r6(c2["2026H2"]["m"]), "nl_2026H2": r6(c2["2026H2"]["nonlfl"])}
    R.p("По вариантам кривой (d подобрана при κ = 0,6): " + "; ".join(
        f"{k}: d {x['d']}, ошибка правила 2П2026 {100 * x['art_2026H2']:+.2f} п.п., m {100 * x['m_2026H2']:.2f}" for k, x in variants_art.items()))

    R.h("6. Терминал: подъём от ротации при нулевом чистом росте")
    cl = netw["close_rate"]["LT"]
    up = (d_book - KAPPA_BOOK) * cl
    # стационар: каждая полугодовая когорта = cl/2 площади; разложение как в eff_consistent
    from common import mu_avg
    newp = d_book * (mu_avg(MU_BOOK, 0) + mu_avg(MU_BOOK, 1) + 0.5 * mu_avg(MU_BOOK, 2)) * cl / 2 - KAPPA_BOOK * cl
    mlt = d_book * ((0.5 * mu_avg(MU_BOOK, 2) - mu_avg(MU_BOOK, 0)) + sum(mu_avg(MU_BOOK, a) - mu_avg(MU_BOOK, a - 2)
                                                                         for a in range(3, len(MU_BOOK) + 3))) * cl / 2
    R.p(f"(d − κ)·cl = ({d_book} − {KAPPA_BOOK}) × {cl} = {pc(up)} % в год; из них вне LFL {pc(newp)} %, в отчётном LFL {pc(mlt)} %.")
    R.p(f"Черновик: (0,95 − 0,6) × 0,02 = 0,70 %. Магнит (книга 1.6): (0,85 − 0,6) × закрытия LT.")

    R.h("7. Подбор d под правило ядра (справка: почему книга его не берёт)")
    d_anchor, rm_anchor, r_anchor = fit_anchor_units(H, T, MU_BOOK, KAPPA_BOOK)
    d_roll, rm_roll = fit_core_rolling(H, T, MU_BOOK, KAPPA_BOOK)
    R.p(f"(а) Та же модель подбора в единицах ядра (средняя продуктивность сети на якоре, κ = {KAPPA_BOOK} к ней же): "
        f"d = {d_anchor:.3f} (RMSE {100 * rm_anchor:.2f} п.п.; в единицах старой сети — {d_anchor * r_anchor:.3f}). "
        "С ней ядро повторяет согласованный рост с 2028 г., но ошибка первого года (история на физической площади) "
        "остаётся и уже ничем не гасится.")
    R.p(f"(б) Правило ядра, заякоренное на каждом историческом полугодии p − 2 (история — физическая площадь с плотностью 1, "
        f"новые когорты — d, закрытия — κ), рост p к p − 2 против NL: d = {d_roll:.3f} (RMSE {100 * rm_roll:.2f} п.п.) — "
        "в d уходят упрощения первого года правила (закрытия и когорты базы с плотностью 1), а в прогнозе d работает все "
        "десять лет.")
    R.p("Ни один подбор не делает правило ядра согласованным с историей: расхождение — в стартовом индексе ядра, а не в d. "
        "Цена трёх вариантов — valuation_effect.py, разд. 3.")

    R.data = {"mu_book": MU_BOOK, "kappa_book": KAPPA_BOOK, "d_fit": r6(fb["d"]), "d_book": d_book, "d_se_jackknife": r6(se),
              "rmse": r6(fb["rmse"]), "ratio_nl_area": r6(ratio_mean), "wedge": r6(wedge_2026()),
              "targets": {p: {k: r6(x) for k, x in T[p].items()} for p in CALIB},
              "calib": {p: {k: r6(x) for k, x in fb["yoy"][p].items()} for p in CALIB},
              "variants": {f"{list(k[0])}|{k[1]}": {"d": r6(f["d"]), "rmse": r6(f["rmse"])} for k, f in fits.items()},
              "variants_artifact": variants_art,
              "mix": {"share_gross_area": {f: r6(x) for f, x in share.items()}, "mix_2025": r6(mix), "mix_ltm": r6(mix_ltm),
                      "mix_plan": r6(mix_plan), "d_to_format_avg": r6(d_book / mix)},
              "forecast_mid": {p: {"nl": r6(cons[p]["nonlfl"]), "m": r6(cons[p]["m"]), "eff_consistent": r6(cons[p]["g"]),
                                   "eff_rule": r6(rule[p])} for p in P},
              "terminal": {"uplift": r6(up), "nonlfl": r6(newp), "m_lt": r6(mlt)},
              "core_rule": {"consistent_to_core_index_2026H1": r6(ratio), "gap_2026H1": r6(core_idx - ec["2026H1"]),
                            "d_fit_anchor_units": d_anchor, "rmse_anchor_units": r6(rm_anchor),
                            "d_fit_core_rolling": d_roll, "rmse_core_rolling": r6(rm_roll)}}
    R.save("density")


if __name__ == "__main__":
    main()
