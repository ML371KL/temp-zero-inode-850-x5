"""(7) Проверка на ядре X5: книга = data/assumptions/assumptions.yaml (канон; book_merge.build_book).

Считает слои, долю капитала в EV, V0/D, кредитный пут слоя при σ_EV = σ акции × E/V (MODEL §7.2),
массы гейтов §13.2 при коридорах книги, распределение показателей гейтов по клеткам (для коридоров
gates.py) и цену ошибки суждений области (точка при концах диапазонов, остальное — книга). Нужен код
ядра репозитория (model/) и факты data/facts; сети не нужно.

    python -B model_check.py            → model_check_out.json, model_check.out

Рубли на акцию — функцией цены ядра (§7.2): капитал × (1 − g) × 1000 / выпущенные акции N + n; V* рынка —
v0_from_price ядра с выручкой от продажи казначейского пакета (§7.3).
"""
from __future__ import annotations

import copy
import sys
import warnings

from book_merge import build_book
from common import REPO, Report, load, put0, r4, write_json

sys.path.insert(0, str(REPO))
warnings.simplefilter("ignore")
from model.book import override  # noqa: E402
from model.book_schema import BookError, validate_book  # noqa: E402
from model.checks import gate_violations  # noqa: E402
from model.core import v0_from_price  # noqa: E402
from model.facts import core_facts, load_facts  # noqa: E402
from model.grid import evaluate  # noqa: E402

R = Report("model_check")


A, used = build_book()
R.h("Книга прогона")
R(f"  {', '.join(used)} (версия {A['meta']['version']} от {A['meta']['date']})")
try:
    validate_book(A)
    R("  схема: книга читается (validate_book без замечаний)")
    schema_errors = []
except BookError as e:
    schema_errors = str(e).splitlines()
    R(f"  схема: {len(schema_errors)} замечаний (прогон идёт без проверки):")
    for line in schema_errors[:12]:
        R("    " + line)

F = load_facts(REPO / "data" / "facts")
CF = core_facts(F, A)
M = load("market.json")
price_mkt = M["price_2026_09_25"]["v"]
sigma_e = M["vol_weekly_tr"]
gov = float(A["valuation"]["governance_discount"])
issued = CF.shares_mln + CF.treasury_mln                 # N + n (§7.2)
treasury_value = CF.treasury_mln * float(A["valuation"]["treasury_sale_price_k"]) * price_mkt / 1000.0


def per_share(bn: float) -> float:
    """Рубли на акцию из млрд ₽ капитала — функция цены ядра (§7.2)."""
    return bn * (1.0 - gov) * 1000.0 / issued


def layers_of(G):
    return {k: dict(v0=L.v0, d=L.d, equity=L.equity, price=L.price, v0_to_d=L.v0_to_d,
                    equity_share=(L.v0 - L.d) / L.v0, pv_shield=L.pv_shield, pv_terminal=L.pv_terminal,
                    terminal_share=L.terminal_share, ev_ebitda=L.ev_ebitda_fwd)
            for k, L in G.layers.items()}


out: dict = {"book": used, "version": str(A["meta"]["version"]), "schema_errors": schema_errors}
G = evaluate(A, CF)
R.h(f"Слои книги (факты data/facts: NWC {CF.nwc:.3f} млрд ₽ — определение A-W1)")
for k, L in layers_of(G).items():
    R(f"  {k:15s} V0 {L['v0']:8.1f}  D {L['d']:6.1f}  капитал {L['equity']:7.1f}  цена {L['price']:7.1f} ₽  "
      f"V0/D {L['v0_to_d']:.2f}  капитал/EV {L['equity_share']:.3f}  щит {L['pv_shield']:6.1f}  "
      f"доля терм. {L['terminal_share']:.3f}")
P = G.point
R(f"  точка {P.central:.1f} ₽ (низ {P.low:.1f}, верх {P.high:.1f}); V0 точки {P.v0_point:.1f} против V* "
  f"{P.v_star:.1f}; 1 % EV = {P.rub_per_1pct_ev_point:.1f} ₽")
out["run"] = {"layers": {k: {kk: r4(vv) for kk, vv in L.items()} for k, L in layers_of(G).items()},
              "point": r4(P.central), "low": r4(P.low), "high": r4(P.high), "v_star": r4(P.v_star)}

