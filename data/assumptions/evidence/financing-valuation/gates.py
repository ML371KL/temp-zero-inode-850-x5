"""(6) Коридоры гейтов правдоподобия по истории X5 и аналогам (docs/MODEL.md §13.2–13.3).

Ключи книги: checks.ev_ebitda{N,H,M}, margin_range, capex_range, terminal_share, real_rate, max_leverage,
min_v0_to_d, book_update. Читает inputs/ и (для масс гейтов на книге) model_check_out.json.
Выход: gates_out.json, gates.out.
"""
from __future__ import annotations

import datetime as dt
import json

from common import HERE, Report, avg_key, d, key_rate_fn, load, mean, r4, write_json

R = Report("gates")
EVH = load("ev_history.json")
W_ = load("worlds.json")["worlds"]
K = load("keyrate.json")
KR = key_rate_fn(K["changes_2013_2023"] + K["changes"])
out: dict = {}

# ------------------------------------------------------------------ EV/EBITDA
R.h("1. EV / EBITDA X5 до МСФО 16 в истории: LTM и следующие 12 мес. (фактические, скорр.)")
adj_q = EVH["adj_ebitda_q"]
qkeys = sorted(adj_q)
rows = []
for r in EVH["rows"]:
    ev = r["price"] * r["shares_mln"] / 1000 + r["nd"] + r["div_after"]
    i = qkeys.index(r["quarter"])
    ntm = sum(adj_q[k] for k in qkeys[i + 1:i + 5]) if i + 4 < len(qkeys) else None
    y, qn = int(r["quarter"][:4]), int(r["quarter"][-1])
    q0 = dt.date(y, 3 * qn - 2, 1)
    q1 = dt.date(y + (qn == 4), 1 if qn == 4 else 3 * qn + 1, 1)
    ks = avg_key(KR, q0, q1)
    regime = "N" if ks <= 0.085 else ("H" if ks < 0.14 else "M")
    rows.append(dict(q=r["quarter"], ev=ev, ltm=ev / r["ebitda_ltm"], ltm_adj=ev / r["adj_ebitda_ltm"],
                     ntm=ev / ntm if ntm else None, key=ks, regime=regime))
    R(f"  {r['quarter']} {r['sec'][:4]:4s} EV {ev:7.1f}  LTM {ev / r['ebitda_ltm']:.2f}×  NTM "
      f"{(ev / ntm) if ntm else float('nan'):.2f}×  КС квартала {ks:.4f} → режим {regime}")
R("  режимы по средней ключевой квартала — как у миров: N (≤ 8,5 %; мир N: 8 % в долгую), H (8,5–14 %; мир H: "
  "14 → 10,5 %), M (≥ 14 %; мир M: 14 → 17,5 %)")
stats = {}
for w in ("N", "H", "M"):
    xs = [x for x in rows if x["regime"] == w]
    ntm = [x["ntm"] for x in xs if x["ntm"]]
    fwd = ntm or [x["ltm_adj"] / 1.10 for x in xs]         # нет факта следующих 12 мес. — LTM / 1,10
    stats[w] = {"n": len(xs), "ltm_min": min(x["ltm"] for x in xs), "ltm_max": max(x["ltm"] for x in xs),
                "fwd_min": min(fwd), "fwd_max": max(fwd), "fwd_mean": mean(fwd), "fwd_kind": "NTM" if ntm else "LTM/1,1"}
    s = stats[w]
    R(f"  режим {w}: кварталов {s['n']}; LTM {s['ltm_min']:.2f}–{s['ltm_max']:.2f}×; вперёд ({s['fwd_kind']}) "
      f"{s['fwd_min']:.2f}–{s['fwd_max']:.2f}×, среднее {s['fwd_mean']:.2f}×")
all_fwd = [x["ntm"] for x in rows if x["ntm"]]
R(f"  вся история NTM: {min(all_fwd):.2f}–{max(all_fwd):.2f}×; аналоги LTM 1П2026 (X4 §3): Магнит 3,50×, Лента 3,79× "
  f"(pro forma 3,4–3,5×), Fix Price 2,00×; X5 на 25.09.2026 — 2,84× (2,63× без дивиденда июля)")
