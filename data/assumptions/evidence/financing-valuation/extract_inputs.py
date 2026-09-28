"""Сборка малых входов листа в inputs/ из первички и соседних каталогов (только при пересборке).

Нужны папка передачи `x5-850-handoff` (research/, reference/primary/), репозиторий Магнита
`magnit-850oa` (ветка v5, только чтение) и `.venv` с openpyxl (databook X5 — xlsx).
Сырые ответы MOEX ISS (КБД на даты размещений, описания и купоны погашенных выпусков,
веса IMOEX, уровень листинга) уже лежат в `inputs/web/`; страница Damodaran разбирается
с диска (`--damodaran ПУТЬ`), в репозиторий кладётся только выписка и sha256.

    python -B extract_inputs.py [--damodaran ctryprem.html] [--cbr2013 keyrate_2013_2023.html]

Анализ (`rates.py` и др.) читает только `inputs/` и работает без сети и без openpyxl.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from common import HERE, INPUTS, REPO

H = REPO.parent / "x5-850-handoff"
PRIM = H / "reference" / "primary"
MAG = REPO.parent / "magnit-850oa"
DATABOOK_NEW = PRIM / "financial_and_operating_results_q2_2026.xlsx"
DATABOOK_OLD = PRIM / "financial_and_operating_results_q1_2024.xlsx"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO.parent)).replace("\\", "/")
    except ValueError:
        return str(path)


def dump(name: str, obj) -> None:
    (INPUTS / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8", newline="\n")
    print("inputs/" + name)


def src_note(path: Path) -> dict:
    return {"file": rel(path), "sha256": sha(path)}


# ------------------------------------------------------------------ долг и ставки
def build_debt() -> None:
    reg = json.loads((H / "research/facts/debt_register.json").read_text(encoding="utf-8"))
    bonds = []
    for b in reg["bonds"]:
        c = b.get("coupon") or {}
        bonds.append({
            "series": b["series"], "isin": b["isin"], "issue_date": b.get("issue_date"),
            "type": b["coupon_type"], "coupon": (c["rate_pct"] / 100) if "rate_pct" in c else None,
            "spread": (c["spread_pp"] / 100) if "spread_pp" in c else None,
            "freq": b.get("coupon_frequency_per_year"),
            # номинал на 31.12.2025 = объём выпуска, если выпуск был в балансе на эту дату
            "face_2025_12_31": (round(b["issue_size_rub_bn"], 3)
                                if b.get("carrying_2025_12_31_rub_bn") is not None else 0.0),
            "face_2026_06_30": b.get("outstanding_2026_06_30_rub_bn") or 0.0,
            "face_2026_09_28": b.get("outstanding_2026_09_28_rub_bn") or 0.0,
            "exit_date": b.get("repayment_date_for_model"),
            "price_2026_09_28": (b.get("market_2026_09_28") or {}).get("price_last_pct"),
            "src": b.get("source")})
    # 003P-07: номинал после оферты 25.09.2025 ≈1,708 (оценка по балансу МСФО) — на 31.12.2025 тоже 1,708
    for b in bonds:
        if b["series"] == "003P-07":
            b["face_2025_12_31"] = 1.708
    # погашенные в 1П2026: купоны — MOEX ISS bondization (inputs/web), даты — оферты ISS
    web = INPUTS / "web"
    red = [
        {"series": "003P-03", "isin": "RU000A107AJ0", "issue_date": "2023-12-05", "type": "fixed",
         "coupon": 0.129, "spread": None, "freq": 4, "face_2025_12_31": 10.0, "face_2026_06_30": 0.0,
         "face_2026_09_28": 0.0, "exit_date": "2026-06-05",
         "src": "MOEX ISS bondization RU000A107AJ0 (купон 12,9 % с 04.03.2025; оферта 05.06.2026); "
                "МСФО 1П2026 прим. 15 (баланс 31.12.2025 9,995)"},
        {"series": "003P-08", "isin": "RU000A10AP21", "issue_date": "2025-01-27", "type": "fixed",
         "coupon": 0.215, "spread": None, "freq": 12, "face_2025_12_31": 20.0, "face_2026_06_30": 0.0,
         "face_2026_09_28": 0.0, "exit_date": "2026-02-27",
         "src": "МСФО 2025 прим. 21 («в январе … 003P-08 … 21,50 % с офертой в феврале 2026»); "
                "MOEX ISS bondization RU000A10AP21 (оферта 27.02.2026)"},
        {"series": "003P-09", "isin": "RU000A10AT19", "issue_date": "2025-02-10", "type": "floating",
         "coupon": None, "spread": 0.02, "freq": 12, "face_2025_12_31": 18.0, "face_2026_06_30": 0.0,
         "face_2026_09_28": 0.0, "exit_date": "2026-04-09",
         "src": "МСФО 2025 прим. 21 («в феврале … 003P-09 … плавающим купоном 2,00 % к ключевой»); "
                "MOEX ISS bondization RU000A10AT19 (оферта 09.04.2026)"},
    ]
    placements_stress = [
        {"series": "003P-01", "date": None, "coupon": 0.23, "put_months": 9,
         "src": "МСФО 2024 прим. (кредиты и займы): «в ноябре и декабре 2024 … 003P-01 … 23,00 % и 003P-07 … "
                "22,85 %, оба выпуска с офертой через 9 месяцев» — дата размещения 003P-01 не раскрыта"},
        {"series": "003P-07", "date": "2024-12-26", "coupon": 0.2285, "put": "2025-09-25", "freq": 12,
         "src": "МСФО 2024 (там же); дата — MOEX ISS ISSUEDATE; оферта 25.09.2025 (X2 §3.2)"},
        {"series": "003P-08", "date": "2025-01-27", "coupon": 0.215, "put": "2026-02-27", "freq": 12,
         "src": "МСФО 2025 прим. 21; MOEX ISS"},
    ]
    kr = json.loads(json.dumps(reg["reported_2026_06_30"]))
    dump("debt.json", {
        "_about": "Реестр долга X5: облигации ИКС 5 ФИНАНС (номинал, млрд ₽), погашенные в 1П2026, банки, "
                  "раскрытия МСФО. Источник — research/facts/debt_register.json (сборка X2, у каждого числа "
                  "ссылка на первичку) + MOEX ISS (inputs/web).",
        "source": src_note(H / "research/facts/debt_register.json"),
        "bonds": bonds + red, "placements_stress_extra": placements_stress,
        "banks": {"short_2026_06_30": 41.913, "long_2026_06_30": 173.501, "short_2025_12_31": 42.5,
                  "long_2025_12_31": 161.591, "short_final_maturity": "2026-2027",
                  "long_final_maturity": "2027-2028",
                  "src": "МСФО 1П2026 прим. 15, с. 20 (кредиторы и ставки по траншам не раскрыты)"},
        "reported": kr,
        "floating_share_history": {
            "2023-12-31": {"v": 0.26, "src": "МСФО 2024, прим. «Процентный риск» (на 31.12.2023: 26 %)"},
            "2024-12-31": {"v": 0.56, "src": "МСФО 2024, прим. «Процентный риск»"},
            "2025-06-30": {"v": 0.36, "src": "МСФО 1П2025, «Рыночный риск – процентный риск»"},
            "2025-12-31": {"v": 0.39, "src": "МСФО 2025 прим. 30, с. 50"},
            "2026-06-30": {"v": 0.55, "src": "МСФО 1П2026 прим. 24, с. 24"}},
        "interest": {
            "loans_h1_2026": {"v": 31.076, "src": "МСФО 1П2026 прим. 21: «Процентные расходы по кредитам и займам» 31 076"},
            "loans_h1_2025": {"v": 31.416, "src": "там же, сравнительные"},
            "loans_fy2025": {"v": 61.176, "src": "МСФО 2025 прим. 27"},
            "loans_fy2024": {"v": 34.138, "src": "МСФО 2025 прим. 27, сравнительные"},
            "income_h1_2026": {"v": 3.186, "src": "МСФО 1П2026 прим. 21: «Процентные доходы» (3 186)"},
            "income_h1_2025": {"v": 22.938, "src": "там же, сравнительные"},
            "income_fy2025": {"v": 27.817, "src": "МСФО 2025 прим. 27"},
            "income_fy2024": {"v": 27.348, "src": "МСФО 2025 прим. 27, сравнительные"},
            "capitalised_h1_2026": {"v": 2.085, "src": "МСФО 1П2026 прим. 15"},
            "tc_amortised_h1_2026": {"v": 0.169, "src": "МСФО 1П2026 прим. 15 («амортизации транзакционных издержек 169»)"},
            "tc_unamortised_2026_06_30": {"v": 0.413, "src": "МСФО 1П2026 прим. 15"},
            # издержки размещения: неамортизированный остаток (займы — за их вычетом) и амортизация за период
            "transaction_costs": {
                "unamortised": {"2023-12-31": 0.116, "2024-12-31": 0.180, "2025-06-30": 0.296,
                                "2025-12-31": 0.371, "2026-06-30": 0.413},
                "amortised": {"2024": 0.089, "2025H1": 0.160, "2025": 0.322, "2026H1": 0.169},
                "src": "«Все кредиты и займы … отражены за вычетом соответствующих транзакционных издержек» и "
                       "«амортизации транзакционных издержек»: МСФО 2024 прим. 21, МСФО 1П2025 прим. 15, "
                       "МСФО 2025 прим. 21, МСФО 1П2026 прим. 15"},
            "borrowings_quarter_end": {
                "2025-06-30": 312.404, "2025-09-30": 321.615, "2025-12-31": 405.428, "2026-03-31": 405.405,
                "2026-06-30": 434.709,
                "src": "financial_and_operating_results_q2_2026.xlsx › Financial Position!R42 + R49 "
                       "(долгосрочные + краткосрочные кредиты и займы)"},
            "borrowings_2025_12_31": {"v": 405.428, "src": "МСФО 1П2026 прим. 15"},
            "borrowings_2026_06_30": {"v": 434.709, "src": "МСФО 1П2026 прим. 15"},
            "total_debt_2026_03_31": {"v": 406.659, "src": "databook q2_2026 › Debt!R6 (31.03.2026; с лизингом)"},
            "leasing_2026_06_30": {"v": 1.148, "src": "databook Debt!R6 − кредиты и займы баланса (research/facts)"},
            "deposits": {"text": "Депозиты в рублях размещены по ставкам в диапазоне 14-15% годовых, депозиты в "
                                 "валюте — 0,1-2,5%", "rub_deposits": 41.578, "fx_deposits": 5.095,
                         "rub_cash_accounts": 58.28, "fx_cash_accounts": 20.257, "total": 125.21,
                         "src": "МСФО 1П2026 прим. 8, с. 15 (скан; сумма 125 210 сходится)"}},
    })


def _cbr_changes(path: Path) -> list[list]:
    t = path.read_text(encoding="utf-8")
    rows = re.findall(r"<tr>\s*<td>(\d\d\.\d\d\.\d{4})</td>\s*<td>([\d,]+)</td>", t)
    prev, ch = None, []
    for day, val in reversed(rows):
        if val != prev:
            iso = dt.datetime.strptime(day, "%d.%m.%Y").date().isoformat()
            ch.append([iso, float(val.replace(",", "."))])
            prev = val
    return ch


def build_keyrate(path13: str | None) -> None:
    """Ряд ЦБ с 03.01.2024 — первичка папки передачи; 17.09.2013–31.12.2023 — выгрузка cbr.ru (--cbr2013)."""
    path = PRIM / "cbr-keyrate-2024-01-01_2026-09-28.html"
    ch = _cbr_changes(path)
    old = json.loads((INPUTS / "keyrate.json").read_text(encoding="utf-8")) if (INPUTS / "keyrate.json").exists() else {}
    early, early_src = old.get("changes_2013_2023"), old.get("source_2013_2023")
    if path13:
        p13 = Path(path13)
        early = _cbr_changes(p13)
        early_src = {"url": "https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True&UniDbQuery.From=17.09.2013"
                            "&UniDbQuery.To=31.12.2023", "accessed": "2026-09-28", "sha256": sha(p13)}
    dump("keyrate.json", {"_about": "Ключевая ставка ЦБ РФ: даты изменения и значение, %",
                          "source": src_note(path) | {"url": "https://www.cbr.ru/hd_base/KeyRate/"},
                          "changes": ch, "source_2013_2023": early_src, "changes_2013_2023": early})


def build_quarterly() -> None:
    reg = json.loads((H / "research/facts/debt_register.json").read_text(encoding="utf-8"))
    dump("quarterly_finance.json", {"_about": "Финансовые расходы и доходы до МСФО 16 по кварталам, долг и "
                                              "касса на концах кварталов (databook X5; сборка X2 §6)",
                                    "source": src_note(H / "research/facts/debt_register.json"),
                                    **reg["net_interest_quarterly"]})


# ------------------------------------------------------------------ databook: рычаг и capex
def build_databook() -> None:
    import openpyxl  # только для пересборки входов
    out = {"_about": "Чистый долг до МСФО 16 и ЧД/EBITDA по годам и кварталам (databook X5, лист Debt, строки 11–12)",
           "new": {}, "old": {}, "source": [src_note(DATABOOK_NEW), src_note(DATABOOK_OLD)]}
    wb = openpyxl.load_workbook(DATABOOK_NEW, data_only=True, read_only=True)
    rows = list(wb["Debt"].iter_rows(values_only=True))
    hdr = rows[4]
    for j, h in enumerate(hdr):
        if isinstance(h, dt.datetime):
            col = openpyxl.utils.get_column_letter(j + 1)
            out["new"][h.date().isoformat()] = {"nd": rows[10][j] / 1000, "nd_ebitda": rows[11][j],
                                                "cash_row": f"Debt!{col}11/{col}12"}
    wb = openpyxl.load_workbook(DATABOOK_OLD, data_only=True, read_only=True)
    rows = list(wb["Debt"].iter_rows(values_only=True))
    hdr = rows[4]
    for j, h in enumerate(hdr):
        if h is None or h == "Russian rouble (RUB), million":
            continue
        col = openpyxl.utils.get_column_letter(j + 1)
        key = str(h)
        nd, lev = rows[10][j], rows[11][j]
        if isinstance(nd, (int, float)):
            out["old"][key] = {"nd": nd / 1000, "nd_ebitda": lev, "cell": f"Debt!{col}11/{col}12"}
    dump("leverage_history.json", out)

    # capex / выручка по годам: 2011–2017 — старый databook, 2018–2025 — research/facts/history_annual.json
    wb = openpyxl.load_workbook(DATABOOK_OLD, data_only=True, read_only=True)
    pl = list(wb["Profit and Loss"].iter_rows(values_only=True))
    cf = list(wb["Cash Flow"].iter_rows(values_only=True))
    years = {}
    for j, y in enumerate(pl[4]):
        if isinstance(y, int) and 2011 <= y <= 2017:
            col = openpyxl.utils.get_column_letter(j + 1)
            years[str(y)] = {"revenue": pl[5][j] / 1000, "capex": -(cf[32][j] + cf[41][j]) / 1000,
                             "src": f"старый databook › Profit and Loss!{col}6; Cash Flow!{col}33 + {col}42"}
    ann = json.loads((H / "research/facts/history_annual.json").read_text(encoding="utf-8"))["data"]
    for fy, node in ann.items():
        rev, cap = node["revenue"]["v"], node["capex_total"]["v"]
        years[fy[2:]] = {"revenue": rev / 1000, "capex": -cap / 1000,
                         "src": "research/facts/history_annual.json (revenue, capex_total = ОС + НМА, ОДДС до МСФО 16)"}
    q = json.loads((H / "research/facts/history_quarterly.json").read_text(encoding="utf-8"))["data"]
    h1 = {"revenue": sum(q[k]["revenue"]["v"] for k in ("2026Q1", "2026Q2")) / 1000,
          "capex": -sum(q[k]["capex_total"]["v"] for k in ("2026Q1", "2026Q2")) / 1000,
          "src": "research/facts/history_quarterly.json (2026Q1 + 2026Q2)"}
    dump("capex_history.json", {"_about": "Денежный capex (ОС + НМА) и выручка, млрд ₽",
                                "years": years, "2026H1": h1,
                                "source": [src_note(DATABOOK_OLD), src_note(H / "research/facts/history_annual.json")]})


# ------------------------------------------------------------------ EV/EBITDA X5 в истории
def last_close(rows: list[tuple[str, float]], day: str) -> tuple[str, float] | None:
    best = None
    for d0, p in rows:
        if d0 <= day and p:
            best = (d0, p)
    return best


def build_ev_history() -> None:
    five = []
    with open(MAG / "data/assumptions/evidence/book-1.4/beta/inputs/prices_FIVE.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = r["LEGALCLOSEPRICE"] or r["CLOSE"]
            if p and int(r["NUMTRADES"] or 0) > 0:
                five.append((r["TRADEDATE"], float(p)))
    x5 = []
    with open(H / "research/market/x5_daily.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            x5.append((r["tradedate"], float(r["legalcloseprice"])))
    q = json.loads((H / "research/facts/history_quarterly.json").read_text(encoding="utf-8"))["data"]
    lev = json.loads((INPUTS / "leverage_history.json").read_text(encoding="utf-8"))
    qends = {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}
    out = []
    keys = sorted(q)
    rev_ltm = {k: sum(q[x]["revenue"]["v"] for x in keys[i - 3:i + 1]) / 1000
               for i, k in enumerate(keys) if i >= 3}
    for i, k in enumerate(keys):
        if i < 3:
            continue
        day = f"{k[:4]}-{qends[k[4:]]}"
        ltm = keys[i - 3:i + 1]
        eb = sum(q[x]["ebitda"]["v"] for x in ltm) / 1000
        adj = sum(q[x]["adj_ebitda"]["v"] for x in ltm) / 1000
        rev = sum(q[x]["revenue"]["v"] for x in ltm) / 1000
        nd = None
        if day in lev["new"]:
            nd = lev["new"][day]["nd"]
        else:
            old_key = f"Q{k[5]} {k[:4]}"
            if old_key in lev["old"]:
                nd = lev["old"][old_key]["nd"]
        if day <= "2024-04-03":
            px, sec, n = last_close(five, day), "FIVE (ГДР)", 271.572872
        elif day >= "2025-01-09":
            px, sec, n = last_close(x5, day), "X5", 245.98118
        else:
            px = None
        if px is None or nd is None:
            continue
        # дивиденды, объявленные до даты и выплаченные после (в ЧД их нет): финал 2024 (ГОСА 27.06.2025),
        # 9М 2025 (ВОСА 18.12.2025), финал 2025 (ГОСА 26.06.2026) — data/facts/dividends.json › history/register
        div = {"2025-06-30": 158.848, "2025-12-31": 90.210, "2026-06-30": 60.269}.get(day, 0.0)
        out.append({"quarter": k, "date": day, "price_date": px[0], "price": px[1], "sec": sec,
                    "shares_mln": n, "nd": nd, "div_after": div, "ebitda_ltm": eb, "adj_ebitda_ltm": adj,
                    "revenue_ltm": rev})
    dump("ev_history.json", {
        "_about": "EV/EBITDA X5 на концы кварталов: EV = цена × бумаги + ЧД до МСФО 16 (+ дивиденды, объявленные "
                  "до даты и выплаченные после); EBITDA — до МСФО 16 за 12 мес. (отчётная и скорр.)",
        "notes": ["ГДР FIVE 2018–03.2024: 271 572 872 ГДР (67 893 218 акций × 4), казначейские пренебрежимы "
                  "(лист беты книги Магнита 1.4, inputs/shares.json); цены — ISS (копия листа беты Магнита)",
                  "акция X5 с 09.01.2025: 245 981 180 в обращении (МСФО 1П2026 прим. 17); цены — ISS "
                  "(research/market/x5_daily.csv)",
                  "между 04.2024 и 01.2025 торгов не было (редомициляция)"],
        "source": [src_note(MAG / "data/assumptions/evidence/book-1.4/beta/inputs/prices_FIVE.csv"),
                   src_note(H / "research/market/x5_daily.csv"),
                   src_note(H / "research/facts/history_quarterly.json")],
        "rows": out, "revenue_ltm": rev_ltm,
        "adj_ebitda_q": {k: q[k]["adj_ebitda"]["v"] / 1000 for k in keys}})


# ------------------------------------------------------------------ рынок и акционеры
def build_market() -> None:
    rows = []
    with open(H / "research/market/x5_daily.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    def vwap(a, b):
        s = [r for r in rows if a <= r["tradedate"] <= b]
        return sum(float(r["value"]) for r in s) / sum(float(r["volume"]) for r in s)
    web = INPUTS / "web"
    desc = {r[0]: r[2] for r in json.loads((web / "iss_x5_desc.json").read_text(encoding="utf-8"))
            ["description"]["data"]}
    mdesc = {r[0]: r[2] for r in json.loads((web / "iss_mgnt_desc.json").read_text(encoding="utf-8"))
             ["description"]["data"]}
    an = json.loads((web / "iss_imoex_analytics.json").read_text(encoding="utf-8"))["analytics"]
    cols = an["columns"]
    w = {r[cols.index("ticker")]: r[cols.index("weight")] for r in an["data"]}
    beta = json.loads((H / "research/market/beta_summary.json").read_text(encoding="utf-8"))
    shares = json.loads((REPO / "data/facts/shares.json").read_text(encoding="utf-8"))
    dump("market.json", {
        "_about": "Рынок X5: цена, акции, казначейский пакет, ликвидность, листинг, волатильность",
        "price_2026_09_25": {"v": 1808.5, "src": "MOEX ISS TQBR, LEGALCLOSEPRICE 25.09.2026 (meta.market_price книги)"},
        "shares_outstanding_mln": shares["outstanding_mln"],
        "shares_issued_mln": shares["issued"], "treasury_mln": shares["treasury"],
        "treasury_sales_2026H1": shares["treasury_sales_history"][0],
        "treasury_policy": shares["treasury_policy"], "free_float": shares["free_float_pct"],
        "vwap": {"2026H1": vwap("2026-01-01", "2026-06-30"), "2026Q1": vwap("2026-01-01", "2026-03-31"),
                 "2026Q2": vwap("2026-04-01", "2026-06-30"), "2025Q2": vwap("2025-04-01", "2025-06-30"),
                 "src": "research/market/x5_daily.csv (ISS: VALUE / VOLUME)"},
        "turnover_bn_day": {"all": 2.47, "last_3m": 1.86, "src": "X4 §1 (x5_daily.csv)"},
        "listing": {"X5": desc.get("LISTLEVEL"), "MGNT": mdesc.get("LISTLEVEL"),
                    "src": "MOEX ISS /iss/securities/{X5,MGNT}.json › description.LISTLEVEL (28.09.2026)"},
        "imoex_weight_pct": {"X5": w.get("X5"), "MGNT": w.get("MGNT"), "date": an["data"][0][1],
                             "src": "MOEX ISS /iss/statistics/engines/stock/markets/index/analytics/IMOEX.json"},
        "vol_weekly_tr": beta["center_weekly_mcftr"]["vol_stock"],
        "vol_daily_tr": beta["center_daily_mcftr"]["vol_stock"],
        "vol_src": "research/market/beta_summary.json (полная доходность X5, 09.01.2025–25.09.2026)",
        "treasury_buyout": {"shares_mln": 26.452692, "cost_bn": 56.115, "cash_bn": 50.01,
                            "src": "МСФО 2025 прим. 22; МСФО 1П2026 с. 8, прим. 17 (X2 §1.2)"},
        "source": [src_note(H / "research/market/x5_daily.csv"), src_note(REPO / "data/facts/shares.json"),
                   src_note(H / "research/market/beta_summary.json")]})


def build_beta() -> None:
    a = json.loads((H / "research/market/beta_summary.json").read_text(encoding="utf-8"))
    b = json.loads((MAG / "data/assumptions/evidence/book-1.4/beta/beta_summary.json").read_text(encoding="utf-8"))
    keep = {k: b[k] for k in ("end", "beta_d", "x5_weekly_windows", "k_book", "results") if k in b}
    keep["results"] = {k: v for k, v in keep["results"].items() if k.startswith(("X5|", "MGNT|", "LENT|"))}
    dump("beta.json", {"_about": "Беты X5 по полной доходности к MCFTR: лист X4 (по 25.09.2026, окно с 01.2025) и "
                                 "лист беты книги Магнита 1.4 (по 18.09.2026, окна 2/3/5 лет со сшивкой ГДР FIVE)",
                       "x4": a, "magnit_book14": keep,
                       "source": [src_note(H / "research/market/beta_summary.json"),
                                  src_note(MAG / "data/assumptions/evidence/book-1.4/beta/beta_summary.json")]})


def build_damodaran(path: str | None) -> None:
    if not path:
        print("damodaran.json: оставлен как есть (нет --damodaran)")
        return
    p = Path(path)
    t = p.read_text(encoding="latin-1")
    text = re.sub(r"<[^>]*>", " ", t)
    text = re.sub(r"\s+", " ", text)

    def row(name):
        m = re.search(re.escape(name) + r" (\S+) ([\d.]+)% ([\d.]+)% ([\d.]+)%", text)
        return {"rating": m.group(1), "default_spread": float(m.group(2)) / 100,
                "crp": float(m.group(3)) / 100, "erp": float(m.group(4)) / 100}
    upd = re.search(r"Last updated: ([A-Za-z]+ \d+, \d{4})", text).group(1)
    ru, us = row("Russia"), row("United States")
    dump("damodaran.json", {"_about": "Damodaran, Country Default Spreads and Risk Premiums",
                            "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html",
                            "last_updated": upd, "accessed": "2026-09-28", "sha256": sha(p),
                            "Russia": ru, "United States": us,
                            "mature_erp": round(us["erp"] - us["crp"], 6),
                            "note": "зрелая премия = ERP США − её добавка (по методу страницы: US ERP минус default spread US)"})


def build_magnit() -> None:
    import yaml
    path = MAG / "data/assumptions/assumptions.yaml"
    A = yaml.safe_load(path.read_text(encoding="utf-8"))
    J, V, F = A["joint"], A["valuation"], A["financing"]
    axes = {a["name"]: a for a in V["uncertainty"]["axes"]}
    chk = (MAG / "model/checks.py").read_text(encoding="utf-8")
    consts = {}
    for name in ("EV_EBITDA_RANGE", "EV_EBITDA_REGIMES", "MARGIN_RANGE", "CAPEX_RANGE",
                 "TERMINAL_SHARE_RANGE", "REAL_RATE_RANGE"):
        m = re.search(rf"^{name} = (.+)$", chk, flags=re.M)
        consts[name] = m.group(1).strip()
    dump("magnit_book.json", {
        "_about": "Выписка из книги Магнита 1.6 (magnit-850oa, ветка v5, только чтение): общие с X5 суждения и "
                  "числа, с которыми сравнивается X5",
        "source": [src_note(path), src_note(MAG / "model/checks.py")],
        "joint": {"world_prob": J["world_prob"], "market_implied_prob": J["world_prob_market_implied"],
                  "neutral_world": J["macro_neutral_world"], "lambda": J["own_macro_confidence"],
                  "by_world": J["by_world"]},
        "financing": {k: F[k] for k in ("spread_float", "spread_fixed", "cash_yield_k", "fixed_share",
                                        "leverage_target", "dividends_from_year")},
        "valuation": {"beta_u": V["beta_u"], "erp": V["erp"], "governance_discount": V["governance_discount"],
                      "print_step": V["headline"]["print_step"], "draws": V["uncertainty"]["draws"],
                      "seed": V["uncertainty"]["seed"]},
        "axes": {k: axes[k] for k in ("β_u", "ERP", "Дисконт за управление", "Целевой рычаг", "Веса миров",
                                      "Инфляция мира M (дата, ликвидность ОФЗ-ИН)")},
        "checks_py": consts,
        "book_update_rule": "ASSUMPTIONS-BOOK.md §17: сдвиг ≥50 б.п. на узлах 5–10 лет или книга старше 45 дней"})


def build_x5_facts() -> None:
    bridge = json.loads((REPO / "data/facts/bridge.json").read_text(encoding="utf-8"))
    divs = json.loads((REPO / "data/facts/dividends.json").read_text(encoding="utf-8"))
    bal = json.loads((REPO / "data/facts/balance.json").read_text(encoding="utf-8"))
    dump("facts_x5.json", {
        "_about": "Факты X5 для моста и дивидендов: строки моста, баланс якоря, дивидендная политика и реестр, LTI",
        "source": [src_note(REPO / "data/facts/bridge.json"), src_note(REPO / "data/facts/dividends.json"),
                   src_note(REPO / "data/facts/balance.json")],
        "bridge": bridge, "dividend_policy": divs["policy"], "dividend_register": divs["register"],
        "balance": {k: bal[k] for k in ("net_debt", "total_debt", "cash", "borrowings", "leasing",
                                        "accrued_interest", "dividends_payable")},
        "lti": {"expense_fy2024": {"v": 3.759, "src": "МСФО 2025 прим. 28, с. 46 (X2 §1.4)"},
                "expense_fy2025": {"v": 7.139, "src": "МСФО 2025 прим. 28, с. 46"},
                "expense_h1_2026": {"v": 4.675, "src": "пресс-релиз 2 кв. 2026, с. 6 (X2 §1.4)"},
                "liability_lt_2025_12_31": {"v": 9.103, "src": "МСФО 2025 прим. 28"},
                "liability_st_2025_12_31": {"v": 2.532, "src": "МСФО 2025 прим. 28"},
                "liability_lt_2026_06_30": {"v": 7.896, "src": "МСФО 1П2026 прим. 13, с. 19"},
                "form": "только денежная, акций и опционов нет (ГО 2025, п. 4.3.2, с. 374)"},
        "unpaid_dividend_2026_08_13": {"v": 14.397, "src": "отчёт эмитента 6м2026, с. 42 (X2 §2.2): выплачено "
                                                          "76,1 % финальных за 2025 г."},
        "covenant_nd_ebitda": {"v": 4.0, "src": "МСФО 1П2026 прим. 16, с. 21 (4,25х два квартала после приобретения)"},
        "audit_opinion_fy2025": "с оговоркой: не получены доказательства по стороне с конечным контролем и связанным "
                                "сторонам (ГО 2025, аудиторское заключение; X2 §1.5)",
        "shareholders": "не раскрываются (Указ № 73 от 27.01.2024; отчёт эмитента 6м2026 п. 3.2, с. 38)"})


def build_worlds() -> None:
    import yaml
    draft = yaml.safe_load((REPO / "data/assumptions/assumptions.draft.yaml").read_text(encoding="utf-8"))
    W = draft["worlds"]
    dump("worlds.json", {"_about": "Миры книги X5 (= книга Магнита 1.6, кривая 18.09.2026): ключевая, кривая, π_LT",
                         "source": src_note(REPO / "data/assumptions/assumptions.draft.yaml"),
                         "worlds_source": W["source"],
                         "worlds": {w: {"key_rate": W[w]["key_rate"], "zero_curve": W[w]["zero_curve"],
                                        "lt_inflation": W[w]["lt"]["inflation"]} for w in ("N", "H", "M")}})


def copy_small() -> None:
    shutil.copyfile(H / "research/market/x5_bonds_gspread.csv", INPUTS / "gspread_2026-09-25.csv")
    print("inputs/gspread_2026-09-25.csv")
    shutil.copyfile(REPO / "data/assumptions/evidence/margin/inputs/x5_margin_history.json",
                    INPUTS / "x5_margin_history.json")
    print("inputs/x5_margin_history.json (копия входа листа margin)")
    ext = REPO / "data/assumptions/evidence/margin/inputs/external_points.json"
    peers = json.loads(ext.read_text(encoding="utf-8"))["peers_2025"]
    dump("margin_peers.json", {"_about": "Маржа EBITDA до МСФО 16 аналогов за 2025 г. (выписка из входа листа margin)",
                               "source": src_note(ext), "peers_2025": peers})


def main() -> None:
    dam = cbr13 = None
    if "--damodaran" in sys.argv:
        dam = sys.argv[sys.argv.index("--damodaran") + 1]
    if "--cbr2013" in sys.argv:
        cbr13 = sys.argv[sys.argv.index("--cbr2013") + 1]
    build_debt()
    build_keyrate(cbr13)
    build_quarterly()
    build_databook()
    build_ev_history()
    build_market()
    build_beta()
    build_damodaran(dam)
    build_magnit()
    build_x5_facts()
    build_worlds()
    copy_small()


if __name__ == "__main__":
    main()
