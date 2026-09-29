"""Проверка фрагмента fragment.yaml (выход листа network-revenue) против листа (выходы скриптов) и схемы черновика.

Проверяет: только ключи области (joint.world_links[*].growth, joint.stress_growth, network.*, revenue.*,
axes_proposals, reverse_dcf_proposals); те же тарифы, состояния спроса и набор ключей блоков, что в черновике;
траектории = выходы network.py, ticket.py, other.py, vat.py; d, μ, κ = density.py; ключ полугодия на
последнем периоде запрещён (MODEL §0.1); средневзвешенное сдвига и трафика по состояниям = центр листа
(до 0,001 п.п.); книга (assumptions.yaml) несёт значения фрагмента (траектории — равны по всем полугодиям
и LT, оси — те же) и проходит закрытую схему ядра (validate_book).

Запуск: python -B check_fragment.py (нужен PyYAML и пакет model/ репозитория).
"""
from __future__ import annotations

import json
import sys

import yaml

from common import HERE, path_value

ASSUMPTIONS = HERE.parents[1]
FRAG = HERE / "fragment.yaml"
DRAFT = ASSUMPTIONS / "assumptions.draft.yaml"
BOOK = ASSUMPTIONS / "assumptions.yaml"
ALLOWED = {"joint": {"world_links", "stress_growth"},
           "network": {"net_growth", "close_rate", "maturity_curve", "new_space_density", "closed_productivity"},
           "revenue": {"ticket_k", "ticket_shift", "traffic", "other_growth", "vat_effect", "homogeneity"},
           "axes_proposals": None, "reverse_dcf_proposals": None}


def out(name: str) -> dict:
    return json.loads((HERE / f"{name}_out.json").read_text(encoding="utf-8"))


