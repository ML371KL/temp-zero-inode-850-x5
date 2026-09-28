"""Лист оборотного капитала, кассы и аренды X5 (A-W1…A-W4, предложение A-F3b).

A-W1 nwc_pct — оборотный капитал «всё, что проходит через операционный поток», к годовой
выручке на 31.12; A-W2 h1_excess_pct — излишек 30.06 над 31.12; A-W3 operating_cash_pct —
несвободная касса; A-W4 lease_adj_pct — поправка FCFF на денежную аренду; financing.
cash_buffer_pct — финансовая подушка (предложение). Читает только inputs/.

    python -B wc_cash_lease.py
"""
from __future__ import annotations

from common import PRIM, Report, bal, hy, an, r6

R = Report()
OL = PRIM["other_st_liabilities"]
FP = PRIM["former_parent_liability"]
DATES = ["2023-12-31", "2024-12-31", "2025-06-30", "2025-12-31", "2026-06-30"]
# операционная часть «Резервов и прочих обязательств»: всё, кроме дивидендов (финансирование),
# кредиторки за ОС/НМА/бизнесы (capex — денежный), обязательств по выкупу НДУ и налоговых
# резервов (строки моста) и обязательства перед бывшей материнской компанией (выкуп акций)
OPER = ["other_payables_accruals", "payables_lessors", "personnel", "other_taxes",
        "advances_received", "other_nonfin"]
# обязательство перед X5 Retail Group N.V. в краткосрочной части (оценка): 31.12.2024 — вся
# выплата 2 кв. 2025; 31.12.2025 — выплата 2 кв. 2026 за вычетом долгосрочной части
FORMER_ST = {"2024-12-31": FP["paid_2025H1"],
             "2025-12-31": FP["paid_2026H1"] - FP["lt_nonfin_2025_12_31"]}


def nwc_op(d: str) -> dict:
    inv = bal(d, "inventories")
    rec = bal(d, "trade_other_receivables_advances")
    vat = bal(d, "vat_other_taxes_receivable")
    tp = bal(d, "trade_payables")
    cl = bal(d, "contract_liabilities_st")
    oth = sum(OL[d][k] for k in OPER) - FORMER_ST.get(d, 0.0)
    check = OL[d]["total"] - bal(d, "provisions_other_liabilities")
    val = inv + rec + vat - tp - cl - oth
    rev = bal(d, "revenue_ltm")
    return {"inventories": inv, "receivables": rec, "vat_receivable": vat, "trade_payables": tp,
            "contract_liabilities": cl, "other_operating_liabilities": oth,
            "former_parent_excluded": FORMER_ST.get(d, 0.0), "nwc_op": val, "revenue_ltm": rev,
            "pct": val / rev, "trade_pct": (inv + rec - tp) / rev, "note_vs_bs": check}


# ================================================================ 1. определение и уровни
R.h("1. A-W1. Оборотный капитал в определении книги (NWC_op), млрд ₽ и % выручки LTM")
nw = {d: nwc_op(d) for d in DATES}
R.table(["дата", "запасы", "дебит.", "НДС к возм.", "торг. кред.", "обяз. по дог.",
         "прочие опер.", "NWC_op", "% LTM", "торговый %"],
        [[d, x["inventories"], x["receivables"], x["vat_receivable"], x["trade_payables"],
          x["contract_liabilities"], x["other_operating_liabilities"], x["nwc_op"],
          100 * x["pct"], 100 * x["trade_pct"]] for d, x in nw.items()])
R.p("Сверка «прочих» с балансом (сумма примечания − строка баланса):",
    {d: round(x["note_vs_bs"], 3) for d, x in nw.items()})
R.data["nwc_op"] = {d: {k: r6(v_) for k, v_ in x.items()} for d, x in nw.items()}

# ================================================================ 2. сверка с ОДДС
R.h("2. Сверка: −ΔNWC_op против изменения оборотного капитала в ОДДС (приток +)")
spans = {"2024": ("2023-12-31", "2024-12-31"), "2025H1": ("2024-12-31", "2025-06-30"),
         "2025H2": ("2025-06-30", "2025-12-31"), "2025": ("2024-12-31", "2025-12-31"),
         "2026H1": ("2025-12-31", "2026-06-30")}
rows, rec_out = [], {}
for p, (a, b) in spans.items():
    d_bs = -(nw[b]["nwc_op"] - nw[a]["nwc_op"])
    d_cf = an(p, "wc_change") if len(p) == 4 else hy(p, "wc_change")
    rev = an(p, "revenue") if len(p) == 4 else hy(p, "revenue")
    rows.append([p, d_bs, d_cf, d_bs - d_cf, 100 * (d_bs - d_cf) / rev])
    rec_out[p] = {"balance": r6(d_bs), "cash_flow": r6(d_cf), "gap_pct_rev": r6((d_bs - d_cf) / rev)}