floor = 1.5
caps = {w: round(1.25 * stats[w]["fwd_max"] * 2) / 2 for w in ("N", "M")}
# режим H в истории — 3 квартала 2022–2023 гг. (шок 2022 г., ГДР в ловушке) — мало и смещено; верх мира H —
# интерполяция между N и M по долгосрочному узлу кривой мира (9,0 / 12,7 / 16,7 %)
zl = {w: W_[w]["zero_curve"]["LT"] for w in ("N", "H", "M")}
caps["H"] = round((caps["N"] + (caps["M"] - caps["N"]) * (zl["H"] - zl["N"]) / (zl["M"] - zl["N"])) * 2) / 2
ev_ebitda = {w: [floor, caps[w]] for w in ("N", "H", "M")}
R(f"  КНИГА checks.ev_ebitda = {ev_ebitda}: низ {floor}× во всех мирах — ниже минимума истории X5 "
  f"({min(all_fwd):.2f}× NTM, 2022 г.) и Fix Price (2,0×), у порога нулевого капитала при нынешнем долге "
  f"(D ≈ 420 / EBITDA ≈ 330 млрд ₽ ≈ 1,3×); верх — 1,25 × максимум NTM своего режима ставок (N, M), у H — интерполяция по узлу LT кривой; округление до 0,5×")
out["ev_ebitda"] = {"rows": [{k: (r4(v) if isinstance(v, float) else v) for k, v in x.items()} for x in rows],
                    "stats": {w: {k: (r4(v) if isinstance(v, float) else v) for k, v in s.items()} for w, s in stats.items()},
                    "book": ev_ebitda}

# ------------------------------------------------------------------ маржа
R.h("2. Маржа скорр. EBITDA до МСФО 16 по полугодиям (margin_range)")
MH = load("x5_margin_history.json")["quarters"]
halves = {}
for k, x in MH.items():
    h = f"{k[:4]}H{1 if k[-1] in '12' else 2}"
    a = halves.setdefault(h, [0.0, 0.0, 0])
    a[0] += x["revenue"]["v"]
    a[1] += x["adj_ebitda"]["v"]
    a[2] += 1
hm = {h: a[1] / a[0] for h, a in halves.items() if a[2] == 2}
lo_h, hi_h = min(hm, key=hm.get), max(hm, key=hm.get)
peers = load("margin_peers.json")["peers_2025"]
R(f"  X5 {min(hm)}–{max(hm)}: минимум {hm[lo_h]:.4f} ({lo_h}), максимум {hm[hi_h]:.4f} ({hi_h}); аналоги 2025: "
  f"Магнит {peers['magnit']['margin']:.4f}, Лента {peers['lenta']['margin']:.4f}")
margin_range = [0.045, 0.090]
R(f"  КНИГА checks.margin_range = {margin_range}: низ — ниже самого слабого федерального аналога (Магнит 2025 г. "
  f"{peers['magnit']['margin']:.2%}) и на 1,2 п.п. ниже минимума X5; верх — выше исторического максимума X5 "
  f"({hm[hi_h]:.2%})")
out["margin_range"] = {"x5_min": r4(hm[lo_h]), "x5_min_half": lo_h, "x5_max": r4(hm[hi_h]), "x5_max_half": hi_h,
                       "book": margin_range}

# ------------------------------------------------------------------ capex
R.h("3. Capex / выручка по годам (capex_range)")
CH = load("capex_history.json")
cr = {y: x["capex"] / x["revenue"] for y, x in CH["years"].items()}
for y, x in sorted(cr.items()):
    R(f"  {y}: {x:.4f}")
h1 = CH["2026H1"]
R(f"  1П2026: {h1['capex'] / h1['revenue']:.4f}; D&A до МСФО 16 без обесценения — 2,32 % выручки 2025 г. "
  f"(лист capex-wc-tax); прогноз компании на 2026 г. — 4,5–4,7 %")
capex_range = [0.020, 0.075]
R(f"  КНИГА checks.capex_range = {capex_range}: низ — ниже D&A (2,32 %) и минимума истории ({min(cr.values()):.2%}, "
  f"{min(cr, key=cr.get)}): год с таким capex — проедание основных средств; верх — выше пика экспансии "
  f"({max(cr.values()):.2%}, {max(cr, key=cr.get)})")
out["capex_range"] = {"years": {y: r4(x) for y, x in cr.items()}, "book": capex_range}

# ------------------------------------------------------------------ доля терминала
R.h("4. Доля PV терминала в EV (terminal_share)")
W = load("worlds.json")["worlds"]
prem = 0.60 * 0.0557
t_end = 10.27                                   # срок терминала от даты книги (run_output ядра: 10,2663)
shares = {}
for w, node in W.items():
    r = node["zero_curve"]["LT"] + prem
    g = node["lt_inflation"]
    share = ((1 + g) / (1 + r)) ** t_end
    shares[w] = share
    R(f"  мир {w}: r {r:.4f}, рост ≈ π_LT {g:.4f} → доля терминала у ровного потока ((1+g)/(1+r))^{t_end} = {share:.3f}")
