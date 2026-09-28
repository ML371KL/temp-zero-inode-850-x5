"""(2) Целевой рычаг, начало выплат модели, предел рычага пути.

Ключи книги: financing.target_leverage, financing.dividends_from, checks.max_leverage
(docs/MODEL.md §4.9, §13.2). Читает только inputs/. Выход: leverage_out.json, leverage.out.
"""
from __future__ import annotations

from common import Report, load, mean, r4, v, write_json

R = Report("leverage")
L = load("leverage_history.json")
FX = load("facts_x5.json")
out: dict = {}

R.h("1. ЧД / EBITDA до МСФО 16 на конец года (databook X5, лист Debt, строки 11–12)")
years = {}
for y in range(2011, 2021):
    years[y] = L["old"][str(y)]["nd_ebitda"]
for key, row in L["new"].items():
    if key.endswith("12-31"):
        years[int(key[:4])] = row["nd_ebitda"]
for y, x in sorted(years.items()):
    R(f"  {y}: {x:.2f}×")
era_old = [years[y] for y in range(2017, 2022)]
R(f"  2017–2021 (дивиденды ГДР, прежняя политика): среднее {mean(era_old):.2f}×; 2011–2016 (экспансия): "
  f"{min(years[y] for y in range(2011, 2017)):.2f}–{max(years[y] for y in range(2011, 2017)):.2f}×; "
  f"2022–2024 (касса копилась, дивидендов нет): {years[2022]:.2f} / {years[2023]:.2f} / {years[2024]:.2f}×")

R.h("2. Кварталы эпохи нынешней политики (с 20.03.2025) и до неё")
for k, x in sorted(L["new"].items()):
    R(f"  {k}: {x['nd_ebitda']:.2f}×  (ЧД {x['nd']:.1f})")
policy_q = [r["nd_ebitda"] for k, r in L["new"].items() if k >= "2025-03-31"]
R(f"  кварталы 03.2025–06.2026: {min(policy_q):.2f}–{max(policy_q):.2f}×, среднее {mean(policy_q):.2f}×")
old_q = [r["nd_ebitda"] for k, r in L["old"].items() if k.startswith("Q")]
R(f"  кварталы 2011–2022 (старый databook): максимум {max(old_q):.2f}× ({[k for k, r in L['old'].items() if r['nd_ebitda'] == max(old_q)][0]})")

R.h("3. Политика и прогноз компании")
pol = FX["dividend_policy"]
R(f"  дивидендная политика: {pol['base']} ({pol['src']}); утверждена: {pol['approved']}")
R("  прогноз 2026: «Чистый долг / EBITDA 1,2–1,4х» (q4-2025-financial-results-webcast.pdf, слайд 20 «Финансовые "
  "цели 2026»); после дивиденда за 9М 2025 — 1,17× (там же, слайд 19)")
bal = FX["balance"]
nd_anchor = v(bal["net_debt"]) + v(bal["dividends_payable"])
ebitda_ltm = [r for r in load("ev_history.json")["rows"] if r["quarter"] == "2026Q2"][0]["ebitda_ltm"]
R(f"  стартовый долг модели (ЧД + объявленные дивиденды к выплате) {nd_anchor:.3f}; отчётная EBITDA до МСФО 16 "
  f"LTM 2К2026 {ebitda_ltm:.1f} (databook, EBITDA стр. 22) → {nd_anchor / ebitda_ltm:.3f}× — компания уже у "
  f"середины политики")
Lt, lo, hi = 1.3, 1.0, 1.6
R(f"  КНИГА target_leverage = {Lt} (середина политики 1,2–1,4× и прогноза 2026); ось полосы {lo}–{hi}: низ — среднее "
  f"кварталов нынешней политики ({mean(policy_q):.2f}×, компания пока держит меньше цели), верх — симметрично +0,3 "
  f"(прежний режим 2017–2021 — {mean(era_old):.2f}×, предел выплат политики 2,0×)")
out["target_leverage"] = {"year_end": {str(y): r4(x) for y, x in years.items()}, "old_era_mean": r4(mean(era_old)),
                          "policy_quarters": [r4(x) for x in policy_q], "policy_mean": r4(mean(policy_q)),
                          "start_leverage": r4(nd_anchor / ebitda_ltm), "book": Lt, "axis": [lo, hi]}

R.h("4. С какого полугодия модель платит дивиденды (MODEL §4.9: div(p) — выплата в полугодии p)")
for row in FX["dividend_register"]:
    R(f"  реестр: {row['id']}: {v(row['amount'])} млрд, отсечка {row.get('ex_date')}, статус {row.get('status')}")
R("  цикл 2025–2026: за 9М 2025 — рекомендация 13.11.2025, ВОСА 18.12.2025, реестр 06.01.2026, выплата до "
  "23.01/13.02.2026 (1П); финал 2025 — ГОСА 26.06.2026, реестр 07.07.2026, выплата до 21.07/11.08.2026 (2П)")
R("  финал 2025 (60,425 с невостребованными) уже в стартовом долге модели (ND якоря = ЧД + дивиденды к выплате); "
  "новых решений на 28.09.2026 нет; следующая выплата при повторе цикла — за 9М 2026 в январе 2027 г.")
R("  КНИГА dividends_from = \"2027H1\": во 2П2026 новых выплат нет; дальше — по полугодию (январь — за 9 мес., июль — "
  "финал), как по политике «дважды в год»")
out["dividends_from"] = "2027H1"

R.h("5. Предел рычага пути (гейт leverage_path)")
cov = FX["covenant_nd_ebitda"]
R(f"  ковенант: ЧД / EBITDA до МСФО 16 ≤ {v(cov):.2f}× ({cov['src']}); исторический максимум кварталов "
  f"{max(old_q):.2f}× (2К2011)")
R("  КНИГА checks.max_leverage = 4.0: путь выше ковенанта без реструктуризации невозможен")
out["max_leverage"] = {"covenant": v(cov), "hist_max_quarter": r4(max(old_q)), "book": 4.0}

write_json("leverage_out.json", out)
R.save()