R.table(["период", "−ΔNWC_op (баланс)", "ΔОК по ОДДС", "разница", "% выручки"], rows)
R.data["odds_reconciliation"] = rec_out

# ================================================================ 3. сезонность (A-W2)
R.h("3. A-W2. Излишек 30.06 над 31.12, п.п. выручки LTM")
# торговый NWC — длинный ряд (запасы + дебиторка − торговая кредиторка)
tr = {}
for y in range(2019, 2027):
    j = bal(f"{y}-06-30", "nwc_trade")
    if j is None:
        continue
    tr[y] = {"june": j / bal(f"{y}-06-30", "revenue_ltm")}
    for dd, key in ((f"{y - 1}-12-31", "prev_dec"), (f"{y}-12-31", "next_dec")):
        x = bal(dd, "nwc_trade")
        if x is not None:
            tr[y][key] = x / bal(dd, "revenue_ltm")
rows = []
nxt, prv = [], []
for y, t in tr.items():
    a = t["june"] - t["next_dec"] if "next_dec" in t else None
    b = t["june"] - t["prev_dec"] if "prev_dec" in t else None
    if a is not None:
        nxt.append(a)
    if b is not None:
        prv.append(b)
    rows.append([y, 100 * t["june"], 100 * a if a is not None else "н/р",
                 100 * b if b is not None else "н/р"])
