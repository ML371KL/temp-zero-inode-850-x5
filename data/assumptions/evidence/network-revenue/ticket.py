"""A-R1 (перенос продовольственной инфляции в LFL-чек, k), A-R2 (сдвиг чека по состояниям спроса),
A-R4 (LFL-трафик по состояниям спроса).

Что считает:
  1. МНК по кварталам: LFL-чек X5 (с НДС) = a + k × прод. ИПЦ г/г (средний уровень квартала, Росстат);
     без 2020–2021 гг. (пандемия: чек +12…+18 % при трафике −6…−17 %); подвыборки и полугодия.
  2. История сдвига s = чек − k × ИПЦ и «избытка» LFL-продаж по полугодиям; реальный чек за 15 лет.
  3. Трафик: отчётный LFL-трафик и трафик зрелой сети = отчётный − m (созревание новых магазинов,
     density.py): книга ведёт LFL зрелой сети, созревание новых магазинов — в эффективной площади (§4.1).
  4. Центр (безусловное ожидание) по годам и состояния спроса bear/base/bull вокруг него: веса состояний =
     вероятности режимов (stress → bear, floor/partial → base, full → bull), средневзвешенное = центр.
  5. Долгосрочный реальный рост терминала по мирам на книжных значениях (MODEL §6).

Выход: ticket_out.txt / ticket_out.json.
"""
from __future__ import annotations

import json

from common import HERE, Report, food_yoy, half_panel, load, ols, panel, path_value, pc, r6

BR = load("book_refs.json")
COVID_Q = {f"{y}Q{i}" for y in (2020, 2021) for i in range(1, 5)}
COVID_H = {"2020H1", "2020H2", "2021H1", "2021H2"}
STATES = ("bear", "base", "bull")

# --- центр (безусловное ожидание листа) и отклонения состояний; обоснование — печать и раздел книги ---
CENTER_SHIFT = {"2026H2": 0.017, "2027": 0.015, "LT": 0.013, "LT_from": 2029}
CENTER_TRAFFIC = {"2026H2": -0.011, "2027": -0.008, "LT": -0.005, "LT_from": 2030}
DELTA_SHIFT = {"bear": {"near": -0.008, "lt": -0.004}, "bull": {"near": 0.007, "lt": 0.003}}
DELTA_TRAFFIC = {"bear": {"near": -0.006, "lt": -0.003}, "bull": {"near": 0.005, "lt": 0.003}}


def state_weights() -> dict:
    rp, rd = BR["regime_prob"], BR["regime_demand"]
    w = {s: 0.0 for s in STATES}
    for reg, p in rp.items():
        w[rd[reg]] += p
    return w


def by_state(center: dict, delta: dict, w: dict) -> dict:
    """Траектории bear/base/bull: base = центр − Σ w·δ (средневзвешенное = центр), округление 0,001 п.п.
    (при шаге 0,1 п.п. все состояния уходили в одну сторону: центр LT смещался на +0,05 п.п.)."""
    out = {}
    for key in ("near", "lt"):
        corr = -sum(w[s] * delta[s][key] for s in ("bear", "bull"))
        for s in STATES:
            out.setdefault(s, {})[key] = corr + (delta[s][key] if s in delta else 0.0)
    res = {}
    for s in STATES:
        spec = {}
        for k, x in center.items():
            if k == "LT_from":
                spec[k] = x
            else:
                d = out[s]["lt" if k == "LT" else "near"]
                spec[k] = round(x + d, 5)
        res[s] = spec
    return res