t_lo, t_hi = round(min(shares.values()) - 0.2, 2), round(max(shares.values()) + 0.2, 2)
terminal_share = [0.15, 0.65]
R(f"  ровный поток: {min(shares.values()):.3f}–{max(shares.values()):.3f}; ± 0,2 на форму пути (стресс сжимает явный "
  f"поток, полный возврат — растягивает) → {t_lo}–{t_hi} → КНИГА checks.terminal_share = {terminal_share}")
out["terminal_share"] = {"steady": {w: r4(x) for w, x in shares.items()}, "book": terminal_share}

# ------------------------------------------------------------------ реальная ставка
R.h("5. Реальная ставка терминала r(LT) − π_LT (real_rate)")
MAG = load("magnit_book.json")
real = {}
for w, node in W.items():
    base = node["zero_curve"]["LT"] - node["lt_inflation"]
    lo = base + 0.45 * 0.049 - (0.005 if w == "M" else 0.0)
    hi = base + 0.75 * 0.065 + (0.003 if w == "M" else 0.0)
    real[w] = [base + prem, lo, hi]
    R(f"  мир {w}: безрисковая реальная {base:.4f}; с премией книги {base + prem:.4f}; по концам полосы β, ERP "
      f"(и инфляции M) {lo:.4f}–{hi:.4f}")
real_rate = [0.02, 0.13]
R(f"  у Магнита — {MAG['checks_py']['REAL_RATE_RANGE']} (model/checks.py): общая макро-основа, премия X5 ниже на "
  f"0,56 п.п. → КНИГА checks.real_rate = {real_rate}: гейт ловит рассогласованный мир (кривая LT ниже инфляции или "
  f"выше неё на 13 п.п.), а не суждения X5")
out["real_rate"] = {"worlds": {w: [r4(x) for x in v] for w, v in real.items()}, "book": real_rate}

# ------------------------------------------------------------------ рычаг, подушка, обновление книги
R.h("6. Остальные коридоры")
lev = json.loads((HERE / "leverage_out.json").read_text(encoding="utf-8"))["max_leverage"]
R(f"  max_leverage = {lev['book']}: ковенант {lev['covenant']}× (история — до {lev['hist_max_quarter']}×) — leverage.py")
mc = json.loads((HERE / "model_check_out.json").read_text(encoding="utf-8"))
thr = mc["credit_put"]["thresholds"]
R(f"  min_v0_to_d = 1.5: при V0/D 1,5 кредитный пут слоя (σ_EV книги, T 1,24 / 3 года) — "
  f"{thr.get('put_at_1.5_T1.24_rub', float('nan')):.1f} / {thr.get('put_at_1.5_T3.0_rub', float('nan')):.1f} ₽ на акцию, "
  f"меньше полушага печати (25 ₽) — model_check.py")
R(f"  book_update = {{shift_bp: 50, max_age_days: 45}}: {MAG['book_update_rule']} — миры общие, правило общее")
out["other"] = {"max_leverage": lev["book"], "min_v0_to_d": 1.5, "book_update": {"shift_bp": 50, "max_age_days": 45}}

# ------------------------------------------------------------------ массы на книге
R.h("7. Массы гейтов на книге (клетки model_check_out.json, слой «свой взгляд»)")
cells = mc["cells"]


def mass(cond) -> tuple[float, list]:
    hit = [c for c in cells if cond(c)]
    return sum(c["p"] for c in hit), [c["key"] for c in hit]


checks = {
    "ev_ebitda": mass(lambda c: not ev_ebitda[c["key"][0]][0] <= c["ev_ebitda"] <= ev_ebitda[c["key"][0]][1]),
    "margin_range": mass(lambda c: c["mmin"] < margin_range[0] or c["mmax"] > margin_range[1]),
    "capex_range": mass(lambda c: c["cmin"] < capex_range[0] or c["cmax"] > capex_range[1]),
    "terminal_share": mass(lambda c: not terminal_share[0] <= c["term"] <= terminal_share[1]),
    "real_rate": mass(lambda c: not real_rate[0] <= c["real"] <= real_rate[1]),
    "leverage_path": mass(lambda c: c["lev"] > lev["book"]),
    "equity_cushion": mass(lambda c: c["equity"] <= 0),
}
for g, (m, keys) in checks.items():
    R(f"  {g:15s} масса {m:.4f}  {keys}")
out["masses"] = {g: {"mass": r4(m), "cells": keys} for g, (m, keys) in checks.items()}

write_json("gates_out.json", out)
R.save()
