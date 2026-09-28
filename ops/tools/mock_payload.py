"""Мок выпуска x5-v1 для проверки витрины, пока ядро не выпустило настоящий.

Пишет tests/fixtures/sample_payload.json строго по docs/PAYLOAD.md: все блоки
REQUIRED_TOP_LEVEL и все поля. Числа мока НИЧЕГО не значат: сетка, слои,
прогоны и путь по годам — правдоподобная иллюстрация (цена ≈1 800 ₽), чтобы
проверить вёрстку. Там, где под рукой первичка (история X5, облигации, аналоги,
цели инвестдомов, дивиденды, миры книги), взяты её числа — так графики похожи
на настоящие. Самосогласованы только правила витрины: заголовок, полосы,
P(ниже рынка) и таблица by_lambda посчитаны из тех же прогонов, что лежат в
выпуске (квантиль тип 7, печать половиной к чётному), — это проверка ползунка λ.

    python ops/tools/mock_payload.py [выход.json]

Входы: data/assumptions/assumptions.draft.yaml (миры, режимы, capex) и
исследования в ../x5-850-handoff/research (если их нет — встроенные числа).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT.parent / "x5-850-handoff" / "research"
OUT = ROOT / "tests" / "fixtures" / "sample_payload.json"

REQUIRED_TOP_LEVEL = ["schema", "meta", "market", "headline", "fair_value", "layers", "grid", "worlds",
                      "regimes", "capex_levels", "paths", "debt", "dividends", "history", "reverse_dcf",
                      "judgements", "uncertainty", "next_report", "journal", "calendar", "checks", "inputs",
                      "live", "changes", "book", "indicators"]

SHARES = 245.98118      # млн акций в обращении на 30.06.2026 (research/facts/shares.json)
GOV = 0.05
PRICE = 1801.0          # ₽, ISS TQBR 28.09.2026 ≈11:38 (research/X4-market.md)
BOOK_PRICE = 1808.5
CLAIMS = 371.0          # млрд ₽, иллюстрация
STEP = 50
DRAWS = 2000
LAMBDA = 0.5


def r(x, k=4):
    return None if x is None else round(float(x), k)


def half_even(x):
    return round(x)


def printed(x):
    return half_even(x / STEP) * STEP


def q7(sorted_vals, q):
    n = len(sorted_vals)
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def load_json(name):
    path = HANDOFF / "facts" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def price_of(equity):
    return equity * (1 - GOV) * 1000 / SHARES if equity > 0 else equity * 1000 / SHARES


def main(out: Path) -> None:
    rng = random.Random(20260928)
    book = yaml.safe_load((ROOT / "data" / "assumptions" / "assumptions.draft.yaml").read_text(encoding="utf-8"))
    joint = book["joint"]
    worlds_book = book["worlds"]
    W = ["N", "H", "M"]
    REG = ["stress", "floor", "partial", "full"]
    CAP = ["low", "base", "high"]
    p_reg = joint["regime_prob"]
    p_cap = joint["capex_prob_given_regime"]
    layer_w = {"analytical": joint["world_prob"], "market_implied": joint["market_implied_prob"],
               "macro_neutral": {"N": 0.0, "H": 0.0, "M": 1.0}}

    # ── сетка (иллюстрация) ──
    fW = {"N": 1.10, "H": 1.00, "M": 0.80}
    fR = {"stress": 0.84, "floor": 0.97, "partial": 1.08, "full": 1.22}
    fC = {"low": 1.05, "base": 1.00, "high": 0.94}
    lt = {k: book["margin"]["targets"][k]["LT"] for k in REG}
    cells = []
    for w in W:
        for g in REG:
            for c in CAP:
                ev = 925.0 * fW[w] * fR[g] * fC[c]
                eq = ev - CLAIMS
                pr = p_reg[g] * p_cap[g][c]
                cells.append({
                    "world": w, "regime": g, "capex": c,
                    "p_analytical": r(layer_w["analytical"][w] * pr, 6),
                    "p_market_implied": r(layer_w["market_implied"][w] * pr, 6),
                    "p_neutral": r(layer_w["macro_neutral"][w] * pr, 6),
                    "ev": r(ev, 2), "d": CLAIMS, "equity": r(eq, 2), "price": r(price_of(eq), 1),
                    "margin_lt": lt[g], "ev_ebitda_fwd": r(ev / 300.0, 2),
                    "terminal_share": r(0.42 + 0.05 * (fW[w] - 1) + 0.04 * (fR[g] - 1), 3),
                    "max_leverage": r(1.45 - 0.5 * (fR[g] - 1), 2),
                })

    def layer(name):
        wts = layer_w[name]
        v0 = sum(cell["ev"] * wts[cell["world"]] * p_reg[cell["regime"]] * p_cap[cell["regime"]][cell["capex"]] for cell in cells)
        eq = v0 - CLAIMS
        pv_terminal = v0 * 0.44
        pv_shield = v0 * 0.035
        titles = {"analytical": "Свой макро-взгляд", "market_implied": "Веса, вменённые рынком", "macro_neutral": "Рыночные ставки как есть"}
        return {"title": titles[name], "world_weights": wts, "v0": r(v0, 2), "d": CLAIMS, "equity": r(eq, 2),
                "price": r(price_of(eq), 1), "pv_fcff": r(v0 - pv_terminal - pv_shield, 2), "pv_shield": r(pv_shield, 2),
                "pv_terminal": r(pv_terminal, 2), "terminal_share": r(pv_terminal / v0, 3), "ev_ebitda_fwd": r(v0 / 300.0, 2),
                "v0_to_d": r(v0 / CLAIMS, 2)}

    layers = {k: layer(k) for k in ("analytical", "market_implied", "macro_neutral")}
    low, high = layers["macro_neutral"]["price"], layers["analytical"]["price"]
    central = low + LAMBDA * (high - low)

    # ── прогоны по суждениям ──
    draws_low, draws_high = [], []
    for _ in range(DRAWS):
        shock = math.exp(rng.gauss(-0.02, 0.30) + 0.08 * rng.gauss(0, 1) ** 2 - 0.08)
        lo_i = low * shock * (1 + 0.04 * rng.gauss(0, 1))
        hi_i = high * shock * (1 + 0.04 * rng.gauss(0, 1))
        draws_low.append(round(max(lo_i, 1.0), 1))
        draws_high.append(round(max(hi_i, 1.0), 1))

    def stats(lam):
        c = sorted(a + lam * (b - a) for a, b in zip(draws_low, draws_high))
        return {"lambda": r(lam, 2), "point": r(low + lam * (high - low), 1), "median": r(q7(c, 0.5), 1),
                "p10": r(q7(c, 0.1), 1), "p25": r(q7(c, 0.25), 1), "p75": r(q7(c, 0.75), 1), "p90": r(q7(c, 0.9), 1),
                "mean": r(sum(c) / len(c), 1), "p_below": r(sum(1 for v in c if v < PRICE) / len(c), 4)}

    by_lambda = [stats(i / 20) for i in range(21)]
    s = stats(LAMBDA)
    median = s["median"]
    headline = {
        "central": median, "printed_central": printed(median),
        "band": [s["p10"], s["p90"]], "printed_band": [printed(s["p10"]), printed(s["p90"])],
        "inner": [s["p25"], s["p75"]], "printed_inner": [printed(s["p25"]), printed(s["p75"])],
        "mean": s["mean"], "p_central_below_market": s["p_below"], "market_price": PRICE,
        "print_step": STEP, "draws": DRAWS, "lambda": LAMBDA,
    }
    v_star = PRICE * SHARES / 1000 / (1 - GOV) + CLAIMS
    v0_med = median * SHARES / 1000 / (1 - GOV) + CLAIMS
    v0_pt = central * SHARES / 1000 / (1 - GOV) + CLAIMS
    fair_value = {
        "low": r(low, 1), "central": r(central, 1), "high": r(high, 1),
        "printed": {"low": printed(low), "central": printed(central), "high": printed(high)},
        "lambda": LAMBDA, "lambda_step": 0.05, "rates_view": {"rub": r(high - low, 1)},
        "by_lambda": by_lambda, "draws_low": draws_low, "draws_high": draws_high,
        "center_ev": {"v0_median": r(v0_med, 2), "v0_point": r(v0_pt, 2), "v_star": r(v_star, 2),
                      "gap_median": r(v0_med / v_star - 1, 4), "gap_point": r(v0_pt / v_star - 1, 4),
                      "rub_per_1pct_ev_median": r(0.01 * v0_med * (1 - GOV) * 1000 / SHARES, 1),
                      "rub_per_1pct_ev_point": r(0.01 * v0_pt * (1 - GOV) * 1000 / SHARES, 1)},
        "equity_share_of_ev": r((v0_pt - CLAIMS) / v0_pt, 4),
    }

    # ── миры (книга: общая макро-основа с Магнитом) ──
    def annual(path):
        years = {}
        for key, val in path.items():
            years.setdefault(int(str(key)[:4]), []).append(val)
        return [{"year": y, "value": r(sum(v) / len(v), 4)} for y, v in sorted(years.items())]

    worlds = {}
    for w in W:
        wb = worlds_book[w]
        r_term = wb["zero_curve"]["LT"] + 0.60 * 0.0557
        eq_w = [cell for cell in cells if cell["world"] == w]
        v0_w = sum(cell["ev"] * p_reg[cell["regime"]] * p_cap[cell["regime"]][cell["capex"]] for cell in eq_w)
        worlds[w] = {"name": wb["name"],
                     "weights": {k: layer_w[k][w] for k in layer_w},
                     "key_rate": annual(wb["key_rate"]), "cpi": annual(wb["cpi"]), "food_cpi": annual(wb["food_cpi"]),
                     "zero_curve": {str(k): v for k, v in wb["zero_curve"].items()},
                     "lt_inflation": wb["lt"]["inflation"], "r_terminal": r(r_term, 4),
                     "real_terminal": r((1 + r_term) / (1 + wb["lt"]["inflation"]) - 1, 4),
                     "price": r(price_of(v0_w - CLAIMS), 1), "v0": r(v0_w, 2)}
    worlds["source"] = worlds_book.get("source")

    # ── история X5 (первичка через research/facts, иначе встроенные числа) ──
    old_adj = {2011: (454.2, 0.0705), 2012: (491.1, 0.0714), 2013: (534.6, 0.0717), 2014: (633.9, 0.0732),
               2015: (808.8, 0.0735), 2016: (1033.7, 0.0769), 2017: (1295.0, 0.0765)}   # старый databook, лист EBITDA
    old_rep = {2011: 0.0731, 2012: 0.0712, 2013: 0.0717, 2014: 0.0723, 2015: 0.0683, 2016: 0.0738, 2017: 0.0743}
    ha = load_json("history_annual.json")
    hh = load_json("history_halfyear.json")
    ob = load_json("operating_by_format.json")
    annual_hist = []
    prev_rev = None
    for y in range(2011, 2026):
        if y in old_adj:
            rev, adj = old_adj[y]
            row = {"year": y, "revenue": rev, "adj_margin": adj, "rep_margin": old_rep[y], "capex_pct": None, "da_pct": None}
        else:
            v = ha["data"][f"FY{y}"] if ha else {}
            g = lambda k: (v.get(k) or {}).get("v")
            rev = (g("revenue") or 0) / 1000
            row = {"year": y, "revenue": r(rev, 1), "adj_margin": r(g("adj_ebitda_pct_rev"), 4), "rep_margin": r(g("ebitda_pct_rev"), 4),
                   "capex_pct": r(g("capex_pct_rev"), 4), "da_pct": r(g("da_ias17_pct_rev"), 4)}
        op = ((ob or {}).get("data", {}).get("annual", {}).get(f"FY{y}", {}) or {}).get("as_reported", {})
        gv = lambda k: (op.get(k) or {}).get("v")
        row.update({"growth": r(row["revenue"] / prev_rev - 1, 4) if prev_rev else None,
                    "leverage": {2021: 1.46, 2022: 1.12, 2023: 0.99, 2024: 0.81, 2025: 0.84}.get(y),
                    "lfl": gv("lfl_sales_total"), "lfl_traffic": gv("lfl_traffic_total"), "lfl_ticket": gv("lfl_ticket_total"),
                    "area_end": r(gv("space_total"), 0), "stores_end": r(gv("stores_total"), 0)})
        prev_rev = row["revenue"]
        annual_hist.append(row)
    halves_hist = []
    for p, v in (hh or {}).get("data", {}).items():
        rev = v["revenue"]["v"] / 1000
        halves_hist.append({"period": p, "revenue": r(rev, 1), "growth": None,
                            "adj_margin": r((v.get("adj_ebitda_pct_rev") or {}).get("v"), 4),
                            "rep_margin": r((v.get("ebitda_pct_rev") or {}).get("v"), 4),
                            "capex_pct": r((v.get("capex_pct_rev") or {}).get("v"), 4)})
    by_p = {h["period"]: h for h in halves_hist}
    for h in halves_hist:
        y, k = int(h["period"][:4]), h["period"][4:]
        prev = by_p.get(f"{y - 1}{k}")
        h["growth"] = r(h["revenue"] / prev["revenue"] - 1, 4) if prev else None
    formats = []
    for y in range(2017, 2026):
        op = ((ob or {}).get("data", {}).get("annual", {}).get(f"FY{y}", {}) or {}).get("as_reported", {})
        gv = lambda k: ((op.get(k) or {}).get("v") or 0) / 1000
        if not op:
            continue
        p5, pk, ch, tot = gv("nrs_pyaterochka"), gv("nrs_perekrestok"), gv("nrs_chizhik"), gv("revenue_total")
        formats.append({"year": y, "pyaterochka": r(p5, 1), "perekrestok": r(pk, 1), "chizhik": r(ch, 1) if ch else None,
                        "digital": None, "other": r(tot - p5 - pk - ch, 1)})
    format_area = []
    for y in range(2017, 2026):
        op = ((ob or {}).get("data", {}).get("annual", {}).get(f"FY{y}", {}) or {}).get("as_reported", {})
        gv = lambda k: (op.get(k) or {}).get("v")
        if op:
            format_area.append({"year": y, "pyaterochka": gv("space_pyaterochka"), "perekrestok": gv("space_perekrestok"),
                                "chizhik": gv("space_chizhik"), "digital": None, "other": None})

    # ── режимы маржи ──
    titles = {"stress": "Стресс", "floor": "Пол", "partial": "Частичный возврат", "full": "Полный возврат"}
    regimes = {}
    for g in REG:
        t = book["margin"]["targets"][g]
        target = [{"period": str(k), "value": v} for k, v in t.items() if k != "LT_from"]
        regimes[g] = {"title": titles[g], "target": target, "lt": t["LT"], "prior": p_reg[g], "posterior": p_reg[g],
                      "demand": joint["regime_demand"][g]}
    regimes["history"] = [{"period": h["period"], "adj_margin": h["adj_margin"], "rep_margin": h["rep_margin"]} for h in halves_hist]
    regimes["annual_history"] = [{"year": a["year"], "adj_margin": a["adj_margin"]} for a in annual_hist]
    regimes["expected_lt"] = r(sum(p_reg[g] * lt[g] for g in REG), 5)
    regimes["update"] = {"sigma_pp": joint["regime_update"]["sigma_pp"], "rho": joint["regime_update"]["rho"],
                         "cap_pp": joint["regime_update"]["cap_pp"], "observations": []}

    # ── capex ──
    capex_levels = {}
    for c in CAP:
        path = book["capex"]["maintenance"][c]
        first, y27, ltv = path["2026H2"], path["2027"], path["LT"]
        vals = {2026: first, 2027: y27}
        for y in range(2028, 2037):
            vals[y] = ltv if y >= path["LT_from"] else y27 + (ltv - y27) * (y - 2027) / (path["LT_from"] - 2027)
        capex_levels[c] = {"maintenance": [{"year": y, "value": r(v, 4)} for y, v in vals.items()], "lt": ltv,
                           "p_given_regime": {g: p_cap[g][c] for g in REG}}
    capex_levels["history"] = [{"year": a["year"], "capex_pct": a["capex_pct"], "da_pct": a["da_pct"]} for a in annual_hist if a["capex_pct"]]
    capex_levels.update({"price_per_m2": book["capex"]["price_per_m2"], "infra_per_m2": book["capex"]["infra_per_m2"],
                         "maintenance_area_share": book["capex"]["maintenance_area_share"]})

    # ── путь «своего взгляда» (иллюстрация) ──
    rows = []
    rev_prev, nd = 4642.0, 310.6
    area = 12157.0
    growth = [0.115, 0.10, 0.09, 0.08, 0.072, 0.066, 0.062, 0.059, 0.057, 0.056, 0.055]
    margins = [0.0598, 0.0600, 0.0602, 0.0604, 0.0606, 0.0607, 0.0607, 0.0607, 0.0607, 0.0607, 0.0607]
    for i, y in enumerate(range(2026, 2037)):
        rev = rev_prev * (1 + growth[i])
        m = margins[i]
        ebitda = rev * m
        lti = rev * 0.0018
        da = rev * (0.026 + 0.0003 * i)
        capex_pct = 0.046 - 0.0012 * i if i < 6 else 0.039
        capex = rev * capex_pct
        maint = rev * 0.026
        infra = rev * 0.003
        grow = capex - maint - infra
        nwc = -0.012 * (rev - rev_prev)
        tax = 0.25 * (ebitda - lti - da + 0.001 * rev)
        fcff = ebitda - lti - tax - capex - nwc
        interest = nd * (0.150 - 0.004 * i)
        shield = 0.25 * interest
        nd_pre = nd - (fcff + shield - interest)
        div = max(0.0, 1.3 * (ebitda - lti) - nd_pre)
        nd = nd_pre + div
        area_g = [0.07, 0.055, 0.05, 0.04, 0.035, 0.028, 0.02, 0.015, 0.01, 0.005, 0.0][i]
        area *= 1 + area_g
        rows.append({"year": y, "revenue": r(rev, 1), "revenue_growth": r(growth[i], 4),
                     "ticket": r(growth[i] - area_g * 0.9 - 0.002, 4), "traffic": r(-0.002 + 0.0005 * i, 4),
                     "area_end": r(area, 0), "area_growth": r(area_g, 4), "margin": m, "adj_ebitda": r(ebitda, 1),
                     "lti": r(lti, 2), "da": r(da, 1), "capex": r(capex, 1), "capex_maintenance": r(maint, 1),
                     "capex_growth": r(grow, 1), "capex_infra": r(infra, 1), "capex_pct": r(capex_pct, 4),
                     "nwc_change": r(nwc, 1), "tax_unlevered": r(tax, 1), "fcff": r(fcff, 1), "shield": r(shield, 1),
                     "interest": r(interest, 1), "dividends": r(div, 1), "net_debt": r(nd, 1), "leverage": r(nd / (ebitda - lti), 2)})
        rev_prev = rev
    halves = []
    for a in rows:
        for k, share in (("H1", 0.48), ("H2", 0.52)):
            halves.append({"period": f"{a['year']}{k}", "revenue": r(a["revenue"] * share, 1),
                           "margin": r(a["margin"] + (-0.0025 if k == "H1" else 0.0023), 4),
                           "adj_ebitda": r(a["adj_ebitda"] * share, 1), "capex": r(a["capex"] * (0.45 if k == "H1" else 0.55), 1),
                           "fcff": r(a["fcff"] * (0.35 if k == "H1" else 0.65), 1), "net_debt": a["net_debt"]})
    halves[0].update({"revenue": 2480.5, "margin": 0.0570})
    paths = {"annual": rows, "halves": halves, "fact_marks": {"annual": [2026], "halves": ["2026H1"]}}

    # ── долг ──
    dr = load_json("debt_register.json") or {}
    rep = dr.get("reported_2026_06_30", {})
    bonds = []
    for b in dr.get("bonds", []):
        if b.get("status") != "live":
            continue
        mkt = b.get("market_2026_09_28") or {}
        cp = b.get("coupon") or {}
        fixed = b.get("coupon_type") != "floating"
        bonds.append({"isin": b["isin"], "name": f"X5 {b['series']}", "outstanding": b.get("outstanding_2026_09_28_rub_bn"),
                      "coupon_type": "fixed" if fixed else "floating",
                      "coupon": r((cp.get("rate_pct") or b.get("coupon_now_pct_est") or 0) / 100, 4) if fixed else None,
                      "spread": None if fixed else r((cp.get("spread_pp") or 0) / 100, 4),
                      "put_date": b.get("next_put_or_offer"), "maturity": b.get("legal_maturity"),
                      "price": mkt.get("price_last_pct"), "ytm": r((mkt.get("yield_eff_pct") or 0) / 100, 4), "as_of": "2026-09-28"})
    sched = (dr.get("derived") or {}).get("bonds_repayment_schedule_by_quarter_2026_09_28") or {}
    banks_q = {"2026Q4": 14.0, "2027Q1": 14.0, "2027Q2": 13.9, "2027Q3": 20.0, "2027Q4": 40.0, "2028Q1": 30.0, "2028Q2": 30.0, "2028Q3": 25.0, "2028Q4": 28.5}
    wall = [{"period": q, "bonds": sched.get(q, 0.0), "banks": banks_q.get(q, 0.0)} for q in sorted(set(sched) | set(banks_q))]
    bridge_lines = [
        {"key": "accrued_interest", "label": "Начисленные проценты", "amount": 1.719, "included": True, "src": "МСФО 1П2026, прим. 15, с. 20"},
        {"key": "nci_put", "label": "Пут на неконтролирующую долю", "amount": 0.698, "included": True, "src": "МСФО 1П2026, прим. 27, с. 26"},
        {"key": "lti_liability", "label": "Обязательство по долгосрочной мотивации", "amount": 3.1, "included": True, "src": "иллюстрация мока"},
        {"key": "tax_provisions_net", "label": "Резервы по налоговым позициям (нетто)", "amount": 1.4, "included": True, "src": "иллюстрация мока"},
        {"key": "st_investments", "label": "Краткосрочные финансовые вложения", "amount": -0.204, "included": True, "src": "МСФО 1П2026, баланс, с. 5"},
        {"key": "associates", "label": "Инвестиции в ассоциированные компании", "amount": -0.9, "included": False, "src": "иллюстрация мока"},
    ]
    lines_sum = sum(x["amount"] for x in bridge_lines if x["included"])
    at_val = [
        {"key": "net_debt", "label": "Чистый долг на 30.06.2026", "amount": 310.647},
        {"key": "operating_cash", "label": "Операционная касса", "amount": 19.5},
        {"key": "bridge_lines", "label": "Строки моста из отчётности", "amount": r(lines_sum, 3)},
        {"key": "dividends", "label": "Дивиденды к выплате (отсечка 07.07.2026)", "amount": 60.269},
    ]
    at_val.append({"key": "roll", "label": "Денежный поток с 01.07.2026 по дату оценки", "amount": r(CLAIMS - sum(x["amount"] for x in at_val), 3)})
    debt = {
        "anchor": {"as_of": "2026-06-30", "total_debt": rep.get("total_debt_company_definition_incl_leasing", 435.857),
                   "cash": rep.get("cash_and_equivalents", 125.21), "net_debt": rep.get("net_debt_company_definition", 310.647),
                   "leverage": rep.get("net_debt_to_ebitda_pre_ifrs16", 1.08), "leasing": 1.148,
                   "lease_liabilities_ifrs16": rep.get("lease_liabilities_ifrs16", 742.991),
                   "credit_lines_unused": rep.get("undrawn_credit_lines", 804.643), "floating_share": 0.55, "effective_rate": 0.1654,
                   "ratings": [{"agency": "АКРА", "rating": "AAA(RU)", "outlook": "стабильный", "date": None},
                               {"agency": "Эксперт РА", "rating": "ruAAA", "outlook": "стабильный", "date": None}]},
        "bridge": {"lines": bridge_lines, "rows_at_valuation": at_val, "total": CLAIMS},
        "bonds": bonds,
        "bank_loans": {"short": 41.913, "long": 173.501, "total": 215.414},
        "wall": wall,
    }

    # ── дивиденды ──
    dividends = {
        "policy": {"target_leverage": [1.2, 1.4], "no_pay_above": 2.0, "frequency": "дважды в год",
                   "text": "Распределяется свободный денежный поток при чистом долге 1,2–1,4× EBITDA до МСФО 16 на конец года выплаты"},
        "register": [
            {"id": "FY2024", "label": "За 2024 год", "dps": 648.0, "amount": 158.848, "record_date": "2025-07-09", "ex_date": "2025-07-09",
             "pay_until": "2025-08-13", "status": "paid", "paid_share": 0.9994, "in_claims": False},
            {"id": "9M2025", "label": "За 9 месяцев 2025 года", "dps": 368.0, "amount": 90.21, "record_date": "2026-01-06", "ex_date": "2026-01-06",
             "pay_until": "2026-02-13", "status": "paid", "paid_share": 0.9993, "in_claims": False},
            {"id": "FY2025-final", "label": "Финальный за 2025 год", "dps": 245.0, "amount": 60.269, "record_date": "2026-07-07", "ex_date": "2026-07-07",
             "pay_until": "2026-08-11", "status": "paid", "paid_share": 0.761, "in_claims": True},
        ],
        "history": [{"period": "2024 год", "dps": 648.0, "amount": 158.848, "record_date": "2025-07-09"},
                    {"period": "9М 2025", "dps": 368.0, "amount": 90.21, "record_date": "2026-01-06"},
                    {"period": "2025 год", "dps": 245.0, "amount": 60.269, "record_date": "2026-07-07"}],
        "model": [{"year": a["year"], "amount": a["dividends"], "dps": r(a["dividends"] * 1000 / SHARES, 0)} for a in rows],
        "next_expected": {"label": "За 9 месяцев 2026 года", "record_date_est": "2027-01-06", "dps_model": 190.0,
                          "note": "результаты 9 месяцев — 29.10.2026, рекомендация совета — в ноябре"},
        "yield_ltm": r(613.0 / PRICE, 4),
    }

    # ── рынок ──
    closes = []
    csv_path = HANDOFF / "market" / "x5_daily.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as fh:
            all_rows = [row for row in csv.DictReader(fh) if row["tradedate"] >= "2025-09-26"]
        for i, row in enumerate(all_rows):
            if i % 3 == 0 or i == len(all_rows) - 1 or row.get("div_ex_rub"):
                closes.append({"date": row["tradedate"], "close": float(row["legalcloseprice"] or row["close"])})
    closes.append({"date": "2026-09-28", "close": PRICE})
    peers = [
        {"ticker": "X5", "name": "X5", "price": PRICE, "price_date": "2026-09-28", "market_cap": r(PRICE * SHARES / 1000, 1), "net_debt": 370.9,
         "ev": r(PRICE * SHARES / 1000 + 370.9, 1), "ebitda_ltm": 287.0, "ev_ebitda": r((PRICE * SHARES / 1000 + 370.9) / 287.0, 2), "pe": 6.7,
         "basis": "чистый долг до МСФО 16 на 30.06.2026 плюс дивиденд, объявленный до даты баланса; EBITDA до МСФО 16 за 12 месяцев", "as_of": "2026-06-30"},
        {"ticker": "MGNT", "name": "Магнит", "price": 1640.0, "price_date": "2026-09-25", "market_cap": 111.3, "net_debt": 518.1, "ev": 629.4,
         "ebitda_ltm": 179.6, "ev_ebitda": 3.50, "pe": None, "basis": "чистый долг до МСФО 16 на 30.06.2026; прибыль за 12 месяцев отрицательная", "as_of": "2026-06-30"},
        {"ticker": "LENT", "name": "Лента", "price": 1746.0, "price_date": "2026-09-25", "market_cap": 202.5, "net_debt": 117.4, "ev": 319.9,
         "ebitda_ltm": 84.4, "ev_ebitda": 3.79, "pe": 6.5, "basis": "О'КЕЙ консолидирован с 02.06.2026: долг целиком, EBITDA за месяц", "as_of": "2026-06-30"},
        {"ticker": "FIXR", "name": "Фикс Прайс", "price": 0.3887, "price_date": "2026-09-25", "market_cap": 38.9, "net_debt": 6.1, "ev": 45.0,
         "ebitda_ltm": 22.5, "ev_ebitda": 2.00, "pe": 3.9, "basis": "ПАО «Фикс Прайс», 100 млрд акций", "as_of": "2026-06-30"},
    ]
    brokers = [
        ("Финам", "2026-09-24", 2510, "покупать", "Финмаркет 6713775"),
        ("Эйлер", "2026-09-11", 2600, "покупать", "Финмаркет 6704753"),
        ("БКС Мир инвестиций", "2026-08-14", 2500, "позитивно", "Финмаркет 6685670"),
        ("Т-Инвестиции", "2026-08-13", 2250, "держать", "Финмаркет 6685040"),
    ]
    market = {
        "price": PRICE, "price_date": "2026-09-28", "price_time": "11:38", "price_source": "ISS TQBR", "price_status": "live", "book_price": BOOK_PRICE,
        "market_cap": r(PRICE * SHARES / 1000, 2), "claims": CLAIMS, "market_ev": r(v_star, 2), "ebitda_rep_ltm": 287.0,
        "adj_ebitda_ltm": 293.1, "ev_ebitda_ltm": r(v_star / 287.0, 2), "pe_ltm": 6.7, "dividend_yield_ltm": r(613.0 / PRICE, 4),
        "peers": {"rows": peers},
        "brokers": {"rows": [{"broker": b, "date": dt, "target": t, "rating": rt, "src": src} for b, dt, t, rt, src in brokers],
                    "median": statistics.median([b[2] for b in brokers]), "after_report": "2 кв. 2026"},
        "price_history": closes,
        "ex_dividend": [{"date": "2026-01-06", "dps": 368.0}, {"date": "2026-07-07", "dps": 245.0}],
    }

    # ── обратный DCF и суждения ──
    reverse_rows = []
    specs = [
        ("Долгосрочный уровень маржи (сдвиг целей всех режимов)", "п.п.", 0.0, 0.0021, [-0.005, 0.004], "solved", 0.0012),
        ("Поддерживающий capex (сдвиг всех уровней)", "п.п.", 0.0, -0.0031, [-0.004, 0.006], "solved", -0.0019),
        ("Бета активов", "", 0.60, 0.52, [0.45, 0.75], "solved", 0.55),
        ("Трафик сопоставимых магазинов (сдвиг)", "п.п.", 0.0, 0.0068, [-0.005, 0.005], "solved", 0.0041),
        ("Чистый рост площади (сдвиг всех тарифов)", "п.п.", 0.0, None, [-0.015, 0.01], "unreachable", None),
    ]
    for name, unit, bk, sol, rg, st, pt in specs:
        reverse_rows.append({"name": name, "unit": unit, "book": bk, "solved": sol, "delta": None if sol is None else r(sol - bk, 4),
                             "in_range": sol is not None and rg[0] <= sol <= rg[1], "range": rg, "status": st, "point_solved": pt})
    reverse_dcf = {"rows": reverse_rows, "target": PRICE, "method": "медиана на подвыборке 200 + сдвиг"}

    axes = book["valuation"]["uncertainty"]["axes"]
    units = ["pp", "pp", "number", "pct", "pct", "pp", "pp", "bn_per_m2", "number", "number", "pct", "pp", "pct", "pct", "times", "weights", "pp"]
    swings = [690, 520, 610, 380, 330, 250, 230, 190, 170, 120, 90, 80, 60, 55, 50, 140, 110]
    names = ["Долгосрочный уровень маржи", "Поддерживающий capex", "Бета активов", "Премия за риск акций", "Дисконт за управление",
             "Трафик сопоставимых магазинов", "Чистый рост площади", "Стоимость открытия", "Плотность новой площади",
             "Продуктивность закрываемой площади", "Операционная касса", "Оборотный капитал", "Постоянные налоговые разницы",
             "Долгосрочная мотивация", "Целевой рычаг", "Веса миров", "Инфляция мира «Рыночный как есть»"]
    shares_raw = [0.26, 0.17, 0.16, 0.08, 0.07, 0.05, 0.045, 0.03, 0.025, 0.015, 0.01, 0.01, 0.005, 0.005, 0.005, 0.03, 0.02]
    tot = sum(shares_raw)
    judg = []
    for i, ax in enumerate(axes):
        sign = -1 if i in (0, 5, 6, 8, 9) else 1
        bookv = 0.0 if ax["kind"] == "shift" else (joint["world_prob"] if ax["kind"] == "dict" else None)
        if bookv is None:
            path = ax["paths"][0].split(".")
            node = book
            for part in path:
                node = node[part]
            bookv = node
        pl = central - sign * swings[i] * 0.55
        ph = central + sign * swings[i] * 0.45
        judg.append({"id": f"A-{i + 1:02d}", "name": names[i], "unit": units[i], "book": bookv, "low": ax["low"], "high": ax["high"],
                     "price_low": r(pl, 1), "price_high": r(ph, 1), "swing": r(abs(ph - pl), 1), "share": r(shares_raw[i] / tot, 4)})
    judg.sort(key=lambda j: -j["swing"])
    judgements = {"rows": judg}
    uncertainty = {"contributions": [{"axis": names[i], "share": r(shares_raw[i] / tot, 4)} for i in range(len(axes))],
                   "draws": DRAWS, "seed": book["valuation"]["uncertainty"]["seed"], "axes_count": len(axes), "mean": s["mean"],
                   "histogram_bins": [i * 250.0 for i in range(0, 23)]}

    # ── ближайший отчёт ──
    demo = book["valuation"]["next_report"]["demo_values"]
    table = []
    neutral_m = 0.0607
    slope = 118.0   # ₽ медианы на 0,1 п.п.
    for m in demo:
        dm = (m - neutral_m) * 1000 * slope
        post = {"stress": max(0.02, 0.25 - (m - 0.06) * 25), "floor": 0.40, "partial": 0.30, "full": 0.05}
        tot_p = sum(post.values())
        table.append({"margin": m, "point": r(central + dm * 1.05, 1), "median": r(median + dm, 1), "d_point": r(dm * 1.05, 1),
                      "d_median": r(dm, 1), "posterior": {k: r(v / tot_p, 4) for k, v in post.items()}})
    events_nr = [
        {"date": "2026-10-16", "title": "Операционные результаты за 3 кв. 2026", "kind": "trading_update", "confirmed": False, "note": "по графику прошлых лет"},
        {"date": "2026-10-23", "title": "Заседание ЦБ по ключевой ставке", "kind": "cbr", "confirmed": True, "note": None},
        {"date": "2026-10-29", "title": "Финансовые результаты за 9 месяцев 2026", "kind": "ifrs", "confirmed": True, "note": "календарь закрытых периодов X5"},
        {"date": "2027-03-18", "title": "Финансовые результаты за 2026 год", "kind": "ifrs", "confirmed": False, "note": "факт маржи 2П 2026 войдёт в журнал"},
    ]
    next_report = {
        "period": "2026H2", "events": events_nr,
        "expectation": {"revenue_growth": 0.121, "revenue": 2688.6, "margin": 0.0612, "adj_ebitda": 164.5,
                        "by_regime": [{"regime": "stress", "margin": 0.0594}, {"regime": "floor", "margin": 0.0612},
                                      {"regime": "partial", "margin": 0.0621}, {"regime": "full", "margin": 0.0630}]},
        "guidance": {"revenue_growth": [0.12, 0.16], "margin_min": 0.06, "capex_pct": [0.045, 0.047], "openings": 2000,
                     "required_h2_margin": 0.0628, "required_h2_growth": 0.135,
                     "src": "прогноз X5 на 2026 год от 20.03.2026 (x5.ru), факт 1П 2026 — пресс-релиз 2 кв. 2026"},
        "benchmarks": [{"name": "то же полугодие год назад", "margin": 0.0648, "revenue_growth": 0.166, "note": "2П 2025"},
                       {"name": "среднее двух последних полугодий", "margin": 0.0609, "revenue_growth": 0.137, "note": "2П 2025 и 1П 2026"},
                       {"name": "как прошлое полугодие", "margin": 0.0570, "revenue_growth": 0.105, "note": "1П 2026"}],
        "table": table, "neutral": {"median": neutral_m, "point": 0.0604}, "rub_per_01pp": slope,
    }
    journal = {
        "entries": [
            {"id": "2026H2-x5.adj_margin-1", "target": "x5.adj_margin", "period": "2026H2", "recorded_at": "2026-09-28", "release_sha": "0" * 12,
             "forecast": 0.0612, "benchmarks": {"same_half_last_year": 0.0648, "mean_two_halves": 0.0609, "last_half": 0.0570},
             "actual": None, "errors": {"forecast": None, "benchmarks": {}}},
            {"id": "2026H2-x5.revenue_growth-1", "target": "x5.revenue_growth", "period": "2026H2", "recorded_at": "2026-09-28", "release_sha": "0" * 12,
             "forecast": 0.121, "benchmarks": {"same_half_last_year": 0.166, "mean_two_halves": 0.137, "last_half": 0.105},
             "actual": None, "errors": {"forecast": None, "benchmarks": {}}},
            {"id": "2026H1-x5.adj_margin-0", "target": "x5.adj_margin", "period": "2026H1", "recorded_at": "2026-03-20", "release_sha": "f" * 12,
             "forecast": 0.0584, "benchmarks": {"same_half_last_year": 0.0580, "mean_two_halves": 0.0614, "last_half": 0.0648},
             "actual": 0.0570, "errors": {"forecast": -0.0014, "benchmarks": {"same_half_last_year": -0.0010, "mean_two_halves": -0.0044, "last_half": -0.0078}}},
        ],
        "rule": "прогноз допускается к цене после четырёх отчётных полугодий вне выборки, если его средняя квадратичная ошибка не больше 0,8 лучшего эталона",
        "status": "копим зачёт: 1 из 4 полугодий",
    }
    calendar = {"events": events_nr[:3] + [
        {"date": "2026-11-13", "title": "Рекомендация дивиденда за 9 месяцев 2026", "kind": "dividend", "confirmed": False, "note": "в 2025 году — 13.11"},
        {"date": "2026-12-18", "title": "Заседание ЦБ по ключевой ставке", "kind": "cbr", "confirmed": True, "note": None},
        {"date": "2027-01-06", "title": "Ожидаемая отсечка дивиденда за 9 месяцев", "kind": "dividend", "confirmed": False, "note": None},
        events_nr[3],
    ]}

    checks = {
        "invariants": [{"name": "Вероятности клеток в сумме 1", "ok": True, "detail": None},
                       {"name": "FCFF из опубликованных строк", "ok": True, "detail": None},
                       {"name": "Путь долга", "ok": True, "detail": None},
                       {"name": "Все числа конечны", "ok": True, "detail": None},
                       {"name": "Размер выпуска", "ok": True, "detail": None}],
        "gates": [
            {"name": "terminal_share", "title": "Доля терминала вне коридора", "fired": True, "mass": 0.034,
             "explanation": "в мире «Нормализация» при полном возврате маржи терминал даёт больше 60 % EV: ставка далее 9 % при росте 5 %",
             "valid_until": "2026-12-31", "expected_mass": [0.0, 0.05]},
            {"name": "ev_ebitda", "title": "EV/EBITDA вне коридора", "fired": False, "mass": 0.0, "explanation": None, "valid_until": None, "expected_mass": None},
            {"name": "margin_range", "title": "Маржа вне коридора", "fired": False, "mass": 0.0, "explanation": None, "valid_until": None, "expected_mass": None},
            {"name": "capex_range", "title": "Capex вне коридора", "fired": False, "mass": 0.0, "explanation": None, "valid_until": None, "expected_mass": None},
            {"name": "real_rate", "title": "Реальная ставка вне коридора", "fired": False, "mass": 0.0, "explanation": None, "valid_until": None, "expected_mass": None},
            {"name": "leverage_path", "title": "Рычаг на пути выше предела", "fired": False, "mass": 0.0, "explanation": None, "valid_until": None, "expected_mass": None},
            {"name": "equity_cushion", "title": "Капитал клетки не положителен", "fired": False, "mass": 0.0, "explanation": None, "valid_until": None, "expected_mass": None},
        ],
        "flags": [
            {"name": "book_update", "title": "Книгу пора обновлять", "raised": False, "detail": "сдвиг кривой ОФЗ 5 лет +6 б.п., 10 лет +21 б.п. при пороге 50 б.п.; книге 0 дней"},
            {"name": "dividend_register", "title": "Нет объявленного дивиденда после ожидаемой отсечки", "raised": False, "detail": None},
            {"name": "price_fallback", "title": "Живая цена не принята", "raised": False, "detail": None},
        ],
    }
    inputs = {"rows": [
        {"name": "Цена акции", "value": PRICE, "unit": "rub", "as_of": "2026-09-28", "source": "ISS TQBR, последняя сделка 11:38", "status": "ok"},
        {"name": "Кривая ОФЗ", "value": "узлы 1, 3, 5, 10 лет", "unit": None, "as_of": "2026-09-25", "source": "ISS, бескупонная кривая", "status": "ok"},
        {"name": "Ключевая ставка", "value": 0.14, "unit": "pct", "as_of": "2026-07-27", "source": "Банк России", "status": "ok"},
        {"name": "Книга допущений", "value": "1.0", "unit": None, "as_of": "2026-09-28", "source": "data/assumptions", "status": "ok"},
        {"name": "Факты отчётности", "value": "1П 2026", "unit": None, "as_of": "2026-06-30", "source": "МСФО и databook X5", "status": "ok"},
    ]}
    live_nodes = {"1": 0.1318, "3": 0.1161, "5": 0.1067, "10": 0.1006}
    book_nodes = {k: worlds_book["N"]["zero_curve"][k] for k in ("1", "3", "5", "10")}   # кривая ОФЗ на дату книги (иллюстрация)
    live = {"price": {"value": PRICE, "date": "2026-09-28", "accepted": True, "reason": None},
            "curve": {"as_of": "2026-09-25", "nodes": live_nodes, "book_nodes": book_nodes,
                      "shift_bp": {"5": r((live_nodes["5"] - book_nodes["5"]) * 1e4, 0), "10": r((live_nodes["10"] - book_nodes["10"]) * 1e4, 0)}},
            "key_rate": {"value": 0.14, "date": "2026-07-27"}, "valuation_date": "2026-09-28"}
    changes = {"vs_previous": {"previous_sha": "a" * 64, "previous_generated_at": "2026-09-25T16:58:00Z",
                               "rows": [{"component": "valuation_date", "rub": 1.9}, {"component": "market_price", "rub": 0.0},
                                        {"component": "residual", "rub": -12.4}], "total_rub": -10.5}}
    book_block = {"version": "1.0", "date": "2026-09-28", "tag": "book-1.0",
                  "sections": [{"id": "network", "title": "Сеть и площадь"}, {"id": "revenue", "title": "Выручка: чек и трафик"},
                               {"id": "margin", "title": "Маржа и режимы"}, {"id": "capex", "title": "Capex и амортизация"},
                               {"id": "working_capital", "title": "Оборотный капитал и касса"}, {"id": "tax", "title": "Налог"},
                               {"id": "financing", "title": "Финансирование и дивиденды"}, {"id": "valuation", "title": "Оценка"},
                               {"id": "worlds", "title": "Миры ставок"}],
                  "worlds_source": "миры общие с моделью Магнита 850oa: книга 1.6, кривая 18.09.2026",
                  "facts_date": "2026-06-30",
                  "key_judgements": [
                      {"id": "A-P1", "name": "Вес мира «Нормализация» в своём взгляде", "value": 0.35, "unit": "pct"},
                      {"id": "A-P1c", "name": "Вес своего взгляда на ставки λ", "value": 0.5, "unit": "number"},
                      {"id": "A-C1", "name": "Маржа далее в режиме «Пол»", "value": 0.059, "unit": "pct"},
                      {"id": "A-K1", "name": "Поддерживающий capex далее, базовый уровень", "value": 0.025, "unit": "pct"},
                      {"id": "A-V1", "name": "Бета активов", "value": 0.60, "unit": "number"},
                      {"id": "A-V2", "name": "Премия за риск акций", "value": 0.0557, "unit": "pct"},
                      {"id": "A-V3", "name": "Дисконт за управление", "value": 0.05, "unit": "pct"},
                      {"id": "A-F5", "name": "Целевой чистый долг / EBITDA", "value": 1.3, "unit": "times"},
                      {"id": "A-K3", "name": "Стоимость открытия", "value": 0.055, "unit": "bn_per_m2"}]}

    def series(start, n, v0, drift, noise, end, step_days=7):
        # Случайное блуждание, сдвинутое так, чтобы последняя точка равнялась плитке.
        from datetime import date, timedelta
        d0 = date.fromisoformat(start)
        vals, v = [], v0
        for i in range(n):
            v = v + drift + rng.gauss(0, noise)
            vals.append(v)
        shift = end - vals[-1]
        return [{"date": (d0 + timedelta(days=step_days * i)).isoformat(), "value": r(x + shift * i / (n - 1), 5)} for i, x in enumerate(vals)]

    price_tiles = [{"date": c["date"], "value": c["close"]} for c in closes][-60:]
    indicators = {"tiles": [
        {"id": "x5.price", "title": "Акция X5", "unit": "rub", "value": PRICE, "date": "2026-09-28", "change": r(PRICE - 1808.5, 1), "history": price_tiles},
        {"id": "cbr.key_rate", "title": "Ключевая ставка", "unit": "pct", "value": 0.14, "date": "2026-07-27", "change": -0.005,
         "history": [{"date": "2026-01-01", "value": 0.16}, {"date": "2026-03-20", "value": 0.155}, {"date": "2026-04-24", "value": 0.15},
                     {"date": "2026-06-05", "value": 0.145}, {"date": "2026-07-27", "value": 0.14}, {"date": "2026-09-28", "value": 0.14}]},
        {"id": "ofz.5y", "title": "ОФЗ 5 лет, бескупонная", "unit": "pct", "value": 0.1067, "date": "2026-09-25", "change": 0.0006,
         "history": series("2026-04-03", 26, 0.1120, -0.0002, 0.0008, 0.1067)},
        {"id": "ofz.10y", "title": "ОФЗ 10 лет, бескупонная", "unit": "pct", "value": 0.1006, "date": "2026-09-25", "change": 0.0021,
         "history": series("2026-04-03", 26, 0.1050, -0.0002, 0.0007, 0.1006)},
        {"id": "x5.bond_spread", "title": "Спред облигаций X5 к ОФЗ", "unit": "bp", "value": 105.0, "date": "2026-09-25", "change": -4.0,
         "history": series("2026-04-03", 26, 120.0, -0.5, 4.0, 105.0)},
    ]}

    payload = {
        "schema": "x5-v1",
        "meta": {"generated_at": "2026-09-28T17:05:00Z", "valuation_date": "2026-09-28", "facts_date": "2026-06-30",
                 "book_version": "1.0", "book_date": "2026-09-28", "engine_commit": "0000000mock", "basis": "до МСФО 16",
                 "shares_mln": SHARES, "governance_discount": GOV, "anchor_period": "2026H1", "first_period": "2026H2",
                 "last_period": "2036H2", "open_period": "2026H2", "curve_as_of": "2026-09-18", "closed_periods": 0,
                 "elapsed": 0.4946, "payload_sha256": "", "bytes": 0, "previous_sha256": "a" * 64},
        "market": market, "headline": headline, "fair_value": fair_value, "layers": layers,
        "grid": {"cells": cells, "regime_order": REG, "capex_order": CAP, "world_order": W},
        "worlds": worlds, "regimes": regimes, "capex_levels": capex_levels, "paths": paths, "debt": debt,
        "dividends": dividends,
        "history": {"annual": annual_hist, "halves": halves_hist, "formats": formats, "format_area": format_area},
        "reverse_dcf": reverse_dcf, "judgements": judgements, "uncertainty": uncertainty, "next_report": next_report,
        "journal": journal, "calendar": calendar, "checks": checks, "inputs": inputs, "live": live, "changes": changes,
        "book": book_block, "indicators": indicators,
    }
    assert list(payload) == REQUIRED_TOP_LEVEL, "порядок и состав блоков — как в docs/PAYLOAD.md"
    content = {k: v for k, v in payload.items()}
    content["meta"] = {k: v for k, v in payload["meta"].items() if k not in ("generated_at", "payload_sha256", "bytes")}
    payload["meta"]["payload_sha256"] = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload["meta"]["bytes"] = len(compact.encode("utf-8"))
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(compact + "\n", encoding="utf-8", newline="\n")
    print(f"{out}: {len(compact.encode('utf-8'))} байт; медиана {headline['printed_central']} ₽, "
          f"полоса {headline['printed_band']}, точка {fair_value['printed']['central']} ₽, P(ниже рынка) {headline['p_central_below_market']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
