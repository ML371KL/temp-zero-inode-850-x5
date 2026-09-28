"""Проверка фрагмента книги fragments/margin.yaml против листа (выходы скриптов) и схемы черновика.

Проверяет: только ключи области; те же имена режимов и ключи траекторий, что в assumptions.draft.yaml;
набор ключей joint.regime_update — как в книге (assumptions.yaml, закрытая схема: sigma_pp, cap_pp,
observations; ρ правила A-P2u — единственный ключ margin.deviation_persistence); вероятности = выход
refclass.py и в сумме 1; траектории = выход paths.py; ρ = оценке series.py (шаг 0,05); σ правила = series.py
(округление 0,01 п.п.); LTI = lti.py; demo_values = next_report.py; цели внутри коридора checks.margin_range;
ключ полугодия на последнем периоде запрещён (MODEL §0.1); значения фрагмента = значениям книги
(assumptions.yaml). Печатает E[m_LT] и ожидание 2П2026.

Запуск: python -B check_fragment.py (нужен PyYAML).
"""
from __future__ import annotations

import json
import math
import sys

import yaml

from common import HERE, REGIMES

REPO_ASSUMPTIONS = HERE.parents[1]
FRAG = REPO_ASSUMPTIONS / "fragments" / "margin.yaml"
DRAFT = REPO_ASSUMPTIONS / "assumptions.draft.yaml"
BOOK = REPO_ASSUMPTIONS / "assumptions.yaml"
ALLOWED = {"joint": {"regime_prob", "regime_demand", "regime_update"},
           "margin": {"targets", "seasonal_h1_pp", "deviation_persistence", "lti_pct"},
           "valuation": {"next_report"}, "axes_proposals": None, "reverse_dcf_proposals": None}


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def main():
    fr = yaml.safe_load(FRAG.read_text(encoding="utf-8"))
    dr = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    bk = yaml.safe_load(BOOK.read_text(encoding="utf-8"))
    errs = []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    for k, v in fr.items():
        need(k in ALLOWED, f"чужой ключ верхнего уровня: {k}")
        if k in ALLOWED and ALLOWED[k] is not None:
            need(set(v) <= ALLOWED[k], f"{k}: чужие ключи {set(v) - ALLOWED[k]}")
    # структура против черновика
    need(set(fr["joint"]["regime_update"]) == set(bk["joint"]["regime_update"]), "regime_update: набор ключей не как в книге")
    need(set(fr["valuation"]["next_report"]) == set(dr["valuation"]["next_report"]), "next_report: набор ключей не как в черновике")
    for r in REGIMES:
        need(set(fr["margin"]["targets"][r]) == set(dr["margin"]["targets"][r]), f"targets.{r}: ключи не как в черновике")
    need(set(fr["joint"]["regime_prob"]) == set(REGIMES), "regime_prob: режимы")
    need(set(fr["joint"]["regime_demand"]) == set(REGIMES), "regime_demand: режимы")
    need(set(fr["joint"]["regime_demand"].values()) <= set(dr["revenue"]["ticket_k"]), "regime_demand: состояния спроса не из revenue.ticket_k")
    last = dr["meta"]["last_period"]
    for r in REGIMES:
        need(last not in fr["margin"]["targets"][r], f"targets.{r}: ключ последнего периода {last} запрещён")
    lo, hi = dr["checks"]["margin_range"]
    for r in REGIMES:
        for key, v in fr["margin"]["targets"][r].items():
            if key != "LT_from":
                need(lo <= v <= hi, f"targets.{r}.{key} = {v} вне checks.margin_range")

    # числа против листа
    rc, pa, ser, lti, nr = (load(n) for n in ("refclass_out.json", "paths_out.json", "series_out.json", "lti_out.json", "next_report_out.json"))
    p = fr["joint"]["regime_prob"]
    need(abs(sum(p.values()) - 1) < 1e-12, f"regime_prob: сумма {sum(p.values())}")
    for r in REGIMES:
        need(abs(p[r] - rc["prob_book"][r]) < 1e-9, f"regime_prob.{r} {p[r]} ≠ refclass {rc['prob_book'][r]}")
        for key, v in pa["targets"][r].items():
            need(abs(fr["margin"]["targets"][r][key] - v) < 1e-12, f"targets.{r}.{key} {fr['margin']['targets'][r][key]} ≠ paths {v}")
    ru = fr["joint"]["regime_update"]
    rho = fr["margin"]["deviation_persistence"]
    need(rho == pa["rho"], "ρ: deviation_persistence и paths.py расходятся")
    need(abs(ru["sigma_pp"] - round(ser["rule"]["sigma_rule_pp"] / 100, 4)) < 1e-12, f"sigma_pp {ru['sigma_pp']} ≠ series {ser['rule']['sigma_rule_pp']:.3f} п.п.")
    need(abs(round(ser["base"]["rho"] * 20) / 20 - pa["rho"]) < 1e-9, "ρ книги ≠ оценке series.py")
    need(abs(fr["margin"]["lti_pct"] - lti["book"]) < 1e-12, "lti_pct ≠ lti.py")
    need(fr["valuation"]["next_report"]["demo_values"] == nr["demo_values"], "demo_values ≠ next_report.py")
    need(fr["valuation"]["next_report"]["period"] == dr["meta"]["first_period"], "next_report.period ≠ meta.first_period")
    need(ru["observations"] == [], "observations: ожидается пусто до первого факта после якоря")
    ax = {a["name"]: a for a in fr["axes_proposals"]}
    lt_axis = ax["Долгосрочный уровень маржи (сдвиг целей LT всех режимов)"]
    need(abs(lt_axis["low"] * 100 - rc["axis"]["low_pp"]) < 1e-9 and abs(lt_axis["high"] * 100 - rc["axis"]["high_pp"]) < 1e-9, "ось LT ≠ refclass")
    need(ax["LTI"]["low"] == lti["axis"][0] and ax["LTI"]["high"] == lti["axis"][1], "ось LTI ≠ lti.py")

    # книга несёт фрагмент: те же значения ключей области в assumptions.yaml
    for blk, keys in (("joint", ("regime_prob", "regime_demand", "regime_update")),
                      ("margin", ("targets", "seasonal_h1_pp", "deviation_persistence", "lti_pct")),
                      ("valuation", ("next_report",))):
        for k in keys:
            need(fr[blk][k] == bk[blk][k], f"{blk}.{k}: фрагмент ≠ книга (assumptions.yaml)")
    book_axes = {a["name"]: a for a in bk["valuation"]["uncertainty"]["axes"]}
    for a in fr["axes_proposals"]:
        need(book_axes.get(a["name"]) == a, f"ось «{a['name']}»: фрагмент ≠ книга")

    e_lt = sum(p[r] * fr["margin"]["targets"][r]["LT"] for r in REGIMES)
    e_h2 = sum(p[r] * fr["margin"]["targets"][r]["2026H2"] for r in REGIMES) + rho * pa["dev_anchor"]
    print(f"E[m_LT] = {e_lt*100:.3f} %; ожидание 2П2026 = {e_h2*100:.3f} %; ρ = {rho}, σ = {ru['sigma_pp']*100:.2f} п.п.")
    if errs:
        print("ОШИБКИ:\n  " + "\n  ".join(errs))
        sys.exit(1)
    print("Фрагмент согласован с листом, схемой черновика и книгой (assumptions.yaml).")


if __name__ == "__main__":
    main()