# ------------------------------------------------------------------ щит: явный и терминальный
R.h("Налоговый щит в V0 слоя «свой взгляд» (APV, MODEL §5–6)")
sh_exp = sh_term = 0.0
for c in G.cells:
    res, p = c.result, c.p["analytical"]
    t = res.terminal
    part = res.pv_terminal * t.tv_shield / (t.tv_flow + t.tv_shield) if (t.tv_flow + t.tv_shield) else 0.0
    sh_exp += p * res.pv_shield
    sh_term += p * part
v0a = G.layers["analytical"].v0
R(f"  щит явного участка {sh_exp:.1f} + щит терминала {sh_term:.1f} = {sh_exp + sh_term:.1f} млрд ₽ = "
  f"{(sh_exp + sh_term) / v0a:.3f} V0 ({per_share(sh_exp + sh_term):.0f} ₽ на акцию)")
out["shield"] = {"explicit": r4(sh_exp), "terminal": r4(sh_term), "share_v0": r4((sh_exp + sh_term) / v0a)}

# ------------------------------------------------------------------ кредитный пут слоя (MODEL §7.2)
R.h("Кредитный пут слоя: нужен ли колл Мертона")
T_debt = 1.24      # средний срок до оферты облигаций 28.09.2026 (X2 §3.2); банки — 2026–2028
put_rows = []
for k, L in layers_of(G).items():
    ev_share = L["equity"] / L["v0"]
    for sig_e, T in ((sigma_e, T_debt), (sigma_e, 3.0), (0.40, 3.0)):
        s_ev = sig_e * ev_share
        p = put0(L["v0"], L["d"], s_ev, T)
        rub = per_share(p)
        put_rows.append({"layer": k, "sigma_e": sig_e, "T": T, "sigma_ev": r4(s_ev), "put_bn": p, "rub": rub})
        R(f"  {k:15s} σ_E {sig_e:.3f} × E/V {ev_share:.3f} = σ_EV {s_ev:.4f}, T {T:.2f}: пут {p:.2e} млрд ₽ = "
          f"{rub:.4f} ₽ на акцию")
Dm = G.layers["analytical"].d
vs = v0_from_price(price_mkt, Dm, gov, CF.shares_mln, CF.treasury_mln, treasury_value)
emkt = vs - Dm
R(f"  рынок: V* = {vs:.1f} (ядро, с выручкой от продажи пакета {treasury_value:.1f}), D {Dm:.1f}, V*/D {vs / Dm:.2f}; "
  f"E/V* {emkt / vs:.3f} → σ_EV {sigma_e * emkt / vs:.4f}; пут {put0(vs, Dm, sigma_e * emkt / vs, T_debt):.2e} млрд ₽")
sig_book = sigma_e * (G.layers["analytical"].v0 - Dm) / G.layers["analytical"].v0
R(f"  при фиксированной σ_EV слоя «свой взгляд» ({sig_book:.4f}) — пут на акцию при V0/D = 1,5 и V0/D, где пут "
  f"достигает 1 ₽ и 25 ₽ (полшага печати):")
thr = {}
for T in (T_debt, 3.0):
    at15 = per_share(put0(1.5 * Dm, Dm, sig_book, T))
    thr[f"put_at_1.5_T{T}_rub"] = r4(at15)
    line = f"    T {T:.2f}: при V0/D 1,5 — {at15:.2f} ₽"
    for target in (1.0, 25.0):
        lo, hi = 1.0001, 5.0
        for _ in range(200):
            mid = (lo + hi) / 2
            p = per_share(put0(mid * Dm, Dm, sig_book, T))
            lo, hi = (mid, hi) if p > target else (lo, mid)
        thr[f"T{T}_rub{target:g}"] = r4(hi)
        line += f"; {target:g} ₽ — при V0/D {hi:.3f}"
    R(line)
lo, hi = 1.0, 10.0
for _ in range(100):
    mid = (lo + hi) / 2
    p = per_share(put0(1.5 * Dm, Dm, sig_book, mid))
    lo, hi = (lo, mid) if p > 25.0 else (mid, hi)
