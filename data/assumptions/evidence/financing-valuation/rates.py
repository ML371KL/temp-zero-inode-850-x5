"""(1) Структура ставок X5: доля фикса, «старый» фикс и его выход на оферты, спреды base/stress, частота купона
нового фикса, издержки размещения, доходность кассы.

Ключи книги: financing.fixed_share, legacy_rate, legacy_weight, spread_float{base,stress},
spread_fixed{base,stress}, fixed_coupon_freq, issuance_cost, cash_yield_k (docs/MODEL.md §0, §4.9).
Конвенции (MODEL §0): legacy_rate, спред флоатеров к КС, issuance_cost и k — простые годовые ставки
(ACT/365: купон; проценты / средний долг / доля года); spread_fixed — к эффективной доходности (G-спред к
КБД), в ставку долга новый фикс входит купоном облигации с fixed_coupon_freq выплатами в год.
Читает только inputs/. Выход: rates_out.json, rates.out.
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import Counter

from common import (INPUTS, Report, avg_key, d, half_bounds, halves, key_rate_fn, load, mean, median,
                    overlap_years, par_bond, r4, r6, v, write_json, zcyc_check, zcyc_fn, zcyc_load)

R = Report("rates")
D = load("debt.json")
_K = load("keyrate.json")
KR = key_rate_fn(_K["changes_2013_2023"] + _K["changes"])
BONDS = D["bonds"]
REP = D["reported"]
INT = D["interest"]
ANCHOR, SEP28 = d("2026-06-30"), d("2026-09-28")
out: dict = {}

# ------------------------------------------------------------------ 1. доля фикса
R.h("1. Доля фиксированного долга")
hist = {k: 1 - x["v"] for k, x in D["floating_share_history"].items()}
for k, x in hist.items():
    R(f"  {k}: фикс {x:.0%}  ({D['floating_share_history'][k]['src']})")
policy_era = [x for k, x in hist.items() if k >= "2024-12-31"]
R(f"  среднее всех 5 дат {mean(hist.values()):.3f}; с начала политики (31.12.2024–30.06.2026) {mean(policy_era):.3f}; "
  f"политика: «>50 % заимствований по фиксированным ставкам» (презентация, слайд 27)")

borrow = v(INT["borrowings_2026_06_30"])
float_share = D["floating_share_history"]["2026-06-30"]["v"]
float_bonds_0630 = sum(b["face_2026_06_30"] for b in BONDS if b["type"] == "floating")
fixed_bonds_0630 = sum(b["face_2026_06_30"] for b in BONDS if b["type"] == "fixed")
banks = D["banks"]["short_2026_06_30"] + D["banks"]["long_2026_06_30"]
float_total = float_share * borrow
float_banks = float_total - float_bonds_0630
fixed_banks = banks - float_banks
R(f"  30.06.2026: займы {borrow:.3f}; плавающих 55 % = {float_total:.2f}; облигации: плавающие {float_bonds_0630:.3f}, "
  f"фикс {fixed_bonds_0630:.3f}; банки {banks:.3f} → плавающие {float_banks:.2f} ({float_banks / banks:.0%}), "
  f"фикс {fixed_banks:.2f}")
float_bonds_0928 = sum(b["face_2026_09_28"] for b in BONDS if b["type"] == "floating")
fixed_bonds_0928 = sum(b["face_2026_09_28"] for b in BONDS if b["type"] == "fixed")
fixed_0928 = fixed_bonds_0928 + fixed_banks
var_a = fixed_0928 / (fixed_0928 + float_bonds_0928 + float_banks)                 # банки не менялись
net_new = float_bonds_0928 - float_bonds_0630                                       # +22 +17,5 −10
var_b = fixed_0928 / (fixed_0928 + float_bonds_0928 + float_banks - net_new)       # размещения погасили банки
R(f"  28.09.2026: облигации плавающие {float_bonds_0928:.3f} (+{net_new:.1f}: 003P-20, 003P-21, −003P-04), фикс "
  f"{fixed_bonds_0928:.3f}; банки после 30.06 не раскрыты → доля фикса {var_a:.3f} (банки те же) … "
  f"{var_b:.3f} (размещения заменили плавающие кредиты)")
fixed_2026h2 = round((var_a + var_b) / 2, 2)
fixed_book = {"2026H2": fixed_2026h2, "2027": 0.50, "LT": 0.55, "LT_from": 2028}
R(f"  КНИГА fixed_share = {fixed_book}: 2П2026 — середина двух прочтений 28.09; 2027 — середина пути к политике "
  f"(оферты 2027 г. переоценивают {sum(b['face_2026_09_28'] for b in BONDS if b['exit_date'] and b['exit_date'][:4] == '2027' and b['type'] == 'fixed'):.1f} "
  f"млрд фикса); с 2028 — 0,55 = среднее пяти дат ({mean(hist.values()):.3f}) и «>50 %» политики")
out["fixed_share"] = {"history_fixed": {k: r4(x) for k, x in hist.items()}, "mean_all": r4(mean(hist.values())),
                      "mean_policy_era": r4(mean(policy_era)), "sep28_banks_same": r4(var_a),
                      "sep28_bonds_replaced_banks": r4(var_b), "fixed_banks_0630": r4(fixed_banks),
                      "float_banks_0630": r4(float_banks), "book": fixed_book}

# ------------------------------------------------------------------ 2. «старый» фикс: выход на оферты
R.h("2. «Старый» фикс: купон и выход на оферты")
legacy_bonds = [b for b in BONDS if b["type"] == "fixed" and b["face_2026_09_28"] > 0]
st_share = D["banks"]["short_2026_06_30"] / banks
bank_st, bank_lt = fixed_banks * st_share, fixed_banks * (1 - st_share)
R("  облигации с фиксированным купоном (номинал 28.09.2026, купон, оферта):")
for b in sorted(legacy_bonds, key=lambda b: b["exit_date"]):
    R(f"    {b['series']}: {b['face_2026_09_28']:7.3f}  {b['coupon']:.4f}  {b['exit_date']}")
R(f"  банки с фиксом ≈{fixed_banks:.2f} (оценка п. 1): короткие {bank_st:.2f} гасятся равномерно 07.2026–12.2027 "
  f"(«2026–2027»), длинные {bank_lt:.2f} — 01.2027–12.2028 («2027–2028»); график по траншам не раскрыт (прим. 15)")


def bank_left(amount: float, a: dt.date, b: dt.date, t: dt.date) -> float:
    if t <= a:
        return amount
    if t >= b:
        return 0.0
    return amount * (b - t).days / (b - a).days


def legacy_at(t: dt.date) -> tuple[float, float]:
    """(облигации, банки) «старого» фикса на дату t."""
    bo = sum(b["face_2026_09_28"] for b in legacy_bonds if d(b["exit_date"]) > t)
    ba = (bank_left(bank_st, dt.date(2026, 7, 1), dt.date(2028, 1, 1), t)
          + bank_left(bank_lt, dt.date(2027, 1, 1), dt.date(2029, 1, 1), t))
    return bo, ba


gross_0928 = borrow + net_new                          # займы 28.09 ≈ 30.06 + нетто-размещения облигаций
weights, rows = {}, []
num = den = 0.0
for p in halves("2026H2", "2028H2"):
    a, b = half_bounds(p)
    days = [a + dt.timedelta(i) for i in range((b - a).days)]
    lb = mean(legacy_at(t)[0] for t in days)
    lk = mean(legacy_at(t)[1] for t in days)
    coup = mean(sum(x["face_2026_09_28"] * x["coupon"] for x in legacy_bonds if d(x["exit_date"]) > t)
                for t in days)
    fs = fixed_2026h2 if p == "2026H2" else (0.50 if p.startswith("2027") else 0.55)
    fixed_stock = fs * gross_0928
    w = min(1.0, (lb + lk) / fixed_stock)
    weights[p] = round(w, 2)
    num += coup                       # купонный поток облигаций «старого» фикса, млрд ₽/год
    den += lb
    rows.append((p, lb, lk, fixed_stock, w))
    R(f"  {p}: старый фикс облигации {lb:6.2f} + банки {lk:6.2f} = {lb + lk:6.2f} из фикса {fs:.2f} × {gross_0928:.1f} = "
      f"{fixed_stock:6.2f} → ℓ = {w:.3f}")
legacy_bond_rate = num / den
bank_years = sum(lk for _, _, lk, _, _ in rows)
R(f"  купон «старого» фикса облигаций, взвешенный по номиналу и времени до оферт: {legacy_bond_rate:.4f} "
  f"(простой средний на 28.09 — {sum(x['face_2026_09_28'] * x['coupon'] for x in legacy_bonds) / fixed_bonds_0928:.4f}); "
  f"ставка фиксированных кредитов — из сверки п. 3")
legacy_book = {**weights, "LT": 0.0, "LT_from": 2029}
out["legacy"] = {"rows": [{"period": p, "bonds": r4(lb), "banks": r4(lk), "fixed_stock": r4(fs), "weight": r4(w)}
                          for p, lb, lk, fs, w in rows],
                 "bond_rate_time_weighted": r6(legacy_bond_rate), "book_weight": legacy_book}

# ------------------------------------------------------------------ 3. сверка процентов 1П2026: ставка банков
R.h("3. Сверка процентов 1П2026: что платят банки")
h0, h1 = dt.date(2026, 1, 1), dt.date(2026, 7, 1)
bond_int = 0.0
bond_days = 0.0
for b in BONDS:
    start = max(h0, d(b["issue_date"]))
    end = min(h1, d(b["exit_date"]))
    face = b["face_2025_12_31"] or b["face_2026_06_30"]
    if end <= start or face <= 0:
        continue
    for i in range((end - start).days):
        t = start + dt.timedelta(i)
        rate = b["coupon"] if b["type"] == "fixed" else KR(t) + b["spread"]
        bond_int += face * rate / 365
        bond_days += face / 365
gross_int = v(INT["loans_h1_2026"]) + v(INT["capitalised_h1_2026"]) - v(INT["tc_amortised_h1_2026"])
bonds_0331 = sum((b["face_2025_12_31"] or b["face_2026_06_30"]) for b in BONDS
                 if d(b["issue_date"]) <= dt.date(2026, 3, 31) < d(b["exit_date"]))
banks_1231 = D["banks"]["short_2025_12_31"] + D["banks"]["long_2025_12_31"]
banks_0331 = v(INT["total_debt_2026_03_31"]) - v(INT["leasing_2026_06_30"]) - bonds_0331
banks_avg = (banks_1231 / 2 + banks_0331 + banks / 2) / 2
bank_int = gross_int - bond_int
yrs = (h1 - h0).days / 365
bank_rate = bank_int / (banks_avg * yrs)
ks_h1 = avg_key(KR, h0, h1)
R(f"  проценты по займам 1П2026: {v(INT['loans_h1_2026']):.3f} + капитализированные {v(INT['capitalised_h1_2026']):.3f} − "
  f"амортизация издержек {v(INT['tc_amortised_h1_2026']):.3f} = {gross_int:.3f} (МСФО 1П2026 прим. 15, 21)")
R(f"  купоны облигаций по дням (фикс — купон, флоатер — КС дня + спред): {bond_int:.3f}; средний номинал {bond_days / yrs:.1f}")
R(f"  банки: 31.12.2025 {banks_1231:.1f}, 31.03.2026 ≈{banks_0331:.1f} (общий долг {v(INT['total_debt_2026_03_31']):.3f} − "
  f"лизинг − облигации {bonds_0331:.1f}), 30.06.2026 {banks:.1f}; среднее {banks_avg:.1f}")
R(f"  проценты банков {bank_int:.3f} → средняя ставка {bank_rate:.4f} при средней КС 1П2026 {ks_h1:.4f} "
  f"(превышение {bank_rate - ks_h1:+.4f})")
fb = float_banks / banks
bond_fixed_avg = sum(b["coupon"] * b["face_2026_06_30"] for b in legacy_bonds) / fixed_bonds_0630
s_bank_if_fix_eq_bonds = (bank_rate - (1 - fb) * bond_fixed_avg) / fb - ks_h1
R(f"  если фиксированные кредиты стоят как фиксированные облигации ({bond_fixed_avg:.4f}), то плавающие банки — "
  f"КС + {s_bank_if_fix_eq_bonds:.4f}; если же банки платят КС + спред облигаций, фикс банков = "
  f"{(bank_rate - fb * (ks_h1 + 0.01177)) / (1 - fb):.4f}")
r_bank_fixed = (bank_rate - fb * (ks_h1 + 0.01177)) / (1 - fb)
legacy_rate = (legacy_bond_rate * den + r_bank_fixed * bank_years) / (den + bank_years)
legacy_book_rate = round(legacy_rate, 3)
R(f"  «старый» фикс целиком (облигации {legacy_bond_rate:.4f} и фиксированные кредиты {r_bank_fixed:.4f}, веса — "
  f"номинал × время до погашения): {legacy_rate:.4f} → КНИГА legacy_rate = {legacy_book_rate}")
out["legacy"]["bank_fixed_rate"] = r4(r_bank_fixed)
out["legacy"]["legacy_rate"] = r6(legacy_rate)
out["legacy"]["book_rate"] = legacy_book_rate
out["h1_2026_reconciliation"] = {"gross_interest": r4(gross_int), "bond_coupons": r4(bond_int),
                                 "banks_avg": r4(banks_avg), "bank_interest": r4(bank_int),
                                 "bank_rate": r4(bank_rate), "key_avg_h1_2026": r4(ks_h1),
                                 "bank_float_spread_if_fixed_as_bonds": r4(s_bank_if_fix_eq_bonds)}

# ------------------------------------------------------------------ 4. спреды: base и stress
R.h("4. Спреды к ключевой (плавающий) и к ОФЗ (фикс)")
R(f"  проверка формулы КБД на узлах ISS: " + ", ".join(
    f"{x}: {zcyc_check(x):.4f} п.п." for x in ("2024-12-26", "2025-02-28", "2025-11-26", "2026-09-25")))
flo = [(b["series"], b["issue_date"], b["spread"], KR(d(b["issue_date"])), b["face_2026_09_28"])
       for b in BONDS if b["type"] == "floating"]
R("  плавающие выпуски: спред при размещении (КС на дату):")
for s, dd, sp, k, f in sorted(flo, key=lambda x: x[1]):
    R(f"    {s} {dd}: КС + {sp:.4f}" + (f" (КС {k:.4f})" if k else "") + f"; в обращении 28.09 {f:.1f}")
w_now = sum(sp * f for _, _, sp, _, f in flo) / sum(f for *_, f in flo)
recent = [sp for _, dd, sp, _, _ in flo if dd >= "2025-09-01"]
crunch_fl = [sp for _, dd, sp, _, _ in flo if "2024-10-28" <= dd <= "2025-06-08"]   # КС 21 %
# режим высоких ставок = как в мире M (КС 14 → 17,5 %): все размещения при КС ≥ 16 %, включая кризис 12.2024–03.2025
regime_fl = [sp for _, dd, sp, k, _ in flo if k >= 0.16]
tc = v(INT["tc_amortised_h1_2026"]) * 2 / (bond_days / yrs)
R(f"  взвешенный спред флоатеров 28.09: {w_now:.4f}; размещения с 09.2025 (КС 17 → 14 %): среднее {mean(recent):.4f}; "
  f"при КС 21 % (пик 10.2024–06.2025): {[round(x, 4) for x in crunch_fl]}; все размещения при КС ≥ 16 %: "
  f"{[round(x, 4) for x in regime_fl]} → среднее "
  f"{mean(regime_fl):.4f}; амортизация издержек 1П2026 × 2 на средний номинал одних облигаций — {tc:.4f} в год "
  f"(мера книги 1.0; по МСФО издержки относятся ко всем займам — ставка issuance_cost ниже)")
fix_rows = []
for b in [x for x in BONDS if x["type"] == "fixed" and x["issue_date"] >= "2024-12-01"] + \
        [dict(series=x["series"], issue_date=x["date"], coupon=x["coupon"], exit_date=x["put"], freq=x["freq"])
         for x in D["placements_stress_extra"] if x.get("date") and x["series"] == "003P-07"]:
    if b["series"] == "003P-07" and b.get("coupon") == 0.1325:
        continue                                   # 13,25 % — купон после оферты 25.09.2025, не размещения
    date = b["issue_date"]
    p, _ = zcyc_load(date)
    Y = zcyc_fn(p)
    years = (d(b["exit_date"]) - d(date)).days / 365.0
    y_eff, dur = par_bond(b["coupon"], b["freq"], years)
    g = y_eff - Y(dur)
    fix_rows.append((date, b["series"], b["coupon"], years, dur, y_eff, Y(dur), g, KR(d(date))))
fix_rows.sort()
R("  фиксированные выпуски: G-спред при размещении = эффективная доходность по номиналу − КБД на дюрации")
for date, s, c, yrs_, dur, ye, zc, g, k in fix_rows:
    R(f"    {s} {date}: купон {c:.4f}, до оферты {yrs_:.2f} г., дюр. {dur:.2f}; доходн. {ye:.4f} − КБД {zc:.4f} = "
      f"G {g:+.4f} (КС {k:.4f})")
g_crunch = [g for date, s_, c, y_, du, *_, g, k in fix_rows if k >= 0.20 and du >= 1.5]
# стресс для узла 3 года — режим высоких ставок (КС ≥ 16 %) с дюрацией ≥ 1,5 года; у 9–12-месячных оферт спред
# задаёт перевёрнутая кривая: короткие корпоративные доходности привязаны к денежному рынку (X4 §5)
g_stress = [g for date, s_, c, y_, du, *_, g, k in fix_rows if k >= 0.16 and du >= 1.5]
g_base = [g for date, *_, g, k in fix_rows if date >= "2025-07-01"]
with open(INPUTS / "gspread_2026-09-25.csv", encoding="utf-8") as f:
    sec = [r for r in csv.DictReader(f) if r["g_spread_bp"]]
g_now = [float(r["g_spread_bp"]) / 10000 for r in sec]
g_liquid = [float(r["g_spread_bp"]) / 10000 for r in sec if float(r["value_mn"]) >= 10]
R(f"  G-спред при КС ≥ 20 % (пик 12.2024–06.2025), дюрация ≥ 1,5 года: {[round(x, 4) for x in g_crunch]} → среднее "
  f"{mean(g_crunch):.4f} (хвост: мир M до КС 21 % не доходит)")
R(f"  G-спред при КС ≥ 16 % (режим высоких ставок, как мир M), дюрация ≥ 1,5 года: {[round(x, 4) for x in g_stress]} → "
  f"среднее {mean(g_stress):.4f}")
R(f"  G-спред при размещении с 07.2025 (КС 18 → 15 %): {[round(x, 4) for x in g_base]} → среднее {mean(g_base):.4f}, медиана "
  f"{median(g_base):.4f}")
R(f"  вторичный рынок 25.09.2026 (X4 §5): медиана {median(g_now):.4f} (n {len(g_now)}), ликвидные (оборот ≥ 10 млн ₽) "
  f"{[round(x, 4) for x in g_liquid]}")
def step(x: float, s: float = 0.0005) -> float:
    """Округление к шагу 0,05 п.п."""
    return round(round(x / s) * s, 4)


# Спреды книги — чистые: плата кредиторам по рыночной цене (долг в требованиях по номиналу). Издержки
# размещения — отдельный ключ financing.issuance_cost (ниже): модель прибавляет их к ставке новой части долга
# (щит на них законен: они вычитаются из базы налога) и вычитает их PV из EV (MODEL §4.9, §5).
fl_base = step(w_now)
fl_stress = step(fl_base + (mean(regime_fl) - w_now))
fx_base = step(median(g_now + g_base))
fx_stress = step(fx_base + (mean(g_stress) - mean(g_base)))
R(f"  КНИГА spread_float (без издержек): base {fl_base} = взвешенный спред {w_now:.4f}; stress {fl_stress} = base + "
  f"(среднее размещений при КС ≥ 16 % {mean(regime_fl):.4f} − {w_now:.4f})")
R(f"  КНИГА spread_fixed (без издержек): base {fx_base} = медиана G-спредов (вторичка 25.09 и размещения с 07.2025, "
  f"{median(g_now + g_base):.4f}); stress {fx_stress} = base + (режим КС ≥ 16 % {mean(g_stress):.4f} − "
  f"размещения с 07.2025 {mean(g_base):.4f})")
# Издержки размещения на валовой долг: займы по МСФО — за вычетом неамортизированных издержек, относящихся ко
# ВСЕМ кредитам и займам (не только к облигациям). Годовая ставка = амортизация LTM / средние займы LTM;
# сверка деньгами — понесённые издержки (прирост остатка + амортизация) 1П2026 на средние займы 1П2026.
TC = INT["transaction_costs"]
BQ = INT["borrowings_quarter_end"]
amort_ltm = TC["amortised"]["2025"] - TC["amortised"]["2025H1"] + TC["amortised"]["2026H1"]
qs = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
debt_ltm = (BQ[qs[0]] / 2 + sum(BQ[q] for q in qs[1:-1]) + BQ[qs[-1]] / 2) / (len(qs) - 1)
ic_amort = amort_ltm / debt_ltm
debt_h1 = (BQ["2025-12-31"] + BQ["2026-06-30"]) / 2
incurred_h1 = TC["unamortised"]["2026-06-30"] - TC["unamortised"]["2025-12-31"] + TC["amortised"]["2026H1"]
ic_cash = incurred_h1 * 2 / debt_h1
ic_book = round(ic_amort, 4)
R(f"  издержки размещения: амортизация LTM (2П2025 {TC['amortised']['2025'] - TC['amortised']['2025H1']:.3f} + 1П2026 "
  f"{TC['amortised']['2026H1']:.3f}) = {amort_ltm:.3f} на средние займы LTM {debt_ltm:.1f} = {ic_amort:.5f} в год; "
  f"деньгами 1П2026 (остаток {TC['unamortised']['2025-12-31']:.3f} → {TC['unamortised']['2026-06-30']:.3f} + "
  f"амортизация) {incurred_h1:.3f} × 2 / {debt_h1:.1f} = {ic_cash:.5f} (с приростом долга)")
R(f"  КНИГА issuance_cost = {ic_book} (к ставке новой части долга; старый фикс несёт свои издержки в прошлом)")
out["issuance_cost"] = {"amortised_ltm": r4(amort_ltm), "debt_ltm_avg": r4(debt_ltm), "rate_amortised": r6(ic_amort),
                        "incurred_h1_2026": r4(incurred_h1), "rate_cash_h1_2026": r6(ic_cash), "book": ic_book}
R(f"  сверка со ставкой банков (п. 3): средняя ставка банков 1П2026 {bank_rate:.4f} против КС {ks_h1:.4f} — банки не "
  f"дороже облигаций; спред плавающего долга книги ({fl_base}) не занижен")
out["spreads"] = {"float_weighted_0928": r4(w_now), "float_recent_mean": r4(mean(recent)), "float_crunch": crunch_fl,
                  "float_regime": regime_fl, "g_crunch_mean": r4(mean(g_crunch)),
                  "tc_per_year": r4(tc), "fixed_placements": [
                      {"date": a, "series": s, "coupon": c, "years": r4(y), "dur": r4(du), "y_eff": r4(ye),
                       "kbd": r4(zc), "g": r4(g), "key": k} for a, s, c, y, du, ye, zc, g, k in fix_rows],
                  "g_stress_mean": r4(mean(g_stress)), "g_base_mean": r4(mean(g_base)),
                  "g_secondary_median": r4(median(g_now)),
                  "book": {"spread_float": {"base": fl_base, "stress": fl_stress},
                           "spread_fixed": {"base": fx_base, "stress": fx_stress}}}

# ------------------------------------------------------------------ 4b. частота купона нового фикса
R.h("4b. Частота купона нового фикса: доходность → купон (MODEL §0, §4.9)")
fixed_all = [b for b in BONDS if b["type"] == "fixed"]
fixed_live = [b for b in fixed_all if b["face_2026_09_28"] > 0]
freq_all = Counter(b["freq"] for b in fixed_all)
freq_live = Counter(b["freq"] for b in fixed_live)
face_live = {f: sum(b["face_2026_09_28"] for b in fixed_live if b["freq"] == f) for f in sorted(freq_live)}
R("  фиксированные выпуски реестра (выплат купона в год; номинал 28.09.2026):")
for b in sorted(fixed_all, key=lambda b: b["issue_date"]):
    R(f"    {b['series']} {b['issue_date']}: {b['freq']:2d}  {b['face_2026_09_28']:7.3f}"
      + ("" if b["face_2026_09_28"] > 0 else f" (погашен {b['exit_date']})"))
R(f"  все выпуски реестра: {dict(sorted(freq_all.items()))} (выплат в год: выпусков); в обращении 28.09: "
  f"{dict(sorted(freq_live.items()))}, номинал {face_live}")
freq_book = freq_live.most_common(1)[0][0]
assert len(freq_live) == 1, "в обращении фикс с разной частотой купона — суждение нужно пересмотреть"
W_ = load("worlds.json")["worlds"]
y_ex = W_["N"]["zero_curve"]["3"] + fx_base
c_ex = freq_book * ((1 + y_ex) ** (1 / freq_book) - 1)
R(f"  G-спред A-F1b — к эффективной доходности (par_bond: (1 + c/m)^m − 1). Облигация по номиналу с m купонами "
  f"в год при эффективной доходности y платит купон cpn(y) = m((1 + y)^(1/m) − 1); за полугодие начисляется "
  f"cpn/2. Пример: мир N, узел 3 года + base = {y_ex:.4f} → купон {c_ex:.4f}, за полугодие {c_ex / 2:.5f} "
  f"(корень {(1 + y_ex) ** 0.5 - 1:.5f}, половина доходности {y_ex / 2:.5f})")
R(f"  КНИГА fixed_coupon_freq = {freq_book}: все {len(fixed_live)} фиксированных выпусков в обращении "
  f"({sum(face_live.values()):.1f} млрд) и все размещения с 12.2024 платят купон ежемесячно; квартальный — только "
  f"погашенный {', '.join(b['series'] for b in fixed_all if b['freq'] != freq_book)}")
out["coupon_freq"] = {"issues": {b["series"]: b["freq"] for b in fixed_all},
                      "by_freq_all": {str(k): v for k, v in sorted(freq_all.items())},
                      "by_freq_live": {str(k): v for k, v in sorted(freq_live.items())},
                      "face_live_by_freq": {str(k): r4(v) for k, v in face_live.items()},
                      "example_n": {"yield": r6(y_ex), "coupon": r6(c_ex)}, "book": freq_book}

# ------------------------------------------------------------------ 5. доходность кассы
R.h("5. Доходность кассы (подушки) в долях ключевой")
Q = load("quarterly_finance.json")["quarters"]
REV = load("ev_history.json")["revenue_ltm"]
OPC = 0.0106          # операционная касса области capex-wc-tax (фрагмент capex-wc-tax.yaml, A-W3)
ks = []
for q in Q:
    name = f"{q['quarter'][2:]}Q{q['quarter'][0]}"          # "1Q2024" → "2024Q1"
    rev = REV.get(name)
    cash = (q["cash_incl_sti_start_mn"] + q["cash_incl_sti_end_mn"]) / 2000
    opc = OPC * rev if rev else None
    yld = q["fin_inc_pre16_mn"] * 4 / 1000 / (cash - opc) if opc else None
    lumpy = q["quarter"] in ("3Q2025", "4Q2025", "1Q2026")
    k = yld / (q["key_rate_avg_pct"] / 100) if yld else None
    if k and not lumpy:
        ks.append(k)
    R(f"  {q['quarter']}: касса средняя {cash:6.1f}, операционная {opc if opc else float('nan'):5.1f}, доход "
      f"{q['fin_inc_pre16_mn'] / 1000:5.2f} → доходность неоперационной {yld if yld else float('nan'):.4f}, КС "
      f"{q['key_rate_avg_pct'] / 100:.4f}, k {k if k else float('nan'):.3f}" + (" (выплаты внутри квартала — не в счёт)" if lumpy else ""))
dep = INT["deposits"]
R(f"  30.06.2026: {dep['text']} (прим. 8); КС 14,25 % с 22.06, 14,50 % до того → рублёвые депозиты ≈ 1,0 × КС; "
  f"валюта {dep['fx_deposits'] + dep['fx_cash_accounts']:.1f} млрд ₽ почти без дохода")
k_mean = mean(ks)
R(f"  k по кварталам без выплат внутри квартала: {[round(x, 3) for x in ks]} → среднее {k_mean:.3f}, медиана "
  f"{median(ks):.3f}")
k_now = ks[-1]
k_old = ks[:-1]
k_book = 0.90
R(f"  КНИГА cash_yield_k = {k_book}: середина между k 2К2026 ({k_now:.2f}; единственный квартал нынешнего режима "
  f"«подушка без излишка» без выплат внутри) и ставкой рублёвых депозитов (≈1,0 × КС); 2024–1П2025 "
  f"({min(k_old):.2f}–{max(k_old):.2f}) — "
  f"другой режим: 150–260 млрд ₽ вкладов, размещённых на росте ставок выше ключевой")
out["cash_yield"] = {"k_quarters": [r4(x) for x in ks], "k_mean": r4(k_mean), "k_median": r4(median(ks)),
                     "k_2026q2": r4(k_now), "book": k_book}

# ------------------------------------------------------------------ 6. сверка ставки модели 2П2026
R.h("6. Ставка долга модели на 2П2026 при книжных значениях (MODEL §4.9)")
W = load("worlds.json")["worlds"]
model_rate = {}
for w, credit in (("N", "base"), ("H", "base"), ("M", "stress")):
    key = W[w]["key_rate"]["2026H2"]
    z3 = W[w]["zero_curve"]["3"]
    lw = legacy_book["2026H2"]
    y_new = z3 + out["spreads"]["book"]["spread_fixed"][credit]
    c_new = freq_book * ((1 + y_new) ** (1 / freq_book) - 1)
    fixed = lw * legacy_book_rate + (1 - lw) * (c_new + ic_book)
    rate = fixed_2026h2 * fixed + (1 - fixed_2026h2) * (key + out["spreads"]["book"]["spread_float"][credit] + ic_book)
    model_rate[w] = r6(rate)
    R(f"  мир {w} ({credit}): КС {key:.4f}, новый фикс: доходность {y_new:.4f} → купон {c_new:.4f}; фикс {fixed:.4f}, "
      f"ставка долга (простая) {rate:.4f}, начисление за полугодие {rate / 2:.5f}")
out["model_rate_2026h2"] = model_rate
R(f"  факт: эффективная ставка по займам 1П2026 {REP['effective_rate_h1_2026_pct'] / 100:.4f} при средней КС "
  f"{ks_h1:.4f}; купон облигаций 28.09 ≈15,2 % при КС 14,00 % (X2 §3.4)")

write_json("rates_out.json", out)
R.save()
