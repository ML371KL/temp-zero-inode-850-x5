"""Лист capex X5 (A-K1…A-K7, A-P3): поддерживающий capex снизу вверх, цена открытия на м²,
инфраструктура на м² прироста, срок службы, поступления от выбытия, вероятности уровней.

Метод перенесён из книги Магнита (A-K1 снизу вверх, физическая доля за площадью × ИПЦ,
IT и автопарк — доля выручки), числа — X5. Читает только inputs/; печатает capex_out.txt,
пишет capex_out.json. Суждения (класс C) собраны в словаре J ниже — всё остальное считается.

    python -B capex.py
"""
from __future__ import annotations

from common import (BOOK, CFI, FACTS, LEVEL, PRIM, Report, an, avg_level, half, hy, net_q,
                    period_months, r6, to_anchor)

R = Report()

# --------------------------------------------------------------- суждения листа (класс C)
J = {
    # цикл полного обновления магазина (лет): низкий / базовый / высокий уровень
    "cycle": {"low": 12.5, "base": 10.0, "high": 8.0},
    # срок замены грузовика (лет)
    "fleet_life": {"low": 10.0, "base": 8.0, "high": 7.0},
    # поддержание РЦ и логистической инфраструктуры (без автопарка), доля выручки
    "dc_upkeep": {"low": 0.0012, "base": 0.0018, "high": 0.0025},
    # повторяющиеся IT и проекты эффективности, доля выручки
    "it": {"low": 0.0045, "base": 0.0055, "high": 0.0065},
    # замена оборудования CVP (пекарни, кофе, фабрики-кухни, онлайн), доля выручки
    "cvp": {"low": 0.0010, "base": 0.0015, "high": 0.0022},
    # удельная стоимость реконструкции: нижняя — минимум 2015 г. (косметика 5 + оборудование
    # 5 тыс. ₽/м²; realnoevremya.ru/articles/3590, 06.04.2015, вторично) в ценах якоря; верхняя — отношение «редизайн / новый магазин» Магнита
    # (38 / 48 тыс. ₽/м²) × цена открытия X5; центр — середина
    "refurb_low_2015_rub_m2": 10_000.0,
    "magnit_refurb_to_new": 38.0 / 48.0,
    # «Чижик»: реконструкция дискаунтера — половина стоимости м² «Пятёрочки»;
    # рестайлинг «Перекрёстка» — 40 % полной реконструкции
    "chizhik_refurb_factor": 0.5,
    "restyle_factor": 0.4,
    # доля расходов на РЦ 2024–2025 (без автопарка), идущая на мощности под прирост площади
    "dc_growth_share": {"low": 1.0 / 3.0, "base": 2.0 / 3.0, "high": 1.0},
    # экономический срок службы длиннее учётного (сверка стоимостью замещения)
    "economic_life_mult": 1.3,
}

TU = PRIM["trading_updates"]
CC = PRIM["capex_company"]


def qsum(series: dict, quarters: list[str]) -> float:
    return float(sum(series.get(q, 0) for q in quarters))


PERIOD_Q = {"2024": ["2024Q1", "2024Q2", "2024Q3", "2024Q4"],
            "2024H1": ["2024Q1", "2024Q2"], "2024H2": ["2024Q3", "2024Q4"],
            "2025": ["2025Q1", "2025Q2", "2025Q3", "2025Q4"],
            "2025H1": ["2025Q1", "2025Q2"], "2025H2": ["2025Q3", "2025Q4"],
            "2026H1": ["2026Q1", "2026Q2"]}
# концы периодов и базис «Пятёрочки»: 2024 — как отчитано (без «Красного Яра» и «Слаты» —
# в том же базисе, что валовые открытия и закрытия бренда); с 2025 — с ними (скорр.)
ENDS = {"2024": ("2023Q4", "2024Q4", "as_reported"), "2024H1": ("2023Q4", "2024Q2", "as_reported"),
        "2024H2": ("2024Q2", "2024Q4", "as_reported"),
        "2025": ("2024Q4", "2025Q4", "restated_KY_Slata"),
        "2025H1": ("2024Q4", "2025Q2", "restated_KY_Slata"),
        "2025H2": ("2025Q2", "2025Q4", "restated_KY_Slata"),
        "2026H1": ("2025Q4", "2026Q2", "as_reported")}
FORMATS = ("pyaterochka", "perekrestok", "chizhik")


def network_flows(p: str) -> dict:
    """Чистый прирост, закрытия и валовое открытие площади по форматам за период."""
    q0, q1, basis = ENDS[p]
    qs = PERIOD_Q[p]
    gross_st = {"pyaterochka": qsum(TU["pyaterochka_opened_gross"], qs),
                "perekrestok": qsum(TU["perekrestok_opened_gross"], qs)}
    out = {"net_area": 0.0, "closed_area": 0.0, "gross_area": 0.0, "by_format": {}}
    for f in FORMATS:
        b = basis if f == "pyaterochka" else "as_reported"
        a0, a1 = net_q(q0, f"space_{f}", b), net_q(q1, f"space_{f}", b)
        s0, s1 = net_q(q0, f"stores_{f}", b), net_q(q1, f"stores_{f}", b)
        net_st = s1 - s0
        # «Чижик»: валовые открытия и закрытия не раскрыты — закрытия приняты нулевыми
        gross = gross_st.get(f, net_st)
        closed_st = max(0.0, gross - net_st)
        closed_area = closed_st * a0 / s0          # средний магазин формата на начало периода
        net_area = a1 - a0
        out["by_format"][f] = {"net_area": net_area, "gross_stores": gross,
                               "net_stores": net_st, "closed_stores": closed_st,
                               "closed_area": closed_area, "gross_area": net_area + closed_area}
        out["net_area"] += net_area
        out["closed_area"] += closed_area
        out["gross_area"] += net_area + closed_area
    return out