def main() -> int:
    fr = yaml.safe_load(FRAG.read_text(encoding="utf-8"))
    dr = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
    errs: list[str] = []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    for k, x in fr.items():
        need(k in ALLOWED, f"чужой ключ верхнего уровня: {k}")
        if k in ALLOWED and ALLOWED[k] is not None:
            need(set(x) <= ALLOWED[k], f"{k}: чужие ключи {set(x) - ALLOWED[k]}")
    for W, link in fr["joint"]["world_links"].items():
        need(W in dr["joint"]["world_links"] and set(link) == {"growth"}, f"world_links.{W}: только ключ growth")
        need(link["growth"] in dr["network"]["net_growth"], f"world_links.{W}.growth — не тариф")
    need(fr["joint"]["stress_growth"] in dr["network"]["net_growth"], "stress_growth — не тариф")
    need(set(fr["network"]) == set(dr["network"]), "network: набор ключей не как в черновике")
    need(set(fr["revenue"]) == set(dr["revenue"]), "revenue: набор ключей не как в черновике")
    need(set(fr["network"]["net_growth"]) == set(dr["network"]["net_growth"]), "тарифы не как в черновике")
    for blk in ("ticket_k", "ticket_shift", "traffic"):
        need(set(fr["revenue"][blk]) == set(dr["revenue"][blk]), f"revenue.{blk}: состояния спроса не как в черновике")
    need(set(fr["revenue"]["homogeneity"]) == set(dr["revenue"]["homogeneity"]), "homogeneity: ключи")
    last = dr["meta"]["last_period"]

    def trajs():
        for t, s in fr["network"]["net_growth"].items():
            yield f"network.net_growth.{t}", s
        yield "network.close_rate", fr["network"]["close_rate"]
        for blk in ("ticket_shift", "traffic"):
            for st, s in fr["revenue"][blk].items():
                yield f"revenue.{blk}.{st}", s
        yield "revenue.other_growth", fr["revenue"]["other_growth"]
        yield "revenue.vat_effect", fr["revenue"]["vat_effect"]

    for where, spec in trajs():
        need(last not in spec, f"{where}: ключ последнего периода {last} запрещён")
    for t, s in fr["network"]["net_growth"].items():
        need(s["LT"] == 0 and s["LT_from"] == int(last[:4]), f"net_growth.{t}: терминал — ноль с {last[:4]}")

    nw, de, tk, vt, ot = (out(n) for n in ("network", "density", "ticket", "vat", "other"))
    need(fr["network"]["net_growth"] == nw["net_growth"], "net_growth ≠ network.py")
    need(fr["network"]["close_rate"] == nw["close_rate"], "close_rate ≠ network.py")
    need(fr["network"]["maturity_curve"] == de["mu_book"], "maturity_curve ≠ density.py")
    need(fr["network"]["new_space_density"] == de["d_book"], "new_space_density ≠ density.py")
    d_ax = next((a for a in fr["axes_proposals"] if a["paths"] == ["network.new_space_density"]), None)
    need(d_ax is not None and [d_ax["low"], d_ax["high"]] == de["d_axis"], "ось d ≠ density.py (d ± 0,10)")
    need(fr["network"]["closed_productivity"] == de["kappa_book"], "closed_productivity ≠ density.py")
    need(fr["revenue"]["ticket_k"] == tk["ticket_k"], "ticket_k ≠ ticket.py")
    need(fr["revenue"]["ticket_shift"] == tk["ticket_shift"], "ticket_shift ≠ ticket.py")
    need(fr["revenue"]["traffic"] == tk["traffic"], "traffic ≠ ticket.py")
    need(fr["revenue"]["other_growth"] == ot["other_growth"], "other_growth ≠ other.py")
    need(fr["revenue"]["vat_effect"] == {"2026H2": vt["vat_effect_book"], "LT": 0.0}, "vat_effect ≠ vat.py")
    w = tk["state_weights"]
    for blk, center in (("ticket_shift", tk["center_shift"]), ("traffic", tk["center_traffic"])):
        for y in range(2026, 2037):
            p = f"{y}H2"
            e = sum(w[s] * path_value(fr["revenue"][blk][s], p) for s in w)
            need(abs(e - path_value(center, p)) < 1e-5, f"{blk} {p}: средневзвешенное {e:.5f} ≠ центр {path_value(center, p):.5f}")
    for ax in fr["axes_proposals"]:
        need({"name", "kind", "paths", "low", "high"} == set(ax), f"ось {ax.get('name')}: ключи")
    for ax in fr["reverse_dcf_proposals"]:
        need({"name", "kind", "paths", "search", "range", "unit"} == set(ax), f"обратный DCF {ax.get('name')}: ключи")

    # книга несёт фрагмент: траектории равны по всем полугодиям и LT, прочие ключи — буквально, оси — те же
    bk = yaml.safe_load(BOOK.read_text(encoding="utf-8"))
    periods = [f"{y}H{h}" for y in range(2026, int(last[:4]) + 1) for h in (1, 2) if f"{y}H{h}" >= dr["meta"]["first_period"]]

    def same_traj(a, b):
        return all(abs(path_value(a, p) - path_value(b, p)) < 1e-12 for p in periods) and a.get("LT") == b.get("LT")

    for where, spec in trajs():
        blk, key, *rest = where.split(".")
        ref = bk[blk][key] if not rest else bk[blk][key][rest[0]]
        need(same_traj(spec, ref), f"{where}: фрагмент ≠ книга")
    for k in ("maturity_curve", "new_space_density", "closed_productivity"):
        need(fr["network"][k] == bk["network"][k], f"network.{k}: фрагмент ≠ книга")
    for k in ("ticket_k", "homogeneity"):
        need(fr["revenue"][k] == bk["revenue"][k], f"revenue.{k}: фрагмент ≠ книга")
    for W, link in fr["joint"]["world_links"].items():
        need(link["growth"] == bk["joint"]["world_links"][W]["growth"], f"world_links.{W}.growth: фрагмент ≠ книга")
    need(fr["joint"]["stress_growth"] == bk["joint"]["stress_growth"], "stress_growth: фрагмент ≠ книга")
    book_axes = {a["name"]: a for a in bk["valuation"]["uncertainty"]["axes"]}
    for ax in fr["axes_proposals"]:
        need(book_axes.get(ax["name"]) == ax, f"ось «{ax['name']}»: фрагмент ≠ книга")
    book_raxes = {a["name"]: a for a in bk["valuation"]["reverse_dcf"]["axes"]}
    for ax in fr["reverse_dcf_proposals"]:
        need(book_raxes.get(ax["name"]) == ax, f"обратный DCF «{ax['name']}»: фрагмент ≠ книга")
    try:
        from check_2026h2 import build_book
        from model.book_schema import validate_book
        validate_book(build_book(True))
        validate_book(build_book(False))
    except Exception as exc:  # noqa: BLE001 — печатаем причину отказа схемы
        errs.append(f"схема ядра: {exc}")

    if errs:
        print("ОШИБКИ:\n  " + "\n  ".join(errs))
        return 1
    print(f"фрагмент согласован с листом, книгой и схемой: тарифы {list(nw['net_growth'])}, d {de['d_book']}, "
          f"k {tk['k_book']}, НДС {vt['vat_effect_book']}, осей {len(fr['axes_proposals'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
