"""Лист налога X5 (A-T1 ставка, A-T2 постоянные разницы в доле выручки).

Постоянные разницы берутся из сверки расчётной и эффективной ставок МСФО (2023–2025) и из
эффективной ставки 1П2026 до МСФО 16 (промежуточная отчётность по МСФО (IAS) 34 применяет
оценку годовой эффективной ставки). База модели: τ × (EBIT − LTI + padd × выручка − I).
Разовые статьи (налог на сверхприбыль 2023, переоценка отложенного налога при смене ставки
2024) из padd исключены. Читает только inputs/.

    python -B tax.py
"""
from __future__ import annotations

from common import PRIM, Report, an, hy, r6

R = Report()
TR = PRIM["tax_reconciliation"]
PERM = ["inventory_shortages", "other_rates", "dta_writeoff", "other_nondeductible"]
ONE_OFF = ["windfall_tax", "rate_change"]

# ================================================================ 1. сверка ставок МСФО
R.h("1. Сверка ставок МСФО: постоянные разницы, млрд ₽ налога и база в % выручки")
rows, obs = [], {}
for y in ("2023", "2024", "2025"):
    t = TR[y]
    items = {k: t[k] for k in PERM}
    total_check = t["tax_at_statutory"] + sum(items.values()) + sum(t[k] for k in ONE_OFF) - t["total"]
    perm_tax = sum(items.values())
    base = perm_tax / t["statutory_rate"]
    rev = an(y, "revenue")
    obs[y] = {"perm_tax": perm_tax, "base": base, "revenue": rev, "pct": base / rev,
              "rate": t["statutory_rate"], "check": total_check,
              "by_item_pct": {k: v_ / t["statutory_rate"] / rev for k, v_ in items.items()}}
    rows.append([y, t["statutory_rate"], t["inventory_shortages"], t["other_rates"],
                 t["dta_writeoff"], t["other_nondeductible"], perm_tax, base, 100 * base / rev,
                 round(total_check, 3)])
R.table(["год", "ставка", "недостачи", "иные ставки", "списание ОНА", "прочие",
         "итого налог", "база", "% выручки", "невязка"], rows)
R.p("Исключены как разовые: налог на сверхприбыль 2023 (1,689), доход от переоценки "
    "отложенного налога при смене ставки 2024 (−3,995)")

# ================================================================ 2. до МСФО 16 и полугодия
R.h("2. Эффективная ставка до МСФО 16 и сверх-налог к 25 % (20 % до 2025 г.)")
rows = []
half_obs = {}
for p in ("2024H1", "2024H2", "2025H1", "2025H2", "2026H1"):
    tau = 0.20 if p.startswith("2024") else 0.25
    pbt, tax = hy(p, "pbt"), -hy(p, "income_tax")
    pbt16, tax16 = hy(p, "pbt_ifrs16"), -hy(p, "income_tax_ifrs16")
    ex = tax - tau * pbt
    ex16 = tax16 - tau * pbt16
    rev = hy(p, "revenue")
    half_obs[p] = {"etr_pre16": tax / pbt, "excess_pre16": ex, "excess_ifrs16": ex16,
                   "pct": ex / tau / rev}
    rows.append([p, tau, pbt, tax, 100 * tax / pbt, ex, ex16, 100 * ex / tau / rev])
for y in ("2024", "2025"):
    tau = 0.20 if y == "2024" else 0.25
    pbt, tax = an(y, "pbt"), -an(y, "income_tax")
    pbt16, tax16 = an(y, "pbt_ifrs16"), -an(y, "income_tax_ifrs16")
    rows.append([y, tau, pbt, tax, 100 * tax / pbt, tax - tau * pbt, tax16 - tau * pbt16,
                 100 * (tax - tau * pbt) / tau / an(y, "revenue")])
R.table(["период", "ставка", "ПДН до МСФО 16", "налог", "эфф. %", "сверх τ·ПДН",
         "то же МСФО 16", "база % выручки"], rows)
R.p("До МСФО 16 налог = налог МСФО 16 + τ × разница прибыли до налога (2025: сверх-налог "
    "−1,547 против −1,548 по МСФО 16) — постоянные разницы в обоих базисах одни. 2024 г. до "
    "МСФО 16 не содержит переоценки отложенного налога по аренде при смене ставки, поэтому его "
    "полугодия с годом МСФО не сравниваются; для 2024 г. берётся сверка МСФО.")
obs["2026H1"] = {"base": half_obs["2026H1"]["excess_pre16"] / 0.25,
                 "revenue": hy("2026H1", "revenue"), "pct": half_obs["2026H1"]["pct"]}

# ================================================================ 3. центр и диапазон
R.h("3. A-T2. Центр и диапазон padd")
keys = ["2023", "2024", "2025", "2026H1"]
w = sum(obs[k]["base"] for k in keys) / sum(obs[k]["revenue"] for k in keys)
eq = sum(obs[k]["pct"] for k in keys) / len(keys)
recent = ["2024", "2025", "2026H1"]
w_recent = sum(obs[k]["base"] for k in recent) / sum(obs[k]["revenue"] for k in recent)
R.p("Наблюдения, % выручки:", {k: round(100 * obs[k]["pct"], 3) for k in keys})
R.p(f"Взвешенно по выручке 2023–1П2026: {100 * w:.3f} %; равными весами {100 * eq:.3f} %; "
    f"2024–1П2026 взвешенно {100 * w_recent:.3f} %")
center = round(w, 4)
lo = round(min(obs[k]["pct"] for k in keys), 4)
hi = round(max(obs[k]["pct"] for k in keys), 4)
R.p(f"Книга: padd = {center}; диапазон — крайние наблюдения [{lo}; {hi}]")
by_item = {k: sum(obs[y]["by_item_pct"][k] * obs[y]["revenue"] for y in ("2023", "2024", "2025"))
           / sum(obs[y]["revenue"] for y in ("2023", "2024", "2025")) for k in PERM}
R.p("Состав (2023–2025, взвешенно, % выручки):", {k: round(100 * v_, 3) for k, v_ in by_item.items()})
R.data = {"annual": {y: {k: (r6(v_) if isinstance(v_, float) else
                             {kk: r6(vv) for kk, vv in v_.items()} if isinstance(v_, dict) else v_)
                         for k, v_ in o.items()} for y, o in obs.items()},
          "halves": {p: {k: r6(v_) for k, v_ in o.items()} for p, o in half_obs.items()},
          "weighted_2023_2026H1": r6(w), "equal_2023_2026H1": r6(eq),
          "weighted_2024_2026H1": r6(w_recent), "by_item": {k: r6(v_) for k, v_ in by_item.items()},
          "book": {"rate": 0.25, "permanent_add_pct": center, "range": [lo, hi]}}

R.save("tax")
print("\n".join(R.lines))
