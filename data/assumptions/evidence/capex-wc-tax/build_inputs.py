"""Сборка малых входов листа capex-wc-tax из фактов исследования X1 и файлов первички.

Запуск нужен только для пересборки `inputs/facts_extract.json`, `inputs/cpi.json`,
`inputs/cf_investing.json` и `inputs/book_refs.json`; расчёты листа (`capex.py`,
`wc_cash_lease.py`, `tax.py`) читают только `inputs/`.

    python -B build_inputs.py [--handoff ../x5-850-handoff]   # по умолчанию — папка рядом с репозиторием
    python -B build_inputs.py --book-refs                      # только снимок книги (inputs/book_refs.json)

Источники (вне репозитория):
* research/facts/history_halfyear.json, history_annual.json, balance_points.json,
  operating_by_format.json — факты X1 (каждое значение со ссылкой на ячейку databook);
* reference/primary/financial_and_operating_results_q1_2024.xlsx — старый databook
  (capex и выручка 2011–2017 для подбора срока амортизации);
* reference/primary/rosstat_ipc_mes_08-2026.xlsx — месячный ИПЦ Росстата;
* reference/primary/financial_and_operating_results_q2_2026.xlsx — databook 2К2026: строки ОДДС до
  МСФО 16 о выбытии ОС и прочих инвестиционных платежах, прочие внеоборотные активы.
Снимок книги (`inputs/book_refs.json`) — из data/assumptions/assumptions.yaml репозитория (нужен PyYAML).
Деньги переводятся из млн ₽ в млрд ₽; ссылки `src` сохраняются как есть.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "inputs"
REPO = HERE.parents[3]
BOOK = REPO / "data" / "assumptions" / "assumptions.yaml"

HALF_KEYS = ["revenue", "adj_ebitda", "ebitda", "lti_and_mgmt_remuneration", "da_ias17",
             "capex_ppe", "capex_intangibles", "capex_total", "acquisitions",
             "ppe_disposal_proceeds", "pbt", "income_tax", "pbt_ifrs16", "income_tax_ifrs16",
             "wc_change", "d_receivables", "d_inventories", "d_trade_payables",
             "d_other_payables", "income_tax_paid", "recon_fixed_rent",
             "recon_rou_derecognition_gain", "lease_principal_paid_ifrs16", "lease_interest_paid"]
BAL_KEYS = ["inventories", "trade_other_receivables_advances", "vat_other_taxes_receivable",
            "income_tax_receivable", "trade_payables", "contract_liabilities_st",
            "provisions_other_liabilities", "dividends_payable", "income_tax_payable",
            "cash", "st_financial_investments", "revenue_ltm", "nwc_trade",
            "other_nc_liabilities", "net_debt_ias17"]
SPACE_KEYS = ["space_pyaterochka", "space_perekrestok", "space_chizhik", "space_total",
              "stores_pyaterochka", "stores_perekrestok", "stores_chizhik", "stores_total"]
MONEY_NO_SCALE = {"revenue_ltm"}  # все денежные поля — млн ₽, переводятся ниже


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def conv(item, scale: float):
    """{'v': млн, 'src': ...} → {'v': млрд, 'src'/'calc': ...}; null остаётся null."""
    if not isinstance(item, dict) or item.get("v") is None:
        return None
    out = {"v": round(float(item["v"]) * scale, 6)}
    for k in ("src", "calc"):
        if k in item:
            out[k] = item[k]
    return out


def build_facts(handoff: Path) -> dict:
    fdir = handoff / "research" / "facts"
    hy = json.loads((fdir / "history_halfyear.json").read_text(encoding="utf-8"))["data"]
    an = json.loads((fdir / "history_annual.json").read_text(encoding="utf-8"))["data"]
    bp = json.loads((fdir / "balance_points.json").read_text(encoding="utf-8"))["data"]
    ob = json.loads((fdir / "operating_by_format.json").read_text(encoding="utf-8"))["data"]
    out = {"units": "млрд ₽ (деньги), тыс. м² (площадь), шт. (магазины)",
           "source_files": {n: sha256(fdir / n) for n in (
               "history_halfyear.json", "history_annual.json", "balance_points.json",
               "operating_by_format.json")},
           "halfyear": {}, "annual": {}, "balance": {}, "network_q": {}}
    for p, row in hy.items():
        out["halfyear"][p] = {k: conv(row.get(k), 1e-3) for k in HALF_KEYS if k in row}
    for y, row in an.items():
        out["annual"][y.replace("FY", "")] = {k: conv(row.get(k), 1e-3) for k in HALF_KEYS
                                            if k in row}
    for d, row in bp.items():
        if d < "2019-06-30":
            continue
        out["balance"][d] = {k: conv(row.get(k), 1e-3) for k in BAL_KEYS if k in row}
    for q, bases in ob["quarterly"].items():
        if q < "2023Q4":
            continue
        out["network_q"][q] = {}
        for basis, row in bases.items():
            vals = {}
            for k in SPACE_KEYS:
                if k in row and isinstance(row[k], dict) and row[k].get("v") is not None:
                    vals[k] = {"v": row[k]["v"], "src": row[k].get("src")}
            out["network_q"][q][basis] = vals
    return out


def build_old_databook(handoff: Path) -> dict:
    import openpyxl  # только для пересборки
    path = handoff / "reference" / "primary" / "financial_and_operating_results_q1_2024.xlsx"
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    cf = list(wb["Cash Flow"].iter_rows(min_row=1, max_row=45, values_only=True))
    pl = list(wb["Profit and Loss"].iter_rows(min_row=1, max_row=8, values_only=True))
    cols = "DEFGHIJ"
    res = {"file": path.name, "sha256": sha256(path), "years": {}}
    for i, y in enumerate(range(2011, 2018)):
        c = 3 + i
        res["years"][str(y)] = {
            "revenue": {"v": round(pl[5][c] / 1e3, 6), "src": f"{path.name} › Profit and Loss!{cols[i]}6"},
            "capex_ppe": {"v": round(cf[32][c] / 1e3, 6), "src": f"{path.name} › Cash Flow!{cols[i]}33"},
            "capex_intangibles": {"v": round(cf[41][c] / 1e3, 6),
                                  "src": f"{path.name} › Cash Flow!{cols[i]}42"},
            "ppe_disposal_proceeds": {"v": round(cf[39][c] / 1e3, 6),
                                      "src": f"{path.name} › Cash Flow!{cols[i]}40"},
        }
    return res


def build_cpi(handoff: Path) -> dict:
    import openpyxl
    path = handoff / "reference" / "primary" / "rosstat_ipc_mes_08-2026.xlsx"
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["01"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[3]
    col = {v: i for i, v in enumerate(hdr) if isinstance(v, int) and v >= 2014}
    months = {}
    for m in range(12):
        r = rows[5 + m]
        for y, c in col.items():
            if isinstance(r[c], (int, float)):
                months[f"{y}-{m + 1:02d}"] = r[c]
    return {"file": path.name, "sha256": sha256(path),
            "src": f"{path.name} › лист 01, строки «к концу предыдущего месяца» (%, м/м)",
            "mom_pct": dict(sorted(months.items()))}


# строки ОДДС databook 2К2026 (до МСФО 16, «IAS 17»): номер строки → ключ
CF_ROWS = {9: "ppe_disposal_gain_noncash", 35: "ppe_disposal_proceeds",
           41: "finance_lease_principal_receipts", 42: "other_investing_payments"}


def _col(i: int) -> str:
    """Индекс столбца (0 = A) → буквы Excel."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def build_cf_investing(handoff: Path) -> dict:
    """Выбытие ОС (поступления и прибыль), прочие инвестиционные платежи и поступления
    по чистым инвестициям в аренду — годы 2021–2025 и кварталы 2023–2026 (до МСФО 16);
    прочие внеоборотные активы на отчётные даты. Деньги — млрд ₽; знак — как в ОДДС."""
    import openpyxl
    path = handoff / "reference" / "primary" / "financial_and_operating_results_q2_2026.xlsx"
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    cf = list(wb["Cash Flow"].iter_rows(min_row=1, max_row=45, values_only=True))
    basis, hdr = cf[3], cf[4]
    cols = {}
    for i, h in enumerate(hdr):
        if basis[i] != "IAS 17" or h is None:
            continue
        if isinstance(h, int):
            cols[str(h)] = i
        else:                                            # «1 КВ. 2026» → 2026Q1
            q, _, y = str(h).split()
            cols[f"{y}Q{q}"] = i
    out = {"file": path.name, "sha256": sha256(path), "units": "млрд ₽; знак ОДДС",
           "labels": {k: str(cf[r - 1][1]).strip() for r, k in CF_ROWS.items()},
           "cash_flow": {}, "other_noncurrent_assets": {}}
    for per, i in sorted(cols.items()):
        row = {}
        for r, k in CF_ROWS.items():
            val = cf[r - 1][i]
            if isinstance(val, (int, float)):
                row[k] = {"v": round(val / 1e3, 6), "src": f"{path.name} › Cash Flow!{_col(i)}{r}"}
        out["cash_flow"][per] = row
    fp = list(wb["Financial Position"].iter_rows(min_row=1, max_row=15, values_only=True))
    for i, d in enumerate(fp[4]):
        if hasattr(d, "date") and isinstance(fp[13][i], (int, float)):
            out["other_noncurrent_assets"][d.date().isoformat()] = {
                "v": round(fp[13][i] / 1e3, 6), "src": f"{path.name} › Financial Position!{_col(i)}14"}
    out["other_noncurrent_assets"] = dict(sorted(out["other_noncurrent_assets"].items()))
    return out


