"""(3) Какие строки моста входят в требования (bridge.include) — решение по каждой строке.

Семантика — docs/MODEL.md §7.1: D = ЧД факт + операционная касса якоря + Σ включённых строк − перекат +
объявленные дивиденды с отсечкой ≤ даты оценки. Читает только inputs/. Выход: bridge_out.json, bridge.out.
"""
from __future__ import annotations

from common import Report, load, r4, v, write_json

R = Report("bridge")
FX = load("facts_x5.json")
W = load("worlds.json")["worlds"]
MH = load("x5_margin_history.json")
lines = {ln["key"]: ln for ln in FX["bridge"]["lines"]}
out: dict = {"lines": {}}

DECISION = {
    "accrued_interest": (True, "долг: требование кредиторов = номинал + НКД; в «общий долг» и ЧД компании не входит. "
                               "Проценты модели начисляются с 2П2026 и в стартовый долг начисленное за 1П не кладут"),
    "nci_put": (True, "долг: обязательство выкупить неконтролирующие доли (опцион пут продавцов); самих НДУ в "
                      "капитале нет (прочерк на 30.06.2026), двойного вычета нет"),
    "lti_liability": (False, "не требование: программа LTI скользящая, модель платит LTI деньгами в момент начисления "
                             "(A-C7, MODEL §4.4) — остаток, который фирма всегда носит, в путь денег не входит; "
                             "вычесть его ещё раз значит заплатить за одну и ту же службу дважды"),
    "tax_provisions_net": (True, "долгоподобно: оценка по IFRIC 23 ожидаемых доплат за прошлые периоды; будущий налог "
                                 "модели (A-T1, A-T2) их не содержит; за вычетом компенсирующего актива — возмещений "
                                 "продавцов купленных бизнесов"),
    "income_tax_net": (True, "налог на прибыль к уплате − к возмещению: налог модели денежный в периоде (A-T1, A-W1 — "
                             "вне оборотного капитала), значит остаток на якоре гасится в 2П2026 и должен быть в "
                             "требованиях; на 30.06.2026 — требование к бюджету (знак «−»)"),
    "deferred_consideration": (True, "долг продавцам купленных бизнесов: бизнесы уже в якоре (выручка, EBITDA, их "
                                     "долг — в ЧД), а платёж не входит ни в NWC (кредиторка за ОС, НМА и бизнесы вне "
                                     "него), ни в capex модели (M&A в нём нет); 1,244 — отложенная часть сделок "
                                     "1П2026 (МСФО 6М2026 прим. 6), нижняя граница остатка на 30.06"),
    "st_investments": (True, "актив: краткосрочные финансовые вложения ЧД компании не уменьшают (вычитается только касса)"),
    "associates": (True, "актив вне EBITDA: доля в прибыли ассоциированных и СП в EBITDA не входит, стоимость — по "
                         "балансу"),
}

R.h("1. Строки факта bridge.json (30.06.2026, млрд ₽; обязательство «+», актив «−»)")
inc_total = all_total = 0.0
for key, ln in lines.items():
    amt = v(ln["amount"])
    use, why = DECISION[key]
    all_total += amt
    inc_total += amt if use else 0.0
    out["lines"][key] = {"amount": amt, "include": use, "why": why}
    R(f"  {'ВКЛ ' if use else 'нет '} {key:22s} {amt:+8.3f}  {ln['label']}: {why}")
R(f"  сумма включённых {inc_total:+.3f}; всех строк {all_total:+.3f}; разница (LTI) {all_total - inc_total:+.3f}")

R.h("2. Вне моста (outside_bridge) и почему")
for key, node in FX["bridge"]["outside_bridge"].items():
    R(f"  {key}: {v(node)} — {node.get('calc', '')}")
R("  дивиденды к выплате 60,425 и невыплаченный остаток 14,4 на 13.08.2026 — не строка моста, а реестр объявленных "
  "(MODEL §7.1: требование с даты отсечки, пока деньги в компании); операционная касса якоря — A-W3 (capex-wc-tax)")

R.h("3. Почему остаток LTI — двойной счёт (скользящая программа)")
lti = FX["lti"]
stock_1231 = v(lti["liability_lt_2025_12_31"]) + v(lti["liability_st_2025_12_31"])
stock = v(lti["liability_lt_2026_06_30"])
q = MH["quarters"]
ltm_keys = ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]
e_ltm = sum(q[k]["lti"]["v"] for k in ltm_keys)
lag = stock_1231 / v(lti["expense_fy2025"])
R(f"  расход LTI: 2024 {v(lti['expense_fy2024'])}, 2025 {v(lti['expense_fy2025'])}, 1П2026 {v(lti['expense_h1_2026'])}; "
  f"LTM 2К2026 {e_ltm:.3f} (кварталы databook); форма — {lti['form']}")
R(f"  остаток: 31.12.2025 {stock_1231:.3f} (долгоср. + краткоср.) = {lag:.2f} года расхода 2025; 30.06.2026 "
  f"долгосрочный {stock:.3f} (краткосрочная часть не раскрыта)")
R("  денежный поток скользящей программы: выплата года t = начисление года t − L (L — срок до выплаты). Модель "
  "платит начисление сразу; PV денег программы = PV(начислений) × (1 + g)^−L ≈ PV(начислений) − S·g/(r − g).")
R("  значит без строки моста модель уже переплачивает ≈ S·g/(r − g), со строкой — ≈ S·r/(r − g):")
prem = 0.60 * 0.0557
rows = []
for w, node in W.items():
    r = node["zero_curve"]["LT"] + prem
    g = node["lt_inflation"]
    wo, wi = stock * g / (r - g), stock * r / (r - g)
    rows.append({"world": w, "r": r4(r), "g": r4(g), "overpay_without": r4(wo), "overpay_with": r4(wi)})
    R(f"    мир {w}: r {r:.4f}, g ≈ π_LT {g:.4f} → без строки +{wo:.1f} млрд ₽, со строкой +{wi:.1f} млрд ₽")
R("  исключение строки — меньшая из двух ошибок и в сторону осторожности (модель всё равно платит LTI раньше, чем "
  "компания)")
out["lti"] = {"stock_2025_12_31": stock_1231, "stock_2026_06_30": stock, "expense_ltm": r4(e_ltm),
              "lag_years": r4(lag), "overpay": rows}

R.h("4. Итог")
include = [k for k, (use, _) in DECISION.items() if use]
R(f"  КНИГА bridge.include = {include}; сумма строк {inc_total:+.3f} млрд ₽")
out["include"] = include
out["included_total"] = r4(inc_total)

write_json("bridge_out.json", out)
R.save()
