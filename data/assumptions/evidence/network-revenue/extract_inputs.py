"""Сборка малых входов листа «Сеть и выручка» (evidence/network-revenue/inputs/).

Что делает (запуск нужен только для пересборки inputs/; расчёты листа читают только inputs/):
  * inputs/quarterly_panel.csv — копия research/indicators/x5_quarterly_panel.csv (сборка X5-indicators:
    выручка, ЧРВ, LFL продажи/трафик/чек, площадь, ИПЦ продтоваров г/г по кварталам 2011Q1–2026Q2;
    ячейки databook — в X5-indicators.md §4);
  * inputs/operating_q.json — кварталы 2021Q4–2026Q2 из research/facts/operating_by_format.json
    (ЧРВ, площадь и магазины по форматам, средний чек, число покупателей, выручка, «прочее»; у каждого
    числа — ссылка на ячейку databook) и продуктивность по форматам 2022–2025 и LTM 2К2026;
  * inputs/network_facts.json — выписка data/facts/network.json репозитория (площадь, магазины, открытия
    и закрытия по кварталам 2024Q1–2026Q2 с источниками);
  * inputs/food_cpi_monthly.json — месячный ИПЦ продтоваров Росстата (rosstat_ipc_mes_08-2026.xlsx, лист 02);
    xlsx читается стандартной библиотекой (zipfile + xml);
  * inputs/book_refs.json — миры (продовольственный ИПЦ, инфляция LT) и веса слоёв из черновика книги,
    вероятности режимов из фрагмента «маржа» (только чтение, с sha256).
Числа трейдинг-апдейтов 4 кв. 2022 – 4 кв. 2023 (валовые открытия и закрытия) и планы компании внесены
вручную в inputs/tu_2022_2023.json и inputs/plans.json со ссылками на строки текстов первички; этот скрипт
их не пишет, а проверяет sha256 PDF.

Первичку в репозиторий не кладём: в выходе — путь и sha256 исходных файлов.
Запуск: python -B extract_inputs.py [--handoff ПУТЬ]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
REPO = HERE.parents[3]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
OPER_KEYS = ("nrs_pyaterochka", "nrs_perekrestok", "nrs_chizhik", "nrs_total", "retail_ex_digital",
             "digital_total", "other_revenue", "revenue_total",
             "lfl_sales_total", "lfl_traffic_total", "lfl_ticket_total",
             "lfl_sales_pyaterochka", "lfl_traffic_pyaterochka", "lfl_ticket_pyaterochka",
             "lfl_sales_perekrestok", "lfl_sales_chizhik",
             "space_pyaterochka", "space_perekrestok", "space_chizhik", "space_karusel", "space_total",
             "stores_pyaterochka", "stores_perekrestok", "stores_chizhik", "stores_total",
             "avg_ticket_pyaterochka", "avg_ticket_perekrestok", "avg_ticket_chizhik", "avg_ticket_total",
             "customers_pyaterochka", "customers_perekrestok", "customers_chizhik", "customers_total")
NET_KEYS = ("area_end", "area_end_by_format", "stores_end_by_format", "stores_end_hist",
            "openings_closures_stores", "closed_area_est", "gross_opened_hist", "close_rate_implied")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def read_food_cpi(path: Path) -> dict:
    """Лист 02 (продовольственные товары): месяц → индекс к предыдущему месяцу, %."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = None
        for s in wb.find("m:sheets", NS):
            if s.get("name") == "02":
                rid = s.get(f"{{{NS['r']}}}id")
                target = next(r.get("Target") for r in rels if r.get("Id") == rid)
        sheet = ET.fromstring(z.read("xl/" + target.lstrip("/").replace("xl/", "")))
    cells: dict[tuple[int, str], object] = {}
    for row in sheet.iter(f"{{{NS['m']}}}row"):
        r = int(row.get("r"))
        for c in row.findall("m:c", NS):
            ref = c.get("r")
            col = "".join(ch for ch in ref if ch.isalpha())
            v = c.find("m:v", NS)
            if v is None:
                continue
            val: object = v.text
            if c.get("t") == "s":
                val = shared[int(v.text)]
            else:
                try:
                    val = float(v.text)
                except (TypeError, ValueError):
                    pass
            cells[(r, col)] = val
    # строка 4 (1-based) — годы; строки 6–17 — январь…декабрь (к предыдущему месяцу, %)
    years = {col: int(val) for (r, col), val in cells.items() if r == 4 and isinstance(val, float)}
    mom = {}
    for (r, col), val in cells.items():
        if 6 <= r <= 17 and col in years and isinstance(val, float):
            mom[f"{years[col]}-{r - 5:02d}"] = {"v": val, "src": f"rosstat_ipc_mes_08-2026.xlsx › 02!{col}{r}"}
    # для листа достаточно 2009 г. и позже (ряд LFL X5 — с 2011 г.)
    return {k: v for k, v in sorted(mom.items()) if k >= "2009"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handoff", default=str(HERE.parents[4] / "x5-850-handoff"))
    args = ap.parse_args()
    H = Path(args.handoff)
    prim = H / "reference" / "primary"
    INPUTS.mkdir(exist_ok=True)

    # 1. квартальная панель X5-indicators — копия
    src = H / "research" / "indicators" / "x5_quarterly_panel.csv"
    shutil.copyfile(src, INPUTS / "quarterly_panel.csv")

    # 2. операционные данные по форматам
    ob = json.loads((H / "research" / "facts" / "operating_by_format.json").read_text(encoding="utf-8"))
    q = ob["data"]["quarterly"]
    oper = {}
    for k in sorted(q):
        if k < "2021Q4":
            continue
        oper[k] = {}
        for basis, row in q[k].items():
            oper[k][basis] = {f: row[f] for f in OPER_KEYS if isinstance(row.get(f), dict) and row[f].get("v") is not None}
    prod = {"annual": {y: ob["productivity"]["annual"][y] for y in ("2022", "2023", "2024", "2025")},
            "ltm_2026-06-30": ob["productivity"]["ltm_2026-06-30"], "note": ob["productivity"]["note"]}
    (INPUTS / "operating_q.json").write_text(json.dumps({
        "_about": "Выписка research/facts/operating_by_format.json (сборка X1 по databook): кварталы 2021Q4–2026Q2 "
                  "в двух базисах (as_reported; restated_KY_Slata — колонки «Скорр.»), продуктивность. "
                  "Деньги — млн ₽, площадь — тыс. м², средний чек — ₽ (с НДС, промокодами и бонусами), покупатели — млн.",
        "source": {"file": "x5-850-handoff/research/facts/operating_by_format.json",
                   "sha256": sha256(H / "research" / "facts" / "operating_by_format.json")},
        "meta_note": ob["meta"].get("note"), "quarterly": oper, "productivity": prod},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    # 3. факты сети репозитория
    nf = json.loads((REPO / "data" / "facts" / "network.json").read_text(encoding="utf-8"))
    (INPUTS / "network_facts.json").write_text(json.dumps({
        "_about": "Выписка data/facts/network.json (факты X5, сопоставимый базис трёх форматов без дарксторов).",
        "source": {"file": "data/facts/network.json", "sha256": sha256(REPO / "data" / "facts" / "network.json")},
        "as_of": nf["as_of"], "basis": nf["basis"], **{k: nf[k] for k in NET_KEYS}},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    # 4. месячный продовольственный ИПЦ
    xl = prim / "rosstat_ipc_mes_08-2026.xlsx"
    (INPUTS / "food_cpi_monthly.json").write_text(json.dumps({
        "_about": "Индекс потребительских цен на продовольственные товары, к предыдущему месяцу, % (Росстат).",
        "source": {"file": "x5-850-handoff/reference/primary/rosstat_ipc_mes_08-2026.xlsx", "sheet": "02",
                   "sha256": sha256(xl)},
        "mom_pct": read_food_cpi(xl)}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    # 5. ссылки на книгу (чужие ключи — только чтение)
    import yaml  # PyYAML — единственная внешняя зависимость
    draft_p = REPO / "data" / "assumptions" / "assumptions.draft.yaml"
    frag_p = REPO / "data" / "assumptions" / "fragments" / "margin.yaml"
    dr = yaml.safe_load(draft_p.read_text(encoding="utf-8"))
    fr = yaml.safe_load(frag_p.read_text(encoding="utf-8"))
    refs = {
        "_about": "Снимок чужих ключей книги для проверок листа: миры (прод. ИПЦ, ИПЦ, инфляция LT), веса слоёв, "
                  "привязки мира; вероятности режимов — из фрагмента «маржа».",
        "draft": {"file": "data/assumptions/assumptions.draft.yaml", "sha256": sha256(draft_p)},
        "margin_fragment": {"file": "data/assumptions/fragments/margin.yaml", "sha256": sha256(frag_p)},
        "meta": {k: dr["meta"][k] for k in ("anchor_period", "first_period", "last_period")},
        "worlds": {w: {"food_cpi": dr["worlds"][w]["food_cpi"], "cpi": dr["worlds"][w]["cpi"],
                       "lt_inflation": dr["worlds"][w]["lt"]["inflation"]} for w in ("N", "H", "M")},
        "world_prob": dr["joint"]["world_prob"], "market_implied_prob": dr["joint"]["market_implied_prob"],
        "neutral_world": dr["joint"]["neutral_world"],
        "regime_prob_draft": dr["joint"]["regime_prob"], "regime_prob": fr["joint"]["regime_prob"],
        "regime_demand": fr["joint"]["regime_demand"],
        "draft_network": dr["network"], "draft_revenue": dr["revenue"],
        "draft_world_links": dr["joint"]["world_links"], "draft_stress_growth": dr["joint"]["stress_growth"],
    }
    (INPUTS / "book_refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8", newline="\n")

    # 6. проверка sha256 PDF, на которые ссылаются ручные входы
    for name in ("tu_2022_2023.json", "plans.json"):
        d = json.loads((INPUTS / name).read_text(encoding="utf-8"))
        for f, want in d.get("sha256", {}).items():
            got = sha256(prim / f)
            print(("ok  " if got == want else "ИЗМЕНЁН ") + f)
    print("inputs/ собраны:", ", ".join(sorted(p.name for p in INPUTS.iterdir())))


if __name__ == "__main__":
    main()
