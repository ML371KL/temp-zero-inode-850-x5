"""Сборка малых входов листа «Маржа и режимы» (evidence/margin/inputs/) из первички и фактов X1.

Что делает:
  * кварталы 2011Q1–2017Q4 — выручка и скорр. EBITDA до МСФО 16 из старого databook X5
    (financial_and_operating_results_q1_2024.xlsx, листы «Profit and Loss» стр. 6 и «EBITDA» стр. 14);
    xlsx читается стандартной библиотекой (zipfile + xml); 3 кв. 2015 — год минус три квартала
    (столбец AO листа EBITDA в databook — копия 3 кв. 2016);
  * контроль: сумма кварталов = году по выручке и скорр. EBITDA для каждого года, где оба из одного databook;
  * кварталы 2018Q1–2026Q2 — из research/facts/history_quarterly.json (сборка X1 по двум databook);
  * LTI по кварталам 2023Q1–2026Q2 и по годам 2021–2025 — оттуда же и из history_annual.json;
  * пишет inputs/x5_margin_history.json: у каждого числа — ссылка на ячейку (src), полугодия и годы — calc;
  * копирует inputs/episodes_magnit_book15.json (референс-класс книги Магнита 1.5, только чтение).

Первичку в репозиторий не кладём: в выходе — путь и sha256 исходных файлов.
Запуск (нужна папка передачи): python -B extract_inputs.py [--handoff ПУТЬ] [--magnit ПУТЬ]
Остальные скрипты листа читают только inputs/ и сети/первички не требуют.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
DB24 = "financial_and_operating_results_q1_2024.xlsx"
DB26 = "financial_and_operating_results_q2_2026.xlsx"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def col_index(ref: str) -> int:
    """'AB' → 28 (1-based)."""
    n = 0
    for ch in ref:
        n = n * 26 + (ord(ch) - 64)
    return n


def col_name(i: int) -> str:
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def read_sheet(path: Path, sheet: str, rows: set[int]) -> dict[str, object]:
    """Значения ячеек нужных строк листа: {'W14': 8096.66, ...} (кэшированные значения формул)."""
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = None
        for s in wb.find("m:sheets", NS):
            if s.get("name") == sheet:
                rid = s.get(f"{{{NS['r']}}}id")
                for rel in rels:
                    if rel.get("Id") == rid:
                        target = rel.get("Target")
        if target is None:
            raise KeyError(f"лист {sheet!r} не найден")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        root = ET.fromstring(z.read(target))
    out: dict[str, object] = {}
    for row in root.iter(f"{{{NS['m']}}}row"):
        r = int(row.get("r"))
        if r not in rows:
            continue
        for c in row.findall("m:c", NS):
            ref = c.get("r")
            v = c.find("m:v", NS)
            if v is None:
                continue
            t = c.get("t")
            if t == "s":
                out[ref] = shared[int(v.text)]
            elif t in ("str", "inlineStr"):
                out[ref] = v.text
            else:
                out[ref] = float(v.text)
    return out


def db24_quarters(path: Path) -> dict[str, dict]:
    """Кварталы 2011Q1–2017Q4 старого databook: заголовки строки 5 («Q1 2011»), выручка P&L стр. 6, скорр. EBITDA стр. 14."""
    pl = read_sheet(path, "Profit and Loss", {4, 5, 6})
    eb = read_sheet(path, "EBITDA", {4, 5, 14})
    out: dict[str, dict] = {}
    for sheet_vals, row_v, key, sheet in ((pl, 6, "revenue", "Profit and Loss"), (eb, 14, "adj_ebitda", "EBITDA")):
        for ref, head in sheet_vals.items():
            m = re.fullmatch(r"([A-Z]+)5", ref)
            if not m or not isinstance(head, str):
                continue
            hm = re.fullmatch(r"Q([1-4]) (\d{4})", head.strip())
            if not hm:
                continue
            col = m.group(1)
            std = sheet_vals.get(f"{col}4")
            if std != "IAS 17":
                continue
            q, y = int(hm.group(1)), int(hm.group(2))
            if y > 2017:
                continue
            per = f"{y}Q{q}"
            val = sheet_vals.get(f"{col}{row_v}")
            out.setdefault(per, {})[key] = {"v": val / 1000.0, "src": f"{DB24} › {sheet}!{col}{row_v}"}
    return dict(sorted(out.items()))


def db24_years(path: Path) -> dict[str, dict]:
    """Годы 2011–2017 старого databook (IAS 17): выручка P&L стр. 6, скорр. EBITDA стр. 14."""
    pl = read_sheet(path, "Profit and Loss", {4, 5, 6})
    eb = read_sheet(path, "EBITDA", {4, 5, 14})
    out: dict[str, dict] = {}
    for sheet_vals, row_v, key, sheet in ((pl, 6, "revenue", "Profit and Loss"), (eb, 14, "adj_ebitda", "EBITDA")):
        for ref, head in sheet_vals.items():
            m = re.fullmatch(r"([A-Z]+)5", ref)
            if not m or not isinstance(head, float):
                continue
            col = m.group(1)
            if sheet_vals.get(f"{col}4") != "IAS 17":
                continue
            y = int(head)
            if y > 2017:
                continue
            out.setdefault(str(y), {})[key] = {"v": sheet_vals[f"{col}{row_v}"] / 1000.0, "src": f"{DB24} › {sheet}!{col}{row_v}"}
    return dict(sorted(out.items()))


def mln_to_bln(item: dict) -> dict:
    src = item.get("src")
    return {"v": item["v"] / 1000.0, "src": src if isinstance(src, str) else list(src)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handoff", default="C:/Users/rodio/Desktop/Claude/x5-850-handoff")
    ap.add_argument("--magnit", default="C:/Users/rodio/Desktop/Claude/magnit-850oa")
    a = ap.parse_args()
    handoff = Path(a.handoff)
    db24 = handoff / "reference/primary" / DB24
    facts = handoff / "research/facts"
    qjson = json.loads((facts / "history_quarterly.json").read_text(encoding="utf-8"))
    ajson = json.loads((facts / "history_annual.json").read_text(encoding="utf-8"))

    quarters = db24_quarters(db24)
    for per, v in qjson["data"].items():
        row = {"revenue": mln_to_bln(v["revenue"]), "adj_ebitda": mln_to_bln(v["adj_ebitda"])}
        if isinstance(v.get("lti_and_mgmt_remuneration"), dict):
            lti = v["lti_and_mgmt_remuneration"]
            row["lti"] = {"v": -lti["v"] / 1000.0, "src": lti["src"]}
        quarters[per] = row
    years = db24_years(db24)
    for y, v in ajson["data"].items():
        yy = y.replace("FY", "")
        row = {"revenue": mln_to_bln(v["revenue"]), "adj_ebitda": mln_to_bln(v["adj_ebitda"])}
        if isinstance(v.get("lti_and_mgmt_remuneration"), dict):
            lti = v["lti_and_mgmt_remuneration"]
            row["lti"] = {"v": -lti["v"] / 1000.0, "src": lti["src"] if isinstance(lti["src"], str) else list(lti["src"])}
        years[yy] = row

    # контроль: квартал старого databook 2019Q1 совпадает с JSON X1 (та же ячейка)
    assert abs(quarters["2019Q1"]["adj_ebitda"]["v"] - 29.473) < 1e-9
    # Ошибка старого databook: столбец 3 кв. 2015 (EBITDA!AO) — копия 3 кв. 2016 (AS) в строках 8–18
    # (SG&A −44 396, аренда 1 773, скорр. EBITDA 19 931, LTI −68 — до рубля, при другой валовой прибыли:
    # AO6 48 990 против AS6 62 554; 48 990 − 44 396 + 1 773 ≠ 19 931). Кварталы 2015 г. давали 64,921 против
    # года H14 59,413. Квартал восстанавливается из года: H14 − AM14 − AN14 − AP14 (ранее отмечено в
    # research/M5-indicators.md, п. 3 папки передачи).
    eb15 = read_sheet(db24, "EBITDA", {14})
    q3_2015 = (eb15["H14"] - eb15["AM14"] - eb15["AN14"] - eb15["AP14"]) / 1000.0
    quarters["2015Q3"]["adj_ebitda"] = {
        "v": round(q3_2015, 6),
        "src": f"{DB24} › EBITDA!H14 − AM14 − AN14 − AP14 (год минус три квартала; столбец AO — копия 3 кв. 2016)"}
    # контроль склейки: сумма кварталов = году для каждого года, где кварталы и год из одного databook
    # (≤2020 — старый, ≥2023 — новый; 2021–2022 — кварталы старого, годы нового databook по правилу X1)
    for y in sorted(years, key=int):
        if 2021 <= int(y) <= 2022:
            continue
        for key in ("revenue", "adj_ebitda"):
            s = sum(quarters[f"{y}Q{q}"][key]["v"] for q in range(1, 5))
            assert abs(s - years[y][key]["v"]) < 0.002, (y, key, s, years[y][key]["v"])

    out = {
        "schema": "x5-margin-history-v1",
        "built": "2026-09-28",
        "units": "млрд ₽; маржа не хранится — считается как adj_ebitda / revenue",
        "basis": "до МСФО 16 (IAS 17); скорр. EBITDA X5 — без LTI и прочего вознаграждения менеджмента, эффекта «Карусели» (2021–2023) и разовой корректировки ОКУ 2К2025",
        "splice": "кварталы ≤2017 — старый databook (X5 Retail Group N.V., лист EBITDA стр. 14, P&L стр. 6); 2018Q1–2022Q4 — старый databook через history_quarterly.json X1; с 2023Q1 — новый databook (ПАО «КЦ ИКС 5»); годы ≤2017 — старый, 2018–2020 — старый, ≥2021 — новый (правило X1)",
        "sources": {
            DB24: {"path": f"x5-850-handoff/reference/primary/{DB24}", "sha256": sha256(db24)},
            "history_quarterly.json": {"path": "x5-850-handoff/research/facts/history_quarterly.json", "sha256": sha256(facts / "history_quarterly.json"),
                                        "note": "сборка X1; каждое число — ссылка на ячейку databook (src)"},
            "history_annual.json": {"path": "x5-850-handoff/research/facts/history_annual.json", "sha256": sha256(facts / "history_annual.json")},
            DB26: {"path": f"x5-850-handoff/reference/primary/{DB26}", "sha256": sha256(handoff / "reference/primary" / DB26),
                   "note": "источник чисел 2023Q1+ (через JSON X1)"},
        },
        "quarters": quarters,
        "years": dict(sorted(years.items())),
    }
    INPUTS.mkdir(exist_ok=True)
    (INPUTS / "x5_margin_history.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    ep_src = Path(a.magnit) / "data/assumptions/evidence/book-1.5/misc/episodes/episodes.json"
    shutil.copyfile(ep_src, INPUTS / "episodes_magnit_book15.json")
    print("кварталов:", len(quarters), "лет:", len(years))
    print("episodes.json sha256:", sha256(ep_src))
    print("записано:", INPUTS / "x5_margin_history.json", INPUTS / "episodes_magnit_book15.json")


if __name__ == "__main__":
    main()
