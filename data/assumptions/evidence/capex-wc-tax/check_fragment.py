"""Проверка фрагмента fragment.yaml (выход листа capex-wc-tax) против выходов листа, книги и черновика.

Проверяет: только ключи своих областей (capex, joint.capex_prob_given_regime, working_capital, tax и предложение
financing.cash_buffer_pct); числа = выходы скриптов листа (capex_out.json, wc_cash_lease_out.json, tax_out.json);
концы осей инфраструктуры и налоговых разниц = выходы листа; ключи траекторий — как в черновике; значения
фрагмента и оси = книга (assumptions.yaml). Сверка «черновик + все фрагменты = книга» и схема — в
evidence/financing-valuation/check_fragment.py.

Запуск: python -B check_fragment.py (нужен PyYAML). Код выхода 1 — расхождение.
"""
from __future__ import annotations

import json
import sys

import yaml

from common import HERE

ASSUMPTIONS = HERE.parents[1]
FRAG = HERE / "fragment.yaml"
DRAFT = ASSUMPTIONS / "assumptions.draft.yaml"
BOOK = ASSUMPTIONS / "assumptions.yaml"
ALLOWED = {"capex": {"maintenance", "maintenance_area_share", "price_per_m2", "infra_per_m2", "infra_from_year",
                     "asset_life_years", "disposal_proceeds_pct"},
           "joint": {"capex_prob_given_regime"},
           "working_capital": {"nwc_pct", "h1_excess_pct", "operating_cash_pct", "lease_adj_pct"},
           "tax": {"rate", "permanent_add_pct"},
           "financing": {"cash_buffer_pct"},
           "axes_proposals": None, "reverse_dcf_proposals": None}


def out(name: str) -> dict:
    return json.loads((HERE / f"{name}_out.json").read_text(encoding="utf-8"))


def main() -> int:
    fr = yaml.safe_load(FRAG.read_text(encoding="utf-8"))
    dr = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    bk = yaml.safe_load(BOOK.read_text(encoding="utf-8"))
    errs: list[str] = []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    for k, x in fr.items():
        need(k in ALLOWED, f"чужой ключ верхнего уровня: {k}")
        if k in ALLOWED and ALLOWED[k] is not None:
            need(set(x) <= ALLOWED[k], f"{k}: чужие ключи {set(x) - ALLOWED[k]}")

    cx, wc, tx = out("capex"), out("wc_cash_lease"), out("tax")
    C = fr["capex"]
    # числа = выходы листа
    need(C["maintenance"] == cx["paths"], f"capex.maintenance {C['maintenance']} ≠ capex.py {cx['paths']}")
    need(C["maintenance_area_share"] == cx["phi"], "maintenance_area_share ≠ capex.py (φ)")
    need(C["price_per_m2"] == round(cx["price_per_m2"]["center"], 4), "price_per_m2 ≠ capex.py (центр, 4 знака)")
    need(C["infra_per_m2"] == round(cx["infra_per_m2"]["values"]["base"], 4), "infra_per_m2 ≠ capex.py")
    need(C["asset_life_years"] == cx["asset_life"]["fit_unbiased_2023_2026"], "asset_life_years ≠ capex.py (несмещённый L)")
    need(C["disposal_proceeds_pct"] == cx["disposal_nbv"]["key"], "disposal_proceeds_pct ≠ capex.py")
    need(fr["joint"]["capex_prob_given_regime"] == cx["capex_prob"]["conditional"],
         "capex_prob_given_regime ≠ capex.py (раздел 12)")
    W = fr["working_capital"]
    need(W["nwc_pct"] == wc["nwc_pct"]["path"], "nwc_pct ≠ wc_cash_lease.py")
    need(W["h1_excess_pct"] == wc["nwc_pct"]["h1_excess_pct"], "h1_excess_pct ≠ wc_cash_lease.py")
    need(W["operating_cash_pct"] == wc["cash"]["operating_cash_pct"], "operating_cash_pct ≠ wc_cash_lease.py")
    need(W["lease_adj_pct"] == wc["lease_adj"]["book"], "lease_adj_pct ≠ wc_cash_lease.py")
    need(fr["financing"]["cash_buffer_pct"] == wc["cash"]["cash_buffer_pct"], "cash_buffer_pct ≠ wc_cash_lease.py")
    need(fr["tax"]["rate"] == tx["book"]["rate"], "tax.rate ≠ tax.py")
    need(fr["tax"]["permanent_add_pct"] == tx["book"]["permanent_add_pct"], "permanent_add_pct ≠ tax.py")
    ax = {a["name"]: a for a in fr["axes_proposals"]}
    infra = cx["infra_per_m2"]["values"]
    ia = ax["Инфраструктура на м² прироста, млрд ₽ на тыс. м²"]
    need([ia["low"], ia["high"]] == [round(infra["low"], 3), round(infra["high"], 3)], "ось инфраструктуры ≠ capex.py")
    ta = ax["Постоянные налоговые разницы"]
    need([ta["low"], ta["high"]] == tx["book"]["range"], "ось налоговых разниц ≠ tax.py")

    # структура против черновика
    for L in ("low", "base", "high"):
        need(set(C["maintenance"][L]) == set(dr["capex"]["maintenance"][L]),
             f"capex.maintenance.{L}: ключи не как в черновике")
    need(set(fr["working_capital"]) == set(dr["working_capital"]), "working_capital: набор ключей не как в черновике")

    # книга несёт фрагмент
    for blk in ("capex", "working_capital", "tax"):
        for k, x in fr[blk].items():
            need(x == bk[blk][k], f"{blk}.{k}: фрагмент ≠ книга (assumptions.yaml)")
    need(fr["joint"]["capex_prob_given_regime"] == bk["joint"]["capex_prob_given_regime"],
         "joint.capex_prob_given_regime: фрагмент ≠ книга")
    need(fr["financing"]["cash_buffer_pct"] == bk["financing"]["cash_buffer_pct"], "cash_buffer_pct: фрагмент ≠ книга")
    book_axes = {a["name"]: a for a in bk["valuation"]["uncertainty"]["axes"]}
    for a in fr["axes_proposals"]:
        need(book_axes.get(a["name"]) == a, f"ось «{a['name']}»: фрагмент ≠ книга")
    book_raxes = {a["name"]: a for a in bk["valuation"]["reverse_dcf"]["axes"]}
    for a in fr["reverse_dcf_proposals"]:
        mine = book_raxes.get(a["name"])
        need(mine is not None and all(mine.get(k) == a[k] for k in a if k != "unit"),
             f"обратный DCF «{a['name']}»: фрагмент ≠ книга")

    if errs:
        print("ОШИБКИ:\n  " + "\n  ".join(errs))
        return 1
    m = C["maintenance"]
    print(f"фрагмент согласован с листом и книгой: поддерживающий 2П2026–2027 {m['low']['2026H2']} / "
          f"{m['base']['2026H2']} / {m['high']['2026H2']}, LT {m['low']['LT']} / {m['base']['LT']} / {m['high']['LT']}; "
          f"φ {C['maintenance_area_share']}; осей {len(fr['axes_proposals'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