thr["T_for_25rub_at_1.5"] = r4(lo)
R(f"    при V0/D 1,5 пут достигает 25 ₽ на сроке {lo:.2f} года (срок долга X5 — 1,24 года до оферт облигаций)")
out["credit_put"] = {"rows": [{k: (r4(v) if isinstance(v, float) else v) for k, v in r.items()} for r in put_rows],
                     "v_star": r4(vs), "d": r4(Dm), "e_to_v_star": r4(emkt / vs), "sigma_ev_market": r4(sigma_e * emkt / vs),
                     "sigma_ev_book": r4(sig_book), "thresholds": thr, "sigma_e": sigma_e, "T": T_debt,
                     "issued_shares_mln": r4(issued)}

# ------------------------------------------------------------------ гейты: распределение и массы
R.h("Показатели гейтов по клеткам (слой «свой взгляд»)")
cells = []
for c in G.cells:
    res = c.result
    real = res.terminal.rate - float(A["worlds"][res.cell.world]["lt"]["inflation"])
    cp = [v for _, v in res.capex_pct_years]
    cells.append(dict(key=c.key, p=c.p["analytical"], ev=res.ev, ev_ebitda=res.ev_ebitda_fwd,
                      term=res.terminal_share, mmin=res.margin_min, mmax=res.margin_max, cmin=min(cp),
                      cmax=max(cp), lev=res.max_leverage, real=real, equity=res.equity, price=res.price))
    R(f"  {c.key:18s} P {c.p['analytical']:.4f}  EV {res.ev:7.1f}  EV/EBITDA {res.ev_ebitda_fwd:5.2f}  "
      f"терм {res.terminal_share:.3f}  маржа {res.margin_min:.4f}…{res.margin_max:.4f}  capex "
      f"{min(cp):.4f}…{max(cp):.4f}  рычаг {res.max_leverage:.2f}  реал. {real:.4f}  цена {res.price:8.1f}")
viol = gate_violations(G)
masses = {}
for g, rows in viol.items():
    keys = {k for k, _ in rows}
    mass = sum(c["p"] for c in cells if c["key"] in keys)
    if any(k.startswith("слой") for k in keys):
        mass = 1.0
    masses[g] = r4(mass)
    R(f"  гейт {g:15s}: нарушений {len(rows):2d}, масса {mass:.4f}" + (f" — {rows[:4]}" if rows else ""))
out["cells"] = [{k: (r4(v) if isinstance(v, float) else v) for k, v in c.items()} for c in cells]
out["gate_masses"] = masses

# ------------------------------------------------------------------ цена ошибки суждений области
R.h("Цена ошибки суждений области: точка при концах диапазона (остальное — книга)")
base_point = G.point.central
# Концы — диапазоны книги (A-V7, A-F1, A-F1b, A-F1c, A-F4, A-F4b, A-F2, A-F3b); спреды base — сам ключ (остальное —
# книга: base — ещё и справедливый спред, A-P4w) и отдельной строкой совместный сдвиг base и stress.
SENS = [
    ("β_u", ["valuation.beta_u"], "value", 0.45, 0.75),
    ("ERP", ["valuation.erp"], "value", 0.049, 0.065),
    ("Дисконт за управление", ["valuation.governance_discount"], "value", 0.0, 0.05),
    ("Целевой рычаг", ["financing.target_leverage"], "value", 1.0, 1.6),
    ("Спред плавающего долга, base", ["financing.spread_float.base"], "value", 0.010, 0.014),
    ("Спред плавающего, base и stress вместе", ["financing.spread_float.base", "financing.spread_float.stress"],
     "shift", -0.002, 0.002),
    ("Спред фикса, base", ["financing.spread_fixed.base"], "value", 0.008, 0.014),
    ("Спред фикса, base и stress вместе", ["financing.spread_fixed.base", "financing.spread_fixed.stress"],
     "shift", -0.003, 0.003),
    ("Издержки размещения", ["financing.issuance_cost"], "value", 0.0, 0.0015),
    ("Доля фикса (сдвиг)", ["financing.fixed_share"], "shift", -0.10, 0.10),
    ("Купон «старого» фикса", ["financing.legacy_rate"], "value", 0.145, 0.155),
    ("Доходность кассы k", ["financing.cash_yield_k"], "value", 0.80, 1.05),
    ("Подушка кассы", ["financing.cash_buffer_pct"], "value", 0.0, 0.0145),
    ("Веса миров", ["joint.world_prob"], "dict", {"N": 0.25, "H": 0.50, "M": 0.25}, {"N": 0.40, "H": 0.45, "M": 0.15}),
    ("Инфляция мира M", ["worlds.M.cpi", "worlds.M.food_cpi", "worlds.M.lt.inflation"], "shift", -0.003, 0.005),
]
sens = []
for name, paths, kind, lo, hi in SENS:
    pl = evaluate(override(A, paths, kind, lo), CF).point.central
    ph = evaluate(override(A, paths, kind, hi), CF).point.central
    sens.append({"name": name, "low": lo, "high": hi, "point_low": r4(pl), "point_high": r4(ph),
                 "d_low": r4(pl - base_point), "d_high": r4(ph - base_point), "swing": r4(abs(ph - pl))})
    R(f"  {name:38s} {str(lo):>28s} → {pl:8.1f} ₽ ({pl - base_point:+6.1f}) | {str(hi):>28s} → {ph:8.1f} ₽ "
      f"({ph - base_point:+6.1f}) | размах {abs(ph - pl):6.1f}")