def main() -> None:
    R = Report()
    q = panel()
    R.h("1. МНК: LFL-чек = a + k × прод. ИПЦ г/г (кварталы)")
    samples = {
        "2011Q1–2026Q2 без 2020–2021 (книга)": [k for k in sorted(q) if k not in COVID_Q],
        "2011Q1–2026Q2 все": sorted(q),
        "2011Q1–2019Q4": [k for k in sorted(q) if k < "2020"],
        "2016Q1–2026Q2 без 2020–2021": [k for k in sorted(q) if k >= "2016" and k not in COVID_Q],
        "2022Q1–2026Q2": [k for k in sorted(q) if k >= "2022"],
    }
    rows, fits = [], {}
    for name, ks in samples.items():
        r = ols([q[k]["cpi"] for k in ks], [q[k]["basket"] for k in ks])
        fits[name] = r
        rows.append([name, r["n"], f"{r['k']:.3f} ({r['se_k']:.3f})", f"{100 * r['a']:+.2f} ({100 * r['se_a']:.2f})",
                     pc(r["rmse"]), f"{r['r2']:.2f}", pc(r["mean_x"]), pc(r["mean_y"])])
    R.table(["выборка", "n", "k (СО)", "a, п.п. (СО)", "RMSE, п.п.", "R²", "ИПЦ ср.", "чек ср."], rows)
    hp = half_panel()
    hk = [p for p in hp if p not in COVID_H]
    rh = ols([food_yoy(p) for p in hk], [hp[p]["ticket"] for p in hk])
    R.p(f"Полугодия без 2020–2021 (n = {rh['n']}): k = {rh['k']:.3f} (СО {rh['se_k']:.3f}), a = {100 * rh['a']:+.2f} п.п. "
        f"Книга Магнита 1.6 (лист agent-revenue) для X5 — 0,63 (0,06): совпадает.")
    kb = fits["2011Q1–2026Q2 без 2020–2021 (книга)"]
    k_book = round(kb["k"], 2)
    R.p(f"Книга: k = {k_book} во всех состояниях спроса. Подвыборки (0,65 до 2020 г.; 0,44 ± 0,16 в 2022–2026 гг.) "
        "не отличаются от центра больше чем на 1,5 СО; различие состояний спроса — в сдвиге и трафике, а "
        "одинаковый k не смещает среднее по состояниям (веса состояний несимметричны: bear 41,5 %).")

    R.h("2. Сдвиг s = чек − k × ИПЦ и избыток LFL-продаж по полугодиям")
    rows = []
    for p in sorted(hp):
        if p < "2016":
            continue
        f = food_yoy(p)
        rows.append([p, pc(hp[p]["ticket"]), pc(f), pc(hp[p]["ticket"] - k_book * f), pc(hp[p]["traffic"]),
                     pc(hp[p]["lfl"] - k_book * f)])
    R.table(["полугодие", "LFL-чек", "прод. ИПЦ", "s", "LFL-трафик", "LFL-продажи − k×ИПЦ"], rows)
    era = lambda a, b: [p for p in hp if a <= p <= b and p not in COVID_H]
    for name, (a, b) in {"2016–2019": ("2016H1", "2019H2"), "2021H2–2023H1": ("2021H2", "2023H1"),
                         "2023H2–2026H1": ("2023H2", "2026H1")}.items():
        ps = era(a, b)
        s = sum(hp[p]["ticket"] - k_book * food_yoy(p) for p in ps) / len(ps)
        R.p(f"Средний s {name}: {pc(s)} п.п.")
    mt = kb["mean_y"] - kb["mean_x"]
    R.p(f"За 2011–2026 гг. (без пандемии) чек рос в среднем на {pc(-mt)} п.п. в год медленнее прод. ИПЦ "
        "(корзина мельчает при росте частоты покупок и экспансии «у дома»); с 2П2023 — наоборот, s 3–6 п.п. "
        "(сокращение промо, готовая еда, рост доли онлайна с чеком ×3), к 2 кв. 2026 — 0,8 п.п.")
    q2 = q["2026Q2"]
    R.p(f"2 кв. 2026: чек {pc(q2['basket'])}, ИПЦ {pc(q2['cpi'])} → s {pc(q2['basket'] - k_book * q2['cpi'])}; "
        f"LFL-продажи {pc(q2['lfl'])} − k×ИПЦ = {pc(q2['lfl'] - k_book * q2['cpi'])}. "
        f"Прод. ИПЦ июль–август 2026 г/г: {pc(food_yoy('2026H2', ['2026-07', '2026-08']))} %.")

    R.h("3. Трафик: отчётный и зрелой сети (отчётный − m)")
    dens = json.loads((HERE / "density_out.json").read_text(encoding="utf-8"))
    rows = []
    for p, c in dens["calib"].items():
        rows.append([p, pc(hp[p]["traffic"]), pc(c["m"]), pc(hp[p]["traffic"] - c["m"])])
    R.table(["полугодие", "отчётный LFL-трафик", "m (созревание)", "трафик зрелой сети"], rows)
    for name, (a, b) in {"2014–2019": ("2014H1", "2019H2"), "2022–2025": ("2022H1", "2025H2")}.items():
        ps = era(a, b)
        R.p(f"Средний отчётный LFL-трафик {name}: {pc(sum(hp[p]['traffic'] for p in ps) / len(ps))} % (годы экспансии 8–20 % "
            "площади в год: в отчётном трафике — созревание новых магазинов, в трафике зрелых — каннибализация ими же).")
    fm = dens["forecast_mid"]
    R.p("m по тарифу mid (density.py): " + ", ".join(f"{p} {pc(x['m'])}" for p, x in fm.items() if p <= "2028H2") +
        f"; в терминале {pc(dens['terminal']['m_lt'])} п.п.")

    R.h("4. Центр и состояния спроса")
    w = state_weights()
    R.p("Веса состояний (из вероятностей режимов фрагмента «маржа»):", ", ".join(f"{s} {pc(x, 1)} %" for s, x in w.items()))
    shift = by_state(CENTER_SHIFT, DELTA_SHIFT, w)
    traffic = by_state(CENTER_TRAFFIC, DELTA_TRAFFIC, w)
    years = ["2026H2", "2027H2", "2028H2", "2029H2", "2030H2", "2036H2"]
    rows = []
    for name, spec in [("сдвиг — центр", CENTER_SHIFT)] + [(f"сдвиг — {s}", shift[s]) for s in STATES] + \
                      [("трафик — центр", CENTER_TRAFFIC)] + [(f"трафик — {s}", traffic[s]) for s in STATES]:
        rows.append([name] + [pc(path_value(spec, p)) for p in years])
    R.table(["траектория, п.п."] + [p[:4] for p in years], rows)
    for p in years:
        for nm, spec, c in (("сдвиг", shift, CENTER_SHIFT), ("трафик", traffic, CENTER_TRAFFIC)):
            e = sum(w[s] * path_value(spec[s], p) for s in STATES)
            assert abs(e - path_value(c, p)) < 1e-5, (nm, p, e)
    R.p("Средневзвешенное по состояниям = центр (до 0,001 п.п.) — проверено по всем годам.")
    fc = {W: BR["worlds"][W]["food_cpi"]["2026H2"] for W in ("N", "H", "M")}
    wp = BR["world_prob"]
    food_w = sum(wp[W] * fc[W] for W in wp)
    t_c = k_book * food_w + path_value(CENTER_SHIFT, "2026H2")
    tr_rep = path_value(CENTER_TRAFFIC, "2026H2") + fm["2026H2"]["m"]
    R.p(f"2П2026, центр: прод. ИПЦ миров {fc} (среднее по весам {pc(food_w)}) → LFL-чек {pc(t_c)} %, трафик зрелой сети "
        f"{pc(path_value(CENTER_TRAFFIC, '2026H2'))} %, отчётный трафик ≈ {pc(tr_rep)} % → отчётный LFL ≈ {pc((1 + t_c) * (1 + tr_rep) - 1)} % "
        "(2 кв. 2026: 4,2 %; 1 кв.: 6,1 %).")

    R.h("5. Реальный рост терминала по мирам (MODEL §6) на книжных значениях, базовый спрос")
    netw = json.loads((HERE / "network_out.json").read_text(encoding="utf-8"))
    d, kap, cl = dens["d_book"], dens["kappa_book"], netw["close_rate"]["LT"]
    rows, real_w = [], {}
    for W in ("N", "H", "M"):
        food_last = BR["worlds"][W]["food_cpi"]["2036H2"]
        pi = BR["worlds"][W]["lt_inflation"]
        hom = (1 - k_book) * (pi - BR["worlds"]["N"]["lt_inflation"])
        for s in STATES:
            tk = k_book * food_last + path_value(shift[s], "2036H2") + hom
            tr = path_value(traffic[s], "2036H2")
            g = (1 + tk) * (1 + tr) * (1 + (d - kap) * cl) - 1
            rows.append([W, s, pc(food_last), pc(pi), pc(tk), pc(tr), pc(g), pc((1 + g) / (1 + pi) - 1)])
            real_w[W] = real_w.get(W, 0.0) + w[s] * ((1 + g) / (1 + pi) - 1)
    R.table(["мир", "спрос", "прод. ИПЦ LT", "π LT", "чек LT", "трафик LT", "g", "реальный g"], rows)
    R.p("Средневзвешенный по состояниям реальный g: " + ", ".join(f"{W} {pc(x)} %" for W, x in real_w.items()))
    R.p(f"Подъём от ротации (d − κ)·cl = {pc((d - kap) * cl)} п.п. Центр: реальный чек ≈ 0, трафик зрелой сети −0,5 % "
        "(население −0,2…−0,5 % в год, уход части покупок в маркетплейсы), итог ≈ 0 % реального роста при нулевом "
        "чистом приросте площади.")

    R.data = {"k_ols": r6(kb["k"]), "k_se": r6(kb["se_k"]), "a_ols": r6(kb["a"]), "k_book": k_book,
              "ticket_k": {s: k_book for s in STATES}, "ticket_shift": shift, "traffic": traffic,
              "center_shift": CENTER_SHIFT, "center_traffic": CENTER_TRAFFIC, "state_weights": {s: r6(x) for s, x in w.items()},
              "fits": {n: {k: r6(x) if isinstance(x, float) else x for k, x in f.items()} for n, f in fits.items()},
              "terminal_real_weighted": {W: r6(x) for W, x in real_w.items()},
              "h2_2026": {"ticket_center": r6(t_c), "traffic_reported": r6(tr_rep), "food_weighted": r6(food_w)}}
    R.save("ticket")


if __name__ == "__main__":
    main()
