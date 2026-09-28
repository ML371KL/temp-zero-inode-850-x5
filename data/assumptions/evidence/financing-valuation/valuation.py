"""(4)–(5) β_u, ERP, премия, дисконт за управление снизу вверх, шаг печати, настройки полосы.

Ключи книги: valuation.beta_u, erp, governance_discount, headline.print_step, uncertainty.{draws, seed},
reverse_dcf.subsample (docs/MODEL.md §5, §7.2, §9, §11). Читает только inputs/.
Выход: valuation_out.json, valuation.out.
"""
from __future__ import annotations

from common import Report, load, r4, v, write_json

R = Report("valuation")
BETA = load("beta.json")
DAM = load("damodaran.json")
MAG = load("magnit_book.json")
MK = load("market.json")
FX = load("facts_x5.json")
out: dict = {}

# ------------------------------------------------------------------ β_u
R.h("1. β_u X5: окна, метод Магнита (полная доходность к MCFTR, недели, Харрис — Пингл β_d 0,1)")
x4 = BETA["x4"]
c = x4["center_weekly_mcftr"]
R(f"  окно одной акции X5 {c['from']}–{c['to']} (X4, 25.09.2026): β_E {c['beta']:.3f} (se NW {c['se_nw']:.3f}, n {c['n']}, "
  f"R² {c['r2']:.2f}), D/V {c['dv_mean']:.3f} → β_u {c['beta_u_hp_bd0.1']:.3f}; поправка Блюма (⅔β + ⅓) → β_E "
  f"{c['beta_blume']:.3f} → β_u {c['beta_blume'] * (1 - c['dv_mean']) + 0.1 * c['dv_mean']:.3f}")
res = BETA["magnit_book14"]["results"]


k_op = BETA["magnit_book14"]["k_book"]["X5"]
# β_u X5 окон 2/3/5 лет — ровно числа листа Магнита: его центры окон, делённые на его же поправку k (у X5 k = 1)
x5_bu = [x / k_op for x in BETA["magnit_book14"]["x5_weekly_windows"]]


def bu(key: str, b_u: float | None = None) -> tuple[float, float, float]:
    """β_E окна, D/V окна, β_u (Харрис — Пингл, β_d 0,1). D/V выводится из β_u листа, если он дан, иначе —
    отношение средних ЧД и E окна (у листа — среднее недельных D/V; расхождение ≤ 0,01 β_u)."""
    r = res[key]
    if b_u is not None:
        dv = (r["beta"] - b_u) / (r["beta"] - 0.1)
        return r["beta"], dv, b_u
    dv = r["nd_mean"] / (r["nd_mean"] + r["e_mean"])
    return r["beta"], dv, r["beta"] * (1 - dv) + 0.1 * dv


windows = {}
for (lab, key), b_u in zip((("2 года", "X5|2 года|W"), ("3 года", "X5|3 года|W"), ("5 лет", "X5|5 лет|W"),
                            ("до 2022", "X5|до 2022 (02.2018–02.2022)|W")), x5_bu + [None]):
    be, dv, b = bu(key, b_u)
    se = res[key]["se_hac"]
    windows[lab] = {"beta_e": r4(be), "dv": r4(dv), "beta_u": r4(b), "se_e": r4(se),
                    "ci95": [r4(b - 1.96 * se * (1 - dv)), r4(b + 1.96 * se * (1 - dv))]}
    R(f"  лист беты книги Магнита 1.4 (до {BETA['magnit_book14']['end']}), X5 {lab}: β_E {be:.3f} (se {se:.3f}), "
      f"D/V {dv:.3f} → β_u {b:.3f}; 95 % {b - 1.96 * se * (1 - dv):.3f}–{b + 1.96 * se * (1 - dv):.3f}")
for sec in ("MGNT", "LENT"):
    for lab in ("2 года", "3 года", "5 лет"):
        key = f"{sec}|{lab}|W"
        be, dv, b = bu(key)
        windows[f"{sec} {lab}"] = {"beta_e": r4(be), "dv": r4(dv), "beta_u": r4(b)}