# альтернативы-решения
alts = []
B = override(override(A, ["financing.spread_float.stress"], "value", 0.020),
             ["financing.spread_fixed.stress"], "value", 0.026)
alts.append(("пиковые спреды stress (КС + 2,00; G + 2,60)", evaluate(B, CF).point.central))
B = copy.deepcopy(A)
B["bridge"]["include"] = list(A["bridge"]["include"]) + ["lti_liability"]
alts.append(("LTI в мосте", evaluate(B, core_facts(F, B)).point.central))
B = copy.deepcopy(A)
B["bridge"]["include"] = [k for k in A["bridge"]["include"] if k != "deferred_consideration"]
alts.append(("без отложенного возмещения по сделкам", evaluate(B, core_facts(F, B)).point.central))
B = copy.deepcopy(A)
B["financing"]["dividends_from"] = "2026H2"
alts.append(("выплаты с 2026H2", evaluate(B, CF).point.central))
B = copy.deepcopy(A)
B["joint"]["world_links"]["M"]["credit"] = "base"
alts.append(("мир M с кредитом base", evaluate(B, CF).point.central))
for name, p in alts:
    R(f"  {name:44s} → {p:8.1f} ₽ ({p - base_point:+.1f} к точке {base_point:.1f})")
# обратный поиск для точки: при каком значении суждения точка = рыночной цене (проверка отрезков search)
R.h("Обратный поиск для точки (проверка отрезков search обратного DCF; печатаемый — для медианы, MODEL §11)")
rev = []
for name, paths, kind, a, b in (("β_u", ["valuation.beta_u"], "value", 0.2, 2.0),
                                ("ERP", ["valuation.erp"], "value", 0.0, 0.20),
                                ("Дисконт за управление", ["valuation.governance_discount"], "value", 0.0, 0.9)):
    fa = evaluate(override(A, paths, kind, a), CF).point.central - price_mkt
    fb = evaluate(override(A, paths, kind, b), CF).point.central - price_mkt
    if fa * fb > 0:
        rev.append({"name": name, "solved": None})
        R(f"  {name}: на [{a}; {b}] решения нет (точка {fa + price_mkt:.0f} … {fb + price_mkt:.0f} ₽)")
        continue
    for _ in range(40):
        m = (a + b) / 2
        fm = evaluate(override(A, paths, kind, m), CF).point.central - price_mkt
        if abs(fm) < 1.0:
            break
        a, b, fa = (a, m, fa) if fa * fm <= 0 else (m, b, fm)
    rev.append({"name": name, "solved": r4(m)})
    R(f"  {name}: {m:.4f} (точка = {price_mkt} ₽)")
out["reverse_point"] = rev
out["sensitivities"] = sens
out["alternatives"] = [{"name": n, "point": r4(p), "delta": r4(p - base_point)} for n, p in alts]
out["base_point"] = r4(base_point)

write_json("model_check_out.json", out)
R.save()