# ============================================================ 1. история capex
R.h("1. История: денежный capex (ОС + НМА), capex компании, D&A, млрд ₽")
rows = []
hist = {}
for y in [str(x) for x in range(2018, 2026)]:
    rev, cap, da = an(y, "revenue"), -an(y, "capex_total"), an(y, "da_ias17")
    comp = CC["total"].get(y)
    hist[y] = {"revenue": rev, "capex_cash": cap, "capex_company": comp, "da": da}
    rows.append([y, rev, cap, cap / rev, comp if comp else "н/р", da / rev])
for p in ["2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]:
    rev, cap, da = hy(p, "revenue"), -hy(p, "capex_total"), hy(p, "da_ias17")
    hist[p] = {"revenue": rev, "capex_cash": cap, "da": da}
    rows.append([p, rev, cap, cap / rev, "", da / rev])
R.table(["период", "выручка", "capex", "capex/выр.", "capex компании", "D&A/выр."], rows)
R.data["history"] = {k: {kk: (r6(vv) if isinstance(vv, float) else vv) for kk, vv in d.items()}
                     for k, d in hist.items()}

# 1б. Прочие инвестиционные платежи (ОДДС стр. 42) и поступления по чистым инвестициям в аренду
# (стр. 41) — вне capex компании (CF30 + CF36); строки появились в 4К2023. Решение A-K1: нетто
# (стр. 42 − стр. 41) — поток капитального характера, входит в тождество и в поддерживающий.
CFQ = CFI["cash_flow"]
HALF_Q = {"2024H1": ("2024Q1", "2024Q2"), "2024H2": ("2024Q3", "2024Q4"),
          "2025H1": ("2025Q1", "2025Q2"), "2025H2": ("2025Q3", "2025Q4"),
          "2026H1": ("2026Q1", "2026Q2")}


def cfi(p: str, key: str) -> float:
    """Строка ОДДС за год или полугодие (сумма кварталов); знак ОДДС."""
    if p in HALF_Q:
        return sum(CFQ[q][key]["v"] for q in HALF_Q[p])
    return CFQ[p][key]["v"]


def other_net(p: str) -> float:
    """Прочие инвестиционные платежи нетто поступлений по аренде, млрд ₽ оттока."""
    return -cfi(p, "other_investing_payments") - cfi(p, "finance_lease_principal_receipts")


R.h("1б. Прочие платежи по инвестиционной деятельности (ОДДС стр. 42) и тело чистых инвестиций в аренду "
    "(стр. 41), млрд ₽")
rows = []
for p in ["2023", "2024", "2025", "2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]:
    rev = an(p, "revenue") if len(p) == 4 else hy(p, "revenue")
    rows.append([p, -cfi(p, "other_investing_payments"), cfi(p, "finance_lease_principal_receipts"),
                 other_net(p), 100 * other_net(p) / rev])
R.table(["период", "прочие платежи", "тело аренды", "нетто", "% выручки"], rows)
ONCA = CFI["other_noncurrent_assets"]
R.p("Прочие внеоборотные активы (Financial Position стр. 14):",
    {d: round(x["v"], 3) for d, x in ONCA.items() if d >= "2023-12-31"})
cum_net = sum(other_net(p) for p in ("2024", "2025", "2026H1"))
d_onca = ONCA["2026-06-30"]["v"] - ONCA["2023-12-31"]["v"]
R.p(f"2024–1П2026: прочие платежи нетто {cum_net:.2f} млрд ₽; прочие внеоборотные активы выросли на "
    f"{d_onca:.2f} ({100 * d_onca / cum_net:.0f} % оттока); других поступлений по этим вложениям в ОДДС нет")
R.data["other_investing"] = {p: {"payments": r6(-cfi(p, "other_investing_payments")),
                                 "lease_receipts": r6(cfi(p, "finance_lease_principal_receipts")),
                                 "net": r6(other_net(p))} for p in [r[0] for r in rows]}
R.data["other_investing_onca"] = {"cum_net_2024_1h26": r6(cum_net), "d_other_nca": r6(d_onca)}

# ============================================================ 2. сеть: валовые открытия
R.h("2. Валовое открытие площади (тыс. м²): чистый прирост + закрытия × средний магазин")
flows = {p: network_flows(p) for p in ENDS}
rows = []
for p, f in flows.items():
    bf = f["by_format"]
    rows.append([p, bf["pyaterochka"]["gross_stores"], bf["pyaterochka"]["closed_stores"],
                 bf["perekrestok"]["gross_stores"], bf["chizhik"]["net_stores"],
                 f["net_area"], f["closed_area"], f["gross_area"]])
R.table(["период", "П: откр.", "П: закр.", "Пер: откр.", "Ч: чистые", "чистая пл.",
         "закрыто пл.", "валовая пл."], rows)
closure_rate = {}
for p in ("2024", "2025", "2026H1"):
    q0, q1, _ = ENDS[p]
    a_avg = 0.5 * (net_q(q0, "space_total") + net_q(q1, "space_total"))
    k = 1.0 if len(p) == 4 else 2.0
    closure_rate[p] = flows[p]["closed_area"] / a_avg * k
R.p("Доля закрываемой площади, годовая:", {k: round(v_, 4) for k, v_ in closure_rate.items()},
    "(книга, network.close_rate LT:", BOOK["network"]["close_rate"]["LT"], ")")
R.data["network_flows"] = {p: {k: (r6(v_) if isinstance(v_, float) else v_)
                               for k, v_ in f.items() if k != "by_format"} for p, f in flows.items()}
R.data["closure_rate_annual"] = {k: r6(v_) for k, v_ in closure_rate.items()}

# ============================================================ 3. A-K3 цена открытия на м²
R.h("3. A-K3. Стоимость открытия, тыс. ₽ на м² валового открытия")
openings = {"2024": CC["structure_2024"]["openings"] * CC["total"]["2024"],
            "2025": CC["structure_2025"]["openings"] * CC["total"]["2025"]}
price_obs = {}
rows = []
for y, cap in openings.items():
    nominal = cap / flows[y]["gross_area"]
    real = nominal * to_anchor(y)
    price_obs[y] = {"openings_capex": cap, "gross_area": flows[y]["gross_area"],
                    "nominal": nominal, "anchor_prices": real, "cpi_to_anchor": to_anchor(y)}
    rows.append([y, cap, flows[y]["gross_area"], 1e3 * nominal, to_anchor(y), 1e3 * real])
R.table(["год", "capex открытий", "валовая пл.", "тыс. ₽/м²", "ИПЦ к якорю", "в ценах якоря"],
        rows)
w = sum(o["gross_area"] for o in price_obs.values())
price_center = sum(o["anchor_prices"] * o["gross_area"] for o in price_obs.values()) / w
R.p(f"Центр (взвешенно по площади): {1e3 * price_center:.1f} тыс. ₽/м² → "
    f"price_per_m2 = {price_center:.4f} млрд ₽ / тыс. м²")
# сверка «Чижиком»: весь capex сегмента (открытия + РЦ + поддержание) на м² чистого прироста
SC = PRIM["segment_capex"]
chz = {}
for p in ("2024", "2025", "2025H1", "2026H1"):
    fl = flows.get(p) or network_flows(p)
    na = fl["by_format"]["chizhik"]["net_area"]
    chz[p] = SC[p]["chizhik"] / na * to_anchor(p)
R.p("Сверка: capex сегмента «Чижик» (открытия + РЦ + поддержание) на м² чистого прироста, "
    "тыс. ₽ в ценах якоря:", {k: round(1e3 * v_, 1) for k, v_ in chz.items()})
R.data["price_per_m2"] = {"obs": {y: {k: r6(v_) for k, v_ in o.items()}
                                  for y, o in price_obs.items()},
                          "center": r6(price_center),
                          "chizhik_all_in_per_net_m2": {k: r6(v_) for k, v_ in chz.items()}}

# ============================================================ 4. автопарк
R.h("4. Автопарк: цена грузовика, парк на м², замена")
PPE = PRIM["ppe_2025"]
tr = TU["trucks_owned"]
est = {}
for y, (q0, q1) in {"2024": ("2023Q4", "2024Q4"), "2025": ("2024Q4", "2025Q4")}.items():
    gross_start = PPE["gross_2023_12_31" if y == "2024" else "gross_2024_12_31"]["transport"]
    unit_hist = gross_start / tr[q0]                     # средняя историческая стоимость единицы
    disposed = PPE[f"disposals_gross_{y}"]["transport"] / unit_hist
    added = tr[q1] - tr[q0] + disposed
    est[y] = {"commissioned": PPE[f"commissioned_{y}"]["transport"], "added_units": added,
              "disposed_units": disposed}
comm = est["2024"]["commissioned"] + est["2025"]["commissioned"]
units = est["2024"]["added_units"] + est["2025"]["added_units"]
f24, f25 = to_anchor("2024"), to_anchor("2025")
price_a = (est["2024"]["commissioned"] * f24 + est["2025"]["commissioned"] * f25) / units
# (б) средняя историческая стоимость парка × рост цен за средний возраст парка
age = PPE["accumulated_2025_12_31"]["transport"] / PPE["depreciation_2025"]["transport"]
lvl_dec25 = LEVEL["2025-12"]
yrs = [(LEVEL[f"{y}-12"] / LEVEL[f"{y - 1}-12"]) for y in range(2022, 2026)]
infl = (yrs[0] * yrs[1] * yrs[2] * yrs[3]) ** 0.25 - 1.0
unit_b = PPE["gross_2025_12_31"]["transport"] / tr["2025Q4"] * (1 + infl) ** age
price_b = unit_b * avg_level(period_months("2026H1")) / lvl_dec25
truck_price = 0.5 * (price_a + price_b)                  # млрд ₽ за единицу
trucks_now, area_now = tr["2026Q2"], net_q("2026Q2", "space_total")
trucks_per_m2 = trucks_now / area_now                    # единиц на тыс. м²
fleet_m2 = trucks_per_m2 * truck_price                   # млрд ₽ на тыс. м² площади
R.p(f"(а) ввод транспорта 2024–2025 / (прирост парка + выбывшие): "
    f"{1e3 * price_a:.2f} млн ₽ за единицу в ценах якоря (выбыло ≈ "
    f"{est['2024']['disposed_units']:.0f} и {est['2025']['disposed_units']:.0f} ед.)")
R.p(f"(б) {PPE['gross_2025_12_31']['transport']:.1f} млрд / {tr['2025Q4']} ед. × (1 + "
    f"{100 * infl:.1f} %)^{age:.2f} года: {1e3 * price_b:.2f} млн ₽")
R.p(f"Цена единицы (среднее а и б): {1e3 * truck_price:.2f} млн ₽; парк {trucks_now} ед. на "
    f"{area_now:.1f} тыс. м² = {trucks_per_m2:.3f} ед. на тыс. м² → "
    f"{1e3 * fleet_m2:.2f} тыс. ₽ на м² прироста")
R.data["fleet"] = {"truck_price_a": r6(price_a), "truck_price_b": r6(price_b),
                   "truck_price": r6(truck_price), "trucks_2026Q2": trucks_now,
                   "trucks_per_thousand_m2": r6(trucks_per_m2), "fleet_per_m2": r6(fleet_m2),
                   "avg_age_years": r6(age), "cpi_2022_2025_annual": r6(infl),
                   "estimates": {y: {k: r6(v_) for k, v_ in e.items()} for y, e in est.items()}}

# ============================================================ 5. A-K4 инфраструктура на м²
R.h("5. A-K4. Инфраструктура под чистый прирост площади")
log24 = CC["structure_2024"]["logistics_transport"] * CC["total"]["2024"]
log25 = CC["structure_2025"]["infra_transport"] * CC["total"]["2025"]
dc24 = log24 - est["2024"]["commissioned"]
dc25 = log25 - est["2025"]["commissioned"]
dc_real = dc24 * f24 + dc25 * f25
net24_25 = flows["2024"]["net_area"] + flows["2025"]["net_area"]
dc_m2 = dc_real / net24_25
infra = {k: fleet_m2 + dc_m2 * s for k, s in J["dc_growth_share"].items()}
R.p(f"Логистика и транспорт: 2024 {log24:.1f}, 2025 {log25:.1f} млрд ₽; без ввода транспорта "
    f"(РЦ и прочая инфраструктура) {dc24:.1f} и {dc25:.1f}; в ценах якоря {dc_real:.1f} млрд ₽ "
    f"на {net24_25:.0f} тыс. м² чистого прироста = {1e3 * dc_m2:.1f} тыс. ₽/м²")
R.p("infra_per_m2 = автопарк + доля РЦ под прирост (1/3; 2/3; 1):",
    {k: round(v_, 4) for k, v_ in infra.items()})
infra_center = infra["base"]
R.data["infra_per_m2"] = {"logistics_2024": r6(log24), "logistics_2025": r6(log25),
                          "dc_2024": r6(dc24), "dc_2025": r6(dc25), "dc_real": r6(dc_real),
                          "dc_per_net_m2": r6(dc_m2), "net_area_2024_2025": r6(net24_25),
                          "values": {k: r6(v_) for k, v_ in infra.items()}}

# ============================================================ 6. тождество по истории
R.h("6. Тождество capex = поддерживающий + открытия × цена + прирост × инфраструктура")
resid = {}
rows = []
for p in ["2024", "2024H1", "2024H2", "2025", "2025H1", "2025H2", "2026H1"]:
    cap = hist[p]["capex_cash"] if p in hist else -hy(p, "capex_total")
    rev = hist[p]["revenue"]
    f = to_anchor(p)
    g_cap = flows[p]["gross_area"] * price_center / f
    i_cap = flows[p]["net_area"] * infra_center / f
    m = cap - g_cap - i_cap
    on = other_net(p)
    resid[p] = {"capex": cap, "revenue": rev, "openings": g_cap, "infra": i_cap, "maint_company": m,
                "other_net": on, "maint": m + on, "maint_pct": (m + on) / rev}
    rows.append([p, cap, g_cap, i_cap, m, on, m + on, 100 * (m + on) / rev])
# 2024 в определении компании (без M&A): 166,4 × (1 − 0,03)
cap_c = CC["total"]["2024"] * (1 - CC["structure_2024"]["mna"])
m_c = cap_c - resid["2024"]["openings"] - resid["2024"]["infra"]
rows.append(["2024 (компания, без M&A)", cap_c, resid["2024"]["openings"], resid["2024"]["infra"],
             m_c, other_net("2024"), m_c + other_net("2024"),
             100 * (m_c + other_net("2024")) / hist["2024"]["revenue"]])
R.table(["период", "capex", "открытия", "инфраструктура", "остаток", "прочие нетто", "поддерж.",
         "% выручки"], rows)
ltm_m = resid["2025H2"]["maint"] + resid["2026H1"]["maint"]
ltm_r = resid["2025H2"]["revenue"] + resid["2026H1"]["revenue"]      # выручка якоря LTM
maint_ltm = ltm_m / ltm_r
other_ltm = (resid["2025H2"]["other_net"] + resid["2026H1"]["other_net"]) / ltm_r
seas = {y: (resid[f"{y}H1"]["maint_pct"] / resid[y]["maint_pct"],
            resid[f"{y}H2"]["maint_pct"] / resid[y]["maint_pct"]) for y in ("2024", "2025")}
R.p(f"LTM 2П2025 + 1П2026: поддерживающий {ltm_m:.1f} млрд ₽ = {100 * maint_ltm:.2f} % выручки "
    f"(из них прочие инвестиционные платежи нетто {100 * other_ltm:.2f} %; без них "
    f"{100 * (maint_ltm - other_ltm):.2f} %)")
R.p("Сезонность поддерживающего (доля в выручке полугодия к году, 1П / 2П):",
    {y: (round(a, 3), round(b, 3)) for y, (a, b) in seas.items()})
R.data["identity"] = {p: {k: r6(v_) for k, v_ in d.items()} for p, d in resid.items()}
R.data["identity"]["2024_company_ex_mna"] = {
    "capex": r6(cap_c), "maint_company": r6(m_c), "other_net": r6(other_net("2024")),
    "maint": r6(m_c + other_net("2024")), "maint_pct": r6((m_c + other_net("2024")) / hist["2024"]["revenue"])}
R.data["maint_ltm_pct"] = r6(maint_ltm)
R.data["other_net_ltm_pct"] = r6(other_ltm)
R.data["maint_seasonality"] = {y: {"h1": r6(a), "h2": r6(b)} for y, (a, b) in seas.items()}

# ============================================================ 7. A-K1 снизу вверх
# База — якорь: рубли в ценах якоря (средний ИПЦ 1П2026) при площади и парке на 30.06.2026 делятся на
# выручку якоря LTM (2П2025 + 1П2026) — это база физической части в ядре (MODEL §4.5: φ × mnt × R_ann(якорь) —
# годовые рубли в площади A(якорь) и ценах якоря) и база ближнего участка (остаток тождества LTM, раздел 6).
R.h("7. A-K1. Стационарный поддерживающий capex снизу вверх (цены и площадь якоря, выручка якоря LTM)")
g_1h = hy("2026H1", "revenue") / hy("2025H1", "revenue") - 1.0
rev_2h26 = hy("2025H2", "revenue") * (1.0 + g_1h)
R26 = hy("2026H1", "revenue") + rev_2h26            # выручка 2026E — только для проверки раздела 9.1
RA = ltm_r                                          # выручка якоря LTM — база уровней
close = BOOK["network"]["close_rate"]["LT"]
area = {f: net_q("2026Q2", f"space_{f}") for f in FORMATS}
stores = {f: net_q("2026Q2", f"stores_{f}") for f in FORMATS}
area_eq = area["pyaterochka"] + area["perekrestok"] + J["chizhik_refurb_factor"] * area["chizhik"]
# реконструкции 2025 в м²-эквиваленте (средний магазин формата на 31.12.2025)
avg_p = net_q("2025Q4", "space_pyaterochka") / net_q("2025Q4", "stores_pyaterochka")
avg_per = net_q("2025Q4", "space_perekrestok") / net_q("2025Q4", "stores_perekrestok")
q25 = PERIOD_Q["2025"]
ref25 = (qsum(TU["pyaterochka_refurbished"], q25) * avg_p
         + qsum(TU["perekrestok_refurbished"], q25) * avg_per
         + J["restyle_factor"] * qsum(TU["perekrestok_restyled"], q25) * avg_per)
store25 = CC["structure_2025"]["store_maintenance_reconstruction"] * CC["total"]["2025"]
area24_3f = (net_q("2024Q4", "space_pyaterochka", "restated_KY_Slata")
             + net_q("2024Q4", "space_perekrestok") + net_q("2024Q4", "space_chizhik"))
area25 = 0.5 * (area24_3f + net_q("2025Q4", "space_total"))   # три формата, сопоставимо
store25_now = store25 * f25 * area_now / area25
cpi_2015 = avg_level(period_months("2026H1")) / avg_level(period_months("2015"))
unit = {"low": J["refurb_low_2015_rub_m2"] * cpi_2015 / 1e6,       # млрд ₽ на тыс. м²
        "high": J["magnit_refurb_to_new"] * price_center}
unit["base"] = 0.5 * (unit["low"] + unit["high"])
R.p(f"Выручка якоря LTM = 2П2025 {hy('2025H2', 'revenue'):.1f} + 1П2026 {hy('2026H1', 'revenue'):.1f} "
    f"= {RA:.1f} млрд ₽; закрытия (книга, LT) {close:.3f}; площадь 30.06.2026 {area_now:.1f} тыс. м²")
# прочие инвестиционные платежи нетто (раздел 1б) в стационаре — факт с выхода программы на масштаб
# (2025–1П2026), один для трёх уровней; ближний участок берёт их LTM через тождество (раздел 6)
other_ss = (other_net("2025") + other_net("2026H1")) / (an("2025", "revenue") + hy("2026H1", "revenue"))
R.p(f"Реконструкции 2025: {ref25:.1f} тыс. м²-экв. (П {qsum(TU['pyaterochka_refurbished'], q25):.0f}"
    f" + Пер {qsum(TU['perekrestok_refurbished'], q25):.0f} полных + "
    f"{qsum(TU['perekrestok_restyled'], q25):.0f} рестайлингов); статья «поддержание магазинов и "
    f"реконструкции» 2025 {store25:.1f} млрд ₽ → в ценах якоря на площадь 30.06.2026 "
    f"{store25_now:.1f} млрд ₽")
R.p("Удельная реконструкция, тыс. ₽/м² (низ — минимум 2015 г. × ИПЦ "
    f"{cpi_2015:.3f}; верх — {J['magnit_refurb_to_new']:.4f} × цена открытия):",
    {k: round(1e3 * v_, 1) for k, v_ in unit.items()})
levels = {}
rows = []
for L in ("low", "base", "high"):
    ref_ss = (1.0 / J["cycle"][L] - close) * area_eq
    store = store25_now + (ref_ss - ref25) * unit[L]
    fleet = trucks_now * truck_price / J["fleet_life"][L]
    dc, it, cvp, oth = (x * RA for x in (J["dc_upkeep"][L], J["it"][L], J["cvp"][L], other_ss))
    tot = store + fleet + dc + it + cvp + oth
    phys = store + dc + cvp
    levels[L] = {"refurb_m2eq": ref_ss, "store": store, "fleet": fleet, "dc": dc, "it": it,
                 "cvp": cvp, "other_net": oth, "total": tot, "pct": tot / RA, "phys_share": phys / tot}
    rows.append([L, J["cycle"][L], ref_ss, 100 * store / RA, 100 * fleet / RA, 100 * dc / RA,
                 100 * it / RA, 100 * cvp / RA, 100 * oth / RA, 100 * tot / RA, phys / tot])
R.table(["уровень", "цикл", "реконстр. тыс.м²", "магазины %", "автопарк %", "РЦ %", "IT %",
         "CVP %", "прочие нетто %", "итого %", "физ. доля"], rows)
R.p(f"Прочие инвестиционные платежи нетто в стационаре — 2025–1П2026: {100 * other_ss:.3f} % выручки "
    "(один для трёх уровней); статьи без них (определение capex компании): "
    + ", ".join(f"{L} {100 * (levels[L]['pct'] - other_ss):.2f} %" for L in levels))
R.data["bottom_up"] = {"R_anchor_ltm": r6(RA), "R26": r6(R26), "g_1h26": r6(g_1h), "close_rate": close,
                       "other_net_ss": r6(other_ss),
                       "area_eq": r6(area_eq), "refurb_2025_m2eq": r6(ref25),
                       "store_2025": r6(store25), "store_2025_now": r6(store25_now),
                       "unit_refurb": {k: r6(v_) for k, v_ in unit.items()},
                       "levels": {L: {k: r6(v_) for k, v_ in d.items()} for L, d in levels.items()},
                       "judgments": {k: v_ for k, v_ in J.items()}}

# ============================================================ 8. пути уровней
R.h("8. Пути capex.maintenance (доля выручки): ближний участок = LTM, стационар = снизу вверх")
base_ss = levels["base"]["pct"]
paths = {}
for L in ("low", "base", "high"):
    k = levels[L]["pct"] / base_ss
    near, ss = maint_ltm * k, levels[L]["pct"]
    paths[L] = {"2026H2": round(near, 4), "2027": round(near, 4), "LT": round(ss, 4),
                "LT_from": 2029}
    R.p(L, paths[L])
phi = round(levels["base"]["phys_share"], 2)
R.p(f"Физическая доля φ (базовый стационар): {levels['base']['phys_share']:.4f} → {phi}")
R.data["paths"] = paths
R.data["phi"] = phi

# ============================================================ 9. проверки
R.h("9. Проверки")
# 9.1 capex 2026 года при сети итоговой книги (рост и закрытия 2П2026) и уровнях — в определении
# компании (ОС + НМА, без прочих инвестиционных платежей): с ним сравнимы факт 1П и прогноз компании
A0 = area_now
idx = 1.0 + half(BOOK["cpi_2026H2"]["N"])
close_2h = BOOK["network"]["close_rate"]["2026H2"]
chk26, cap2h_c = {}, {}
for tariff in ("low", "mid", "high"):
    gn = BOOK["network"]["net_growth"][tariff]["2026H2"]
    net2h = A0 * gn / 2.0
    opened = net2h + A0 * close_2h / 2.0
    for L in ("low", "base", "high"):
        cap2h = ((paths[L]["2026H2"] - other_ltm) * rev_2h26 + opened * price_center * idx
                 + net2h * infra_center * idx)
        cap2h_c[f"{tariff}/{L}"] = cap2h
        chk26[f"{tariff}/{L}"] = (-hy("2026H1", "capex_total") + cap2h) / R26
R.p(f"Выручка 2026E = 1П2026 {hy('2026H1', 'revenue'):.1f} + 2П2025 × (1 + {100 * g_1h:.2f} %) = "
    f"{R26:.1f} млрд ₽; сеть 2П2026 — книга (рост {BOOK['network']['net_growth']['mid']['2026H2']}"
    f" в среднем тарифе, закрытия {close_2h})")
R.p("capex 2026 / выручка 2026E (1П факт + 2П модель, определение компании; тариф роста / уровень):",
    {k: round(100 * v_, 2) for k, v_ in chk26.items()})
run_rate = -hy("2026H1", "capex_total") / hy("2026H1", "revenue")
R.p(f"Ориентиры: факт 1П2026 {100 * run_rate:.2f} %; прогноз компании "
    f"{100 * CC['guidance_2026_pct_revenue']['low']:.1f}–{100 * CC['guidance_2026_pct_revenue']['high']:.1f} %"
    f" (за 2П2026 это {CC['guidance_2026_pct_revenue']['low'] * R26 + hy('2026H1', 'capex_total'):.0f}–"
    f"{CC['guidance_2026_pct_revenue']['high'] * R26 + hy('2026H1', 'capex_total'):.0f} млрд ₽)")
rp, cp = BOOK["regime_prob"], BOOK["capex_prob_given_regime"]
p_lvl = {L: sum(rp[r] * cp[r][L] for r in rp) for L in ("low", "base", "high")}   # как в разделе 12
exp26 = sum(p_lvl[L] * chk26[f"mid/{L}"] for L in p_lvl)
cap2h_mid = cap2h_c["mid/base"]
ratio_2h = (cap2h_mid / rev_2h26) / run_rate
R.p(f"Ожидание по уровням (средний тариф): {100 * exp26:.2f} %; 2П2026 модели {cap2h_mid:.1f} млрд ₽ = "
    f"{100 * cap2h_mid / rev_2h26:.2f} % выручки полугодия — в {ratio_2h:.2f} раза выше 1П; с прочими "
    f"инвестиционными платежами нетто — {100 * (cap2h_mid / rev_2h26 + other_ltm):.2f} %")
R.data["checks_2026"] = {"expected_mid": r6(exp26), "h2_pct": r6(cap2h_mid / rev_2h26),
                         "h2_bn": r6(cap2h_mid), "h2_to_h1": r6(ratio_2h),
                         "p_levels": {k: r6(v_) for k, v_ in p_lvl.items()}}
# 9.2 стоимость замещения по классам ОС
cls = ["buildings_land", "machinery_equipment", "refrigeration", "transport", "other"]
yrs7 = [(LEVEL[f"{y}-12"] / LEVEL[f"{y - 1}-12"]) for y in range(2019, 2026)]
pi7 = 1.0
for x in yrs7:
    pi7 *= x
pi7 = pi7 ** (1 / 7) - 1.0
repl = 0.0
rows = []
for c in cls:
    dep = PPE["depreciation_2025"][c]
    a = PPE["accumulated_2025_12_31"][c] / dep
    rc = dep * (1 + pi7) ** a
    repl += rc
    rows.append([c, dep, a, rc])
SW = PRIM["intangibles_2025"]
a_sw = SW["software_accumulated_2025_12_31"] / SW["software_amortization"]["2025"]
rc_sw = SW["software_amortization"]["2025"] * (1 + pi7) ** a_sw
repl += rc_sw
rows.append(["software", SW["software_amortization"]["2025"], a_sw, rc_sw])
R.table(["класс", "D&A 2025", "ср. возраст, лет", "замещение, цены конца 2025"], rows)
to_anc = avg_level(period_months("2026H1")) / lvl_dec25
repl_hi = repl * to_anc / RA
repl_lo = repl_hi / J["economic_life_mult"]
# ОС и ПО: поддерживающий база без прочих инвестиционных платежей (они не ОС и не НМА X5)
model_ss = levels["base"]["pct"] - other_ss + close * A0 * price_center / RA
R.p(f"Стоимость замещения (учётные сроки / экономические ×{J['economic_life_mult']}): "
    f"{100 * repl_hi:.2f} % / {100 * repl_lo:.2f} % выручки якоря LTM; модель в стационаре "
    f"(поддерживающий база без прочих платежей {100 * (levels['base']['pct'] - other_ss):.2f} % + "
    f"замещающие открытия {100 * close * A0 * price_center / RA:.2f} %): {100 * model_ss:.2f} %")
imp25 = PPE["impairment_net_2025"] + PRIM["intangibles_2025"]["impairment"]["2025"]
da25 = (an("2025", "da_ias17") - imp25) / an("2025", "revenue")
R.p(f"D&A 2025 без обесценения ({imp25:.3f}): {100 * da25:.2f} % выручки → стационарный capex / D&A "
    f"= {model_ss / da25:.2f} (стационарная сеть при росте цен: 1,2–1,6)")
R.data["capex_to_da"] = {"da_2025_ex_impairment_pct": r6(da25), "ratio": r6(model_ss / da25)}
R.data["checks"] = {"capex_2026": {k: r6(v_) for k, v_ in chk26.items()},
                    "run_rate_1h26": r6(run_rate),
                    "replacement_corridor": {"accounting": r6(repl_hi), "economic": r6(repl_lo),
                                             "pi": r6(pi7)},
                    "model_stationary_total": r6(model_ss)}

# ============================================================ 10. A-K6 срок службы
R.h("10. A-K6. Срок службы: подбор L по D&A 1-х полугодий (амортизация когорт 1/(2L))")
halves = []
for y in range(2011, 2018):
    d = FACTS["old_databook_2011_2017"]["years"][str(y)]
    c = -(d["capex_ppe"]["v"] + d["capex_intangibles"]["v"])
    halves += [(f"{y}H1", c / 2), (f"{y}H2", c / 2)]
for y in range(2018, 2027):
    for h in ("H1", "H2"):
        p = f"{y}{h}"
        if p in FACTS["halfyear"]:
            halves.append((p, -hy(p, "capex_total")))
names = [p for p, _ in halves]
caps = [c for _, c in halves]
targets = [f"{y}H1" for y in range(2021, 2027)]
recent = targets[2:]                           # 1П2023–1П2026: периметр ПАО, новый databook
imp = PRIM["impairment_net_1h"]                # D&A без обесценения, где оно раскрыто
fit = {}
for L2 in range(10, 25):                       # 2L полугодий: L = 5 … 12
    sse, pts = 0.0, {}
    for t in targets:
        i = names.index(t)
        if i - L2 < 0:
            sse = None
            break
        da_m = sum(caps[i - j] for j in range(1, L2 + 1)) / L2
        da_f = hy(t, "da_ias17") - imp.get(t, 0.0)
        pts[t] = (da_m, da_f)
        sse += (da_m / da_f - 1.0) ** 2
    if sse is not None:
        bias = sum(pts[t][0] / pts[t][1] for t in recent) / len(recent)
        fit[L2 / 2] = (sse, pts, bias)
best = min(fit, key=lambda k: fit[k][0])
unbiased = min(fit, key=lambda k: abs(fit[k][2] - 1.0))
R.table(["L, лет", "ср. кв. ошибка 2021–26", "модель/факт 2023H1", "2024H1", "2025H1", "2026H1",
         "среднее 2023–26"],
        [[L, (s / len(targets)) ** 0.5] + [pts[t][0] / pts[t][1] for t in recent] + [b]
         for L, (s, pts, b) in sorted(fit.items())])
R.p(f"Минимум ошибки на 1П2021–1П2026: L = {best}; несмещённо на 1П2023–1П2026 "
    f"(среднее модель/факт ≈ 1): L = {unbiased} лет")
# эффективный срок по классам (ввод 2025 × норма D&A класса 2025) и по политике
rate = {c: PPE["depreciation_2025"][c] / (0.5 * (PPE["gross_2024_12_31"][c] + PPE["gross_2025_12_31"][c]))
        for c in cls}
sw_rate = SW["software_amortization"]["2025"] / (0.5 * (SW["software_gross"]["2024-12-31"]
                                                          + SW["software_gross"]["2025-12-31"]))
add = dict(PPE["commissioned_2025"])
add_sw = SW["software_additions"]["2025"]
eff_class = (sum(add.values()) + add_sw) / (sum(add[c] * rate[c] for c in cls) + add_sw * sw_rate)
R.p(f"Эффективный срок по классам (ввод 2025, нормы D&A 2025): {eff_class:.2f} лет "
    "(завышен: в первоначальной стоимости — земля и полностью самортизированные объекты)")
R.data["asset_life"] = {"fit_best": best, "fit_unbiased_2023_2026": unbiased,
                        "fit_bias": {str(k): r6(v_[2]) for k, v_ in fit.items()},
                        "fit_rmse": {str(k): r6((v_[0] / len(targets)) ** 0.5) for k, v_ in fit.items()},
                        "class_effective": r6(eff_class)}

# ============================================================ 11. A-K7 поступления от выбытия
R.h("11. A-K7. Поступления от продажи ОС, % выручки")
disp = {}
for y in range(2011, 2018):
    d = FACTS["old_databook_2011_2017"]["years"][str(y)]
    disp[str(y)] = (d["ppe_disposal_proceeds"]["v"], d["revenue"]["v"])
for y in range(2018, 2026):
    disp[str(y)] = (an(str(y), "ppe_disposal_proceeds"), an(str(y), "revenue"))
disp["2026H1"] = (hy("2026H1", "ppe_disposal_proceeds"), hy("2026H1", "revenue"))
R.table(["период", "поступления", "выручка", "%"],
        [[k, a, b, 100 * a / b] for k, (a, b) in disp.items()])


def wavg(keys):
    return sum(disp[k][0] for k in keys) / sum(disp[k][1] for k in keys)


windows = {"2011–2025": [str(y) for y in range(2011, 2026)],
           "2018–2025": [str(y) for y in range(2018, 2026)],
           "2021–1П2026": [str(y) for y in range(2021, 2026)] + ["2026H1"],
           "2024–1П2026": ["2024", "2025", "2026H1"]}
R.p("Взвешенно по выручке:", {k: round(100 * wavg(v_), 3) for k, v_ in windows.items()})
# Прибыль от выбытия (ОДДС стр. 9, до МСФО 16: неденежная корректировка, «−» — прибыль) уже в EBITDA
# до МСФО 16; денежный вклад сверх EBITDA — остаточная стоимость = поступления − прибыль.
gain = {p: -cfi(p, "ppe_disposal_gain_noncash") for p in ("2021", "2022", "2023", "2024", "2025", "2026H1")}
proc = {p: cfi(p, "ppe_disposal_proceeds") for p in gain}
R.table(["период", "поступления (стр. 35)", "прибыль от выбытия (стр. 9)", "остаточная стоимость",
         "прибыль / поступления"],
        [[p, proc[p], gain[p], proc[p] - gain[p], gain[p] / proc[p]] for p in gain])
w24 = ("2024", "2025", "2026H1")
gain_share = sum(gain[p] for p in w24) / sum(proc[p] for p in w24)
nbv_pct = sum(proc[p] - gain[p] for p in w24) / sum(disp[p][1] for p in w24)
book_gross = 0.0006          # решение книги 1.0 о поступлениях (0,057 % 2024–1П2026 вверх до 0,06 %)
disposal_key = round(book_gross * (1.0 - gain_share), 5)
R.p(f"2024–1П2026: прибыль — {100 * gain_share:.1f} % поступлений; остаточная стоимость "
    f"{100 * nbv_pct:.3f} % выручки; ключ = 0,06 % × (1 − {gain_share:.3f}) = {100 * disposal_key:.3f} %")
R.data["disposal_nbv"] = {"gain_share_2024_1h26": r6(gain_share), "nbv_pct_2024_1h26": r6(nbv_pct),
                          "key": disposal_key}

# ============================================================ 12. A-P3
R.h("12. A-P3. Вероятности уровней capex по режимам")
cond = BOOK["capex_prob_given_regime"]
R.data["capex_prob"] = {"conditional": cond}
rp = BOOK["regime_prob"]
unc = {L: sum(rp[r] * cond[r][L] for r in rp) for L in ("low", "base", "high")}
exp_ss = sum(unc[L] * levels[L]["pct"] for L in unc)
R.p(f"Безусловно (режимы книги {rp}):", {k: round(v_, 4) for k, v_ in unc.items()},
    f"→ ожидаемый стационар {100 * exp_ss:.2f} % против базового {100 * base_ss:.2f} %")
R.data["capex_prob"]["book"] = {"unconditional": {k: r6(v_) for k, v_ in unc.items()},
                                "expected_ss": r6(exp_ss)}

R.save("capex")
print("\n".join(R.lines))