R("  аналоги (собственные β_u, тот же метод, D/V — по средним окна): " + "; ".join(
    f"{k} {x['beta_u']:.2f}" for k, x in windows.items() if k.startswith(("MGNT", "LENT"))))
R(f"  книга Магнита 1.6: β_u 0,70 = β_u X5 3 года × поправка на операционный рычаг Магнита {k_op:.3f} "
  f"(ВП/EBITDA 4,73 против 4,09); для X5 поправка = 1 (X5 — сама себе аналог)")
beta_book, beta_lo, beta_hi = 0.60, 0.45, 0.75
w3 = windows["3 года"]
R(f"  КНИГА beta_u = {beta_book}: окно 3 года — то же правило, что у книги Магнита (одна измерительная база двух "
  f"панелей: 0,70 Магнита = {w3['beta_u']:.3f} × {k_op:.3f}); середина окон 2 / 3 / 5 лет "
  f"({windows['2 года']['beta_u']:.2f} / {w3['beta_u']:.2f} / {windows['5 лет']['beta_u']:.2f})")
R(f"  ДИАПАЗОН {beta_lo}–{beta_hi} (± 0,15, как у Магнита): низ — 95 % окна одной акции "
  f"({windows['2 года']['ci95'][0]:.2f}) и собственные β_u аналогов 0,30–0,57 (окна 2–3 года); верх — 95 % окна 3 года "
  f"({w3['ci95'][1]:.2f}) и окно 5 лет ({windows['5 лет']['beta_u']:.2f})")
out["beta"] = {"windows": windows, "x4_one_share": {"beta_u": c["beta_u_hp_bd0.1"], "blume_beta_e": c["beta_blume"]},
               "k_op_magnit": r4(k_op), "book": beta_book, "axis": [beta_lo, beta_hi]}

# ------------------------------------------------------------------ ERP
R.h("2. ERP поверх ОФЗ и её диапазон")
ru, us = DAM["Russia"], DAM["United States"]
mature = DAM["mature_erp"]
erp_ofz = ru["erp"] - ru["default_spread"]
R(f"  Damodaran ({DAM['last_updated']}; sha256 страницы {DAM['sha256'][:12]}…): Россия — дефолтный спред "
  f"{ru['default_spread']:.4f}, страновая премия {ru['crp']:.4f}, ERP {ru['erp']:.4f}; зрелая премия {mature:.4f} "
  f"(США {us['erp']:.4f} − {us['crp']:.4f})")
R(f"  центр книги Магнита и X5: ERP поверх ОФЗ = {ru['erp']:.4f} − {ru['default_spread']:.4f} = {erp_ofz:.4f} "
  f"(дефолтный спред уже в доходности ОФЗ)")


def lam_erp(beta: float) -> float:
    """ERP-эквивалент λ-подхода (λ = 1): премия β·ERP_зрел + (CRP − DS), делённая на β."""
    return (beta * mature + ru["crp"] - ru["default_spread"]) / beta


def beta_erp(beta: float) -> float:
    """ERP-эквивалент β-подхода: β·ERP_total − DS, делённая на β."""
    return (beta * ru["erp"] - ru["default_spread"]) / beta


R(f"  верх Магнита — λ-конвенция при β 0,70: премия {0.70 * mature + ru['crp'] - ru['default_spread']:.4f} → "
  f"ERP {lam_erp(0.70):.4f} (книга Магнита: 4,30 п.п. / 0,70 = 6,14 → 6,2 %) — воспроизводится")
R(f"  та же конвенция при β X5 {beta_book}: премия {beta_book * mature + ru['crp'] - ru['default_spread']:.4f} → "
  f"ERP {lam_erp(beta_book):.4f}; β-подход: {beta_erp(beta_book):.4f} (не берётся — низ книги Магнита — реализованная "
  f"премия)")
R("  низ — реализованная премия акций над ОФЗ с 2003 г. +4,9 п.п. (книга Магнита 1.6, A-V2; evidence/book-1.5/"
  "book15_inputs.yaml, rows_3_12_erp) — свойство рынка, общее для двух панелей")