R.table(["год", "торговый NWC июнь %", "июнь − след. декабрь", "июнь − пред. декабрь"], rows)
med = lambda xs: sorted(xs)[len(xs) // 2] if len(xs) % 2 else 0.5 * (sorted(xs)[len(xs) // 2 - 1] + sorted(xs)[len(xs) // 2])
R.p(f"Торговый NWC: июнь − след. декабрь среднее {100 * sum(nxt) / len(nxt):.2f} п.п., "
    f"медиана {100 * med(nxt):.2f} (n = {len(nxt)}); июнь − пред. декабрь среднее "
    f"{100 * sum(prv) / len(prv):.2f}, медиана {100 * med(prv):.2f} (n = {len(prv)})")
op_obs = {"2025: июнь − след. дек.": nw["2025-06-30"]["pct"] - nw["2025-12-31"]["pct"],
          "2025: июнь − пред. дек.": nw["2025-06-30"]["pct"] - nw["2024-12-31"]["pct"],
          "2026: июнь − пред. дек.": nw["2026-06-30"]["pct"] - nw["2025-12-31"]["pct"]}
R.p("NWC_op:", {k: round(100 * v_, 2) for k, v_ in op_obs.items()},
    f"среднее {100 * sum(op_obs.values()) / 3:.2f} п.п.")
all_obs = nxt + prv + list(op_obs.values())
h1x = sum(all_obs) / len(all_obs)
R.p(f"Все наблюдения (n = {len(all_obs)}): среднее {100 * h1x:.2f} п.п., медиана "
    f"{100 * med(all_obs):.2f}")
R.data["h1_excess"] = {"trade_next_dec": [r6(x) for x in nxt], "trade_prev_dec": [r6(x) for x in prv],
                       "nwc_op_obs": {k: r6(v_) for k, v_ in op_obs.items()},
                       "mean_all": r6(h1x), "median_all": r6(med(all_obs))}

# ================================================================ 4. уровень nwc_pct (A-W1)
R.h("4. A-W1. Уровень на 31.12 (декабрьский базис)")
dec = [nw[d]["pct"] for d in ("2023-12-31", "2024-12-31", "2025-12-31")]
dec_mean = sum(dec) / len(dec)
h1x_book = round(h1x, 3)
anchor_dec = nw["2026-06-30"]["pct"] - h1x_book
R.p(f"Декабри 2023–2025: {[round(100 * x, 2) for x in dec]} %, среднее {100 * dec_mean:.2f} %")
R.p(f"Якорь 30.06.2026 {100 * nw['2026-06-30']['pct']:.2f} % − сезонный излишек "
    f"{100 * h1x_book:.1f} п.п. = {100 * anchor_dec:.2f} % (ожидание 31.12.2026 по якорю)")
nwc_2026 = round(anchor_dec, 4)
four = dec + [anchor_dec]
nwc_lt = round(sum(four) / len(four), 3)
R.p(f"Путь: 2026 = ожидание по якорю {nwc_2026}; LT (с 2028) = среднее четырёх декабрьских "
    f"наблюдений (2023–2025 и ожидание 2026) {100 * sum(four) / len(four):.2f} % → {nwc_lt}")
R.data["nwc_pct"] = {"dec_2023_2025": [r6(x) for x in dec], "dec_mean": r6(dec_mean),
                     "anchor_dec_expect": r6(anchor_dec),
                     "path": {"2026": nwc_2026, "LT": nwc_lt, "LT_from": 2028},
                     "h1_excess_pct": h1x_book,
                     "anchor_fact_nwc_op": r6(nw["2026-06-30"]["nwc_op"])}

# ================================================================ 5. касса (A-W3) и подушка
R.h("5. A-W3. Операционная касса и финансовая подушка, % выручки LTM")
CN = PRIM["cash_notes"]
rows, d1s, d2s = [], [], []
cash_out = {}
for d, c in CN.items():
    if not d[0].isdigit():
        continue
    rev = bal(d, "revenue_ltm")
    div = OL[d]["dividends_payable"] if d in OL else 0.0
    liquid = c["total"] + c.get("st_investments", 0.0)
    free = liquid - div
    if "in_stores" in c:
        d1 = c["in_stores"] + c["in_transit"]
        d2 = d1 + c["current_rub"]
        d1s.append(d1 / rev)
        d2s.append(d2 / rev)
    else:
        d1, d2 = None, c["banks_transit_stores_rub"]
        d2s.append(d2 / rev)
    cash_out[d] = {"d1": d1, "d2": d2, "liquid": liquid, "dividends_payable": div,
                   "non_dividend_liquidity": free, "revenue_ltm": rev}
    rows.append([d, c["weekday"], c.get("in_stores", "н/р"), c.get("in_transit", "н/р"),
                 100 * d1 / rev if d1 else "н/р", 100 * d2 / rev, 100 * free / rev])
R.table(["дата", "день", "в кассах", "в пути", "D1 = кассы + путь %",
         "D2 = D1 + текущие ₽ %", "ликвидность без дивидендов %"], rows)
d1_mean = sum(d1s) / len(d1s)
float_min = CN["2023-12-31"]["current_rub"] / bal("2023-12-31", "revenue_ltm")
opc = round(d1_mean + float_min, 4)
R.p(f"D1 среднее {100 * d1_mean:.2f} % (n = {len(d1s)}); минимальный остаток текущих счетов в "
    f"рублях (31.12.2023, излишек тогда лежал в краткосрочных вкладах 116 млрд) "
    f"{100 * float_min:.2f} % → операционная касса {100 * opc:.2f} % выручки")
post = ["2025-06-30", "2025-12-31", "2026-06-30"]
free_mean = sum(cash_out[d]["non_dividend_liquidity"] / cash_out[d]["revenue_ltm"] for d in post) / 3
buf = round(free_mean - opc, 4)
R.p(f"Ликвидность без дивидендов к выплате после начала выплат по политике (30.06.2025–30.06.2026): "
    f"среднее {100 * free_mean:.2f} % → подушка сверх операционной кассы {100 * buf:.2f} % "
    f"(неиспользованные линии {CN['credit_lines_unused_2025_12_31']['v']} млрд ₽ на 31.12.2025)")
R.data["cash"] = {"by_date": {d: {k: (r6(v_) if isinstance(v_, float) else v_) for k, v_ in x.items()}
                              for d, x in cash_out.items()},
                  "d1_mean": r6(d1_mean), "float_min": r6(float_min), "operating_cash_pct": opc,
                  "non_dividend_mean": r6(free_mean), "cash_buffer_pct": buf}

# ================================================================ 6. аренда (A-W4)
R.h("6. A-W4. Денежная аренда против расхода IAS 17 (фиксированная аренда сверки EBITDA)")
rows, lease = [], {}
periods = [("2023H2", "hy"), ("2024H1", "hy"), ("2024H2", "hy"), ("2025H1", "hy"),
           ("2025H2", "hy"), ("2026H1", "hy"), ("2024", "an"), ("2025", "an")]
for p, kind in periods:
    g = hy if kind == "hy" else an
    fixed = -g(p, "recon_fixed_rent")
    cash = -(g(p, "lease_principal_paid_ifrs16") + g(p, "lease_interest_paid"))
    rev = g(p, "revenue")
    adj = (fixed - cash) / rev                  # поправка к FCFF: + расход IAS 17 − денежная
    lease[p] = {"ias17_fixed": fixed, "cash": cash, "adj_pct": adj}
    rows.append([p, fixed, cash, fixed - cash, 100 * adj])
R.table(["период", "аренда IAS 17", "денежная (тело + %)", "разница", "% выручки"], rows)
hs = ["2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]
w_adj = sum(lease[p]["ias17_fixed"] - lease[p]["cash"] for p in hs) / sum(hy(p, "revenue") for p in hs)
R.p(f"2П2023–1П2026 взвешенно: {100 * w_adj:.3f} % выручки; диапазон полугодий "
    f"{100 * min(lease[p]['adj_pct'] for p in hs):.3f} … {100 * max(lease[p]['adj_pct'] for p in hs):.3f} %")
R.data["lease_adj"] = {"by_period": {p: {k: r6(v_) for k, v_ in x.items()} for p, x in lease.items()},
                       "weighted_2023H2_2026H1": r6(w_adj), "book": round(w_adj, 4),
                       "range": [round(min(lease[p]["adj_pct"] for p in hs), 4),
                                 round(max(lease[p]["adj_pct"] for p in hs), 4)]}

R.save("wc_cash_lease")
print("\n".join(R.lines))