def build_book_refs() -> dict:
    """Снимок чужих ключей итоговой книги для проверок листа (раздел 9 и 12 capex.py)."""
    import yaml
    A = yaml.safe_load(BOOK.read_text(encoding="utf-8"))
    N, J = A["network"], A["joint"]
    return {"_about": "Снимок ключей итоговой книги (чужие области), которыми лист пользуется только "
                      "для проверок: рост и закрытия сети, вероятности режимов и уровней capex, веса "
                      "миров, ИПЦ миров в 2П2026 (индекс цен первого прогнозного полугодия).",
            "file": "data/assumptions/assumptions.yaml", "version": A["meta"]["version"],
            "sha256": sha256(BOOK),
            "network": {"net_growth": N["net_growth"], "close_rate": N["close_rate"]},
            "regime_prob": J["regime_prob"], "capex_prob_given_regime": J["capex_prob_given_regime"],
            "world_prob": J["world_prob"],
            "cpi_2026H2": {w: A["worlds"][w]["cpi"]["2026H2"] for w in ("N", "H", "M")}}


def write_json(name: str, obj: dict) -> None:
    # перевод строки \n на любой ОС — файл побайтно одинаков на Windows и Linux
    with open(OUT / name, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handoff", default=str(REPO.parent / "x5-850-handoff"),
                    help="папка передачи (по умолчанию — рядом с репозиторием)")
    ap.add_argument("--book-refs", action="store_true", help="только снимок книги")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    write_json("book_refs.json", build_book_refs())
    if a.book_refs:
        print("inputs/book_refs.json — готово")
        return
    h = Path(a.handoff)
    facts = build_facts(h)
    facts["old_databook_2011_2017"] = build_old_databook(h)
    for name, obj in (("facts_extract.json", facts), ("cpi.json", build_cpi(h)),
                      ("cf_investing.json", build_cf_investing(h))):
        write_json(name, obj)
    print("inputs/facts_extract.json, cpi.json, cf_investing.json, book_refs.json — готово")


if __name__ == "__main__":
    main()