erp_hi = round(lam_erp(beta_book) + 1e-9, 3)
R(f"  КНИГА erp = {erp_ofz:.4f}; ось {0.049}–{erp_hi}: верх — λ-конвенция книги Магнита при β X5 (перенос правила, "
  f"а не числа: 6,2 % Магнита выведены при β 0,70; тот же страновой риск без масштаба β при β 0,60 даёт "
  f"{lam_erp(beta_book) * 100:.2f} %)")
prem = beta_book * erp_ofz
R(f"  премия β_u × ERP = {beta_book} × {erp_ofz:.4f} = {prem:.4f} (Магнит — 0,70 × 5,57 % = 3,90 п.п.); по концам "
  f"β: {beta_lo * erp_ofz:.4f}–{beta_hi * erp_ofz:.4f}; обе оси — {beta_lo * 0.049:.4f}–{beta_hi * erp_hi:.4f}")
out["erp"] = {"damodaran": {"ds": ru["default_spread"], "crp": ru["crp"], "erp_total": ru["erp"], "mature": mature},
              "center": r4(erp_ofz), "lambda_erp_at_070": r4(lam_erp(0.70)), "lambda_erp_at_book": r4(lam_erp(beta_book)),
              "beta_erp_at_book": r4(beta_erp(beta_book)), "axis": [0.049, erp_hi], "premium": r4(prem)}

# ------------------------------------------------------------------ дисконт за управление
R.h("3. Казначейский пакет и дисконт за управление (MODEL §7.2: цена = [(V0 − D) + n·k·P/1000]·(1 − g)·1000/(N + n))")
n_o = v(MK["shares_outstanding_mln"])
n_t = v(MK["treasury_mln"])
share_t = n_t / (n_o + n_t)
sale = MK["treasury_sales_2026H1"]
px_sale = v(sale["avg_price_rub"])
vw = MK["vwap"]
R(f"  казначейский пакет n = {n_t:.3f} млн = {share_t:.4f} выпущенных N + n = {n_o + n_t:.3f} млн; одобрено отчуждение в "
  f"срок до 3 лет (НС 13.11.2025), погашение не объявлялось. Продажа n акций по P_s даёт акционеру "
  f"(E + n·P_s)/(N + n) на акцию: размывание к стоимости акции V — n/(N + n)·(1 − P_s/V), то есть продажа нейтральна "
  f"только по стоимости, а не по рынку. Пакет считается в формуле цены: P_s = k × рыночная цена, k — ниже")
disc_vs = {k: 1 - px_sale / vw[k] for k in ("2026H1", "2026Q1", "2026Q2")}
R(f"  факт 1П2026: продано {v(sale['units_mln'])} млн по {px_sale:.1f} ₽ (покупатель не раскрыт); VWAP 1П2026 "
  f"{vw['2026H1']:.1f}, 1К {vw['2026Q1']:.1f}, 2К {vw['2026Q2']:.1f} → дисконт {disc_vs['2026H1']:.3f} "
  f"({disc_vs['2026Q2']:.3f}…{disc_vs['2026Q1']:.3f})")
buy = MK["treasury_buyout"]
R(f"  выкуп пакета у X5 Retail Group N.V. (470-ФЗ): {buy['shares_mln']} млн за {buy['cost_bn']} млрд = "
  f"{buy['cost_bn'] / buy['shares_mln'] * 1000:.0f} ₽ при VWAP 2К2025 {vw['2025Q2']:.0f} ₽ — ниже рынка (миноритариям "
  f"не в убыток)")
# сценарии судьбы пакета: вероятность × дисконт к рынку → k = Σ p·(1 − d)
scen = [("продажа рынку / инвесторам (SPO, ускоренная книга, обменные облигации)", 0.80, round(disc_vs["2026H1"], 3)),
        ("оплата сделок M&A по рыночной цене", 0.10, 0.0),
        ("передача нераскрытому кругу с глубоким дисконтом", 0.10, 0.50)]
k_sale = sum(p * (1 - dd) for _, p, dd in scen)
for name, p, dd in scen:
    R(f"    {name}: вероятность {p:.2f} × цена {1 - dd:.3f} рынка = {p * (1 - dd):.4f}")
k_book = round(k_sale, 4)
R(f"  (а) КНИГА treasury_sale_price_k = Σ p·(1 − d) = {k_sale:.4f} → {k_book}: ожидаемая цена продажи пакета — "
  f"{k_book} × рыночной; в дисконт за управление навес больше не входит")
