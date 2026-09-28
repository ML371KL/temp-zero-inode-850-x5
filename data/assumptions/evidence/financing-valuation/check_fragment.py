"""Сверка фрагмента fragments/financing-valuation.yaml с выходами листа и схемой книги.

1) ключи фрагмента — только ключи области; 2) числа фрагмента = выходы скриптов листа (*_out.json);
3) итоговые списки осей полосы и обратного DCF покрывают предложения всех фрагментов и оси черновика;
4) черновик + все фрагменты проходят закрытую схему ядра (model/book_schema.py::validate_book).
Нужен PyYAML и код ядра репозитория. Код выхода 1 — расхождение.
"""
from __future__ import annotations

import json
import sys

import yaml

from book_merge import build_book
from common import HERE, REPO

sys.path.insert(0, str(REPO))
from model.book_schema import BookError, validate_book  # noqa: E402

ASSUME = REPO / "data" / "assumptions"
FRAG = yaml.safe_load((ASSUME / "fragments" / "financing-valuation.yaml").read_text(encoding="utf-8"))
bad: list[str] = []


def out(name: str) -> dict:
    return json.loads((HERE / f"{name}_out.json").read_text(encoding="utf-8"))


def eq(label: str, a, b) -> None:
    if a != b:
        bad.append(f"{label}: фрагмент {a!r} ≠ лист {b!r}")


# 1. ключи области
AREA = {"joint": {"world_prob", "market_implied_prob", "neutral_world", "lambda", "world_links"},
        "financing": {"fixed_share", "legacy_rate", "legacy_weight", "spread_float", "spread_fixed",
                      "cash_buffer_pct", "cash_yield_k", "target_leverage", "dividends_from"},
        "bridge": {"include"},
        "valuation": {"beta_u", "erp", "governance_discount", "headline", "uncertainty", "reverse_dcf"},
        "checks": {"ev_ebitda", "margin_range", "capex_range", "terminal_share", "real_rate", "max_leverage",
                   "min_v0_to_d", "book_update"}}
for block, keys in FRAG.items():
    if block not in AREA:
        bad.append(f"блок {block} — не область financing-valuation")
        continue
    extra = set(keys) - AREA[block]
    if extra:
        bad.append(f"{block}: ключи вне области {sorted(extra)}")
for w, link in FRAG["joint"]["world_links"].items():
    if set(link) != {"credit"}:
        bad.append(f"joint.world_links.{w}: только credit (рост — область network-revenue)")

# 2. числа = выходы листа
R_, L_, B_, V_, G_ = out("rates"), out("leverage"), out("bridge"), out("valuation"), out("gates")
F = FRAG["financing"]
eq("fixed_share", F["fixed_share"], R_["fixed_share"]["book"])
eq("legacy_weight", F["legacy_weight"], R_["legacy"]["book_weight"])
eq("legacy_rate", F["legacy_rate"], R_["legacy"]["book_rate"])
eq("spread_float", F["spread_float"], R_["spreads"]["book"]["spread_float"])
eq("spread_fixed", F["spread_fixed"], R_["spreads"]["book"]["spread_fixed"])
eq("cash_yield_k", F["cash_yield_k"], R_["cash_yield"]["book"])
eq("target_leverage", F["target_leverage"], L_["target_leverage"]["book"])
eq("dividends_from", F["dividends_from"], L_["dividends_from"])
eq("bridge.include", FRAG["bridge"]["include"], B_["include"])
V = FRAG["valuation"]
eq("beta_u", V["beta_u"], V_["beta"]["book"])
eq("erp", V["erp"], V_["erp"]["center"])
eq("governance_discount", V["governance_discount"], V_["governance"]["book"])
eq("print_step", V["headline"]["print_step"], V_["headline"]["print_step"])
eq("draws", V["uncertainty"]["draws"], V_["headline"]["draws"])
eq("seed", V["uncertainty"]["seed"], V_["headline"]["seed"])
eq("subsample", V["reverse_dcf"]["subsample"], V_["headline"]["subsample"])
axes = {a["name"]: a for a in V["uncertainty"]["axes"]}
eq("ось β_u", [axes["Бета активов β_u"]["low"], axes["Бета активов β_u"]["high"]], V_["beta"]["axis"])
eq("ось ERP", [axes["Премия за риск ERP"]["low"], axes["Премия за риск ERP"]["high"]], V_["erp"]["axis"])
eq("ось дисконта", [axes["Дисконт за управление"]["low"], axes["Дисконт за управление"]["high"]],
   V_["governance"]["axis"])
eq("ось рычага", [axes["Целевой рычаг"]["low"], axes["Целевой рычаг"]["high"]], L_["target_leverage"]["axis"])
C = FRAG["checks"]
eq("ev_ebitda", C["ev_ebitda"], G_["ev_ebitda"]["book"])
eq("margin_range", C["margin_range"], G_["margin_range"]["book"])
eq("capex_range", C["capex_range"], G_["capex_range"]["book"])
eq("terminal_share", C["terminal_share"], G_["terminal_share"]["book"])
eq("real_rate", C["real_rate"], G_["real_rate"]["book"])
eq("max_leverage", C["max_leverage"], G_["other"]["max_leverage"])
eq("min_v0_to_d", C["min_v0_to_d"], G_["other"]["min_v0_to_d"])
eq("book_update", C["book_update"], G_["other"]["book_update"])
mag = json.loads((HERE / "inputs" / "magnit_book.json").read_text(encoding="utf-8"))
for k in ("world_prob", "market_implied_prob", "neutral_world", "lambda"):
    eq(f"joint.{k} = книга Магнита", FRAG["joint"][k], mag["joint"][k])
m_axes = mag["axes"]
eq("ось весов миров = Магнит", [axes["Веса миров"]["low"], axes["Веса миров"]["high"]],
   [m_axes["Веса миров"]["low"], m_axes["Веса миров"]["high"]])
mi = m_axes["Инфляция мира M (дата, ликвидность ОФЗ-ИН)"]
eq("ось инфляции M = Магнит", [axes["Инфляция мира M"]["low"], axes["Инфляция мира M"]["high"]],
   [mi["shift_low"], mi["shift_high"]])

# 3. покрытие предложений других областей и осей черновика
draft = yaml.safe_load((ASSUME / "assumptions.draft.yaml").read_text(encoding="utf-8"))
for path in sorted((ASSUME / "fragments").glob("*.yaml")):
    if path.name == "financing-valuation.yaml":
        continue
    other = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for prop in other.get("axes_proposals", []):
        mine = axes.get(prop["name"])
        if mine is None:
            bad.append(f"ось «{prop['name']}» ({path.name}) не в итоговом списке")
        elif {k: mine[k] for k in prop} != prop:
            bad.append(f"ось «{prop['name']}» ({path.name}) изменена")
    rev = {a["name"]: a for a in V["reverse_dcf"]["axes"]}
    for prop in other.get("reverse_dcf_proposals", []):
        mine = rev.get(prop["name"])
        if mine is None:
            bad.append(f"обратный DCF «{prop['name']}» ({path.name}) не в итоговом списке")
        else:
            diff = [k for k in prop if mine.get(k) != prop[k] and k != "unit"]
            if diff:
                bad.append(f"обратный DCF «{prop['name']}» ({path.name}): отличаются {diff}")
covered = {tuple(a["paths"]) for a in V["uncertainty"]["axes"]}
for a in draft["valuation"]["uncertainty"]["axes"]:
    hits = [p for p in a["paths"] if any(p == q or q.startswith(p + ".") or p.startswith(q + ".")
                                         for c in covered for q in c)]
    if not hits:
        bad.append(f"ось черновика «{a['name']}» не покрыта итоговым списком")

# 4. схема на собранной книге
A, _ = build_book()
try:
    validate_book(A)
    print("схема: черновик + все фрагменты читаются ядром")
except BookError as e:
    bad.append("схема: " + str(e).replace("\n", "; "))

if bad:
    print("РАСХОЖДЕНИЯ:")
    for line in bad:
        print("  " + line)
    sys.exit(1)
print(f"фрагмент = выходы листа; осей полосы {len(V['uncertainty']['axes'])}, обратного DCF "
      f"{len(V['reverse_dcf']['axes'])}; предложения всех областей учтены")