lst = MK["listing"]
imo = MK["imoex_weight_pct"]
ff = v(MK["free_float"])
liq = 0.005
R(f"  (б) ликвидность и листинг: X5 — {lst['X5']}-й уровень, вес в IMOEX {imo['X5']} % ({imo['date']}), оборот "
  f"{MK['turnover_bn_day']['last_3m']}–{MK['turnover_bn_day']['all']} млрд ₽/день; free float {ff:.0%} (MOEX); Магнит — "
  f"{lst['MGNT']}-й уровень, вне IMOEX (у него 3–5 %) → X5 {liq:.3f} (у свободной доли меньше трети — не ноль)")
opq = 0.015
R(f"  (в) непрозрачность владельцев и утечки: {FX['shareholders']}; аудитор — {FX['audit_opinion_fy2025']}. Раскрытых "
  f"сделок со связанными сторонами с убытком нет (у Магнита — заём связанной стороне с потерей 30 %, авансы 127 млрд) "
  f"→ {opq:.3f} (нижняя половина «утечек» Магнита 1–3 %)")
R("  (г) политика выплаты свободного денежного потока дважды в год (≈309 млрд ₽ за 07.2025–07.2026) — агентская "
  "стоимость удержанной кассы ≈ 0: у Магнита дивидендов за 2024–2025 нет")
g_sum = liq + opq
g_book = round(g_sum + 1e-9, 2)
g_hi = round(0.02 + 0.03 + 1e-9, 2)
# крайние судьбы пакета — в ожидании k; их цена в рублях на акцию: n·Δk·P·(1 − g)/(N + n)
price_mk = MK["price_2026_09_25"]["v"]
rub = {kk: n_t * (kk - k_book) * price_mk * (1 - g_book) / (n_o + n_t) for kk in (0.5, 1.0)}
R(f"  СУММА {liq:.3f} + {opq:.3f} = {g_sum:.4f} → КНИГА governance_discount = {g_book}")
R(f"  ДИАПАЗОН 0–{g_hi}: низ — X5 на уровне среднего эмитента рынка (общий страновой риск уже в ERP); верх — "
  f"ликвидность 2 % + утечки 3 % (навес казначейского пакета — в формуле цены, не в дисконте)")
R(f"  крайние судьбы пакета (в ожидании k, не ось): весь пакет за полцены рынка {rub[0.5]:+.1f} ₽ на акцию, весь по "
  f"рынку {rub[1.0]:+.1f} ₽ при цене {price_mk} ₽")
out["governance"] = {"treasury_share": r4(share_t), "sale_discount": {k: r4(x) for k, x in disc_vs.items()},
                     "scenarios": [{"name": n, "p": p, "d": dd} for n, p, dd in scen],
                     "treasury_sale_price_k": k_book, "k_extremes_rub": {str(kk): r4(x) for kk, x in rub.items()},
                     "liquidity": liq, "opacity": opq, "sum": r4(g_sum), "book": g_book, "axis": [0.0, g_hi],
                     "magnit": MAG["valuation"]["governance_discount"]}

# ------------------------------------------------------------------ печать и полоса
R.h("4. Шаг печати, полоса, обратный DCF")
price = MK["price_2026_09_25"]["v"]
R(f"  шаг печати Магнита {MAG['valuation']['print_step']} ₽ при цене ≈1 560–1 640 ₽ (3,0–3,2 %); у X5 50 ₽ = "
  f"{50 / price:.1%} цены {price} ₽; 1 % EV ≈ 33 ₽ на акцию (X2 §5) → КНИГА print_step = 50")
R(f"  полоса: {MAG['valuation']['draws']} прогонов как у Магнита; зерно — дата книги X5 (у Магнита — "
  f"{MAG['valuation']['seed']}, дата его книги) → КНИГА draws 2000, seed 20260928; обратный DCF — первые 200 "
  f"прогонов (у Магнита median_draws 200)")
out["headline"] = {"print_step": 50, "draws": 2000, "seed": 20260928, "subsample": 200}

write_json("valuation_out.json", out)
R.save()
