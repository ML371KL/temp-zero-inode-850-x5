"""Журнал прогнозов (docs/PAYLOAD.md `journal`): ожидание модели против наивных эталонов.

* **Цели** — маржа скорр. EBITDA до МСФО 16 полугодия (`x5.adj_margin`) и рост
  выручки полугодия год к году (`x5.revenue_growth`).
* **Запись** — одна на цель и полугодие: если для открытого полугодия (самое
  раннее прогнозное без внесённого факта) записи нет, выпуск добавляет её с
  прогнозом модели (§12: средние по клеткам слоя «свой взгляд») и тремя
  наивными эталонами из фактов на тот же момент. После факта записать
  прогноз нельзя.
* **Неизменяемость** — прошлые записи переносятся как есть и сверяются
  побайтово (канонический JSON); меняться могут только `actual` и `errors` и
  только один раз, пока `actual` был `null`.
* **Факт** — `data/facts/actuals.json` (вносит человек после отчёта): `actual`
  и ошибки (прогноз − факт, эталон − факт).

Журнал к цене ничего не подключает; правило допуска печатается для владельца.
"""

from __future__ import annotations

import json
import math

from model.book import prev_period, prev_same_half

fsum = math.fsum

TARGETS = ("x5.adj_margin", "x5.revenue_growth")
TARGET_FIELD = {"x5.adj_margin": "margin", "x5.revenue_growth": "revenue_growth"}
BENCHMARKS = ("same_half_last_year", "mean_two_halves", "last_half")
BENCHMARK_TITLES = {"same_half_last_year": "то же полугодие год назад",
                    "mean_two_halves": "среднее двух последних полугодий",
                    "last_half": "как прошлое полугодие"}
MUTABLE = ("actual", "errors")
# Правило допуска (перенесено из 850oa): полугодий вне выборки и отношение MSE.
ADMISSION_REPORTS = 4
ADMISSION_RATIO = 0.8
RULE = ("прогноз модели на открытое полугодие записывается один раз и не меняется; после отчёта "
        "вносится факт и ошибки прогноза и эталонов; прогноз допускается к цене после четырёх "
        "отчётных полугодий вне выборки, если его средняя квадратичная ошибка не больше 0,8 от "
        "ошибки лучшего наивного эталона; решение — за владельцем")


class JournalError(ValueError):
    """Журнал переписан задним числом или не читается."""


def canonical(entry: dict) -> bytes:
    """Канонический вид записи для побайтовой сверки."""
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def entry_id(target: str, period: str) -> str:
    return f"{period}-{target}"


def half_label(p: str) -> str:
    """«1П 2026» для "2026H1"."""
    return f"{p[5]}П {p[:4]}"


def entries_of(journal) -> list[dict]:
    """Записи журнала из блока выпуска, файла `journal.json` или списка."""
    if journal is None:
        return []
    if isinstance(journal, list):
        items = journal
    elif isinstance(journal, dict):
        items = journal.get("entries") or []
    else:
        raise JournalError(f"журнал: ожидался объект с entries или список, а не {type(journal).__name__}")
    if not all(isinstance(e, dict) and e.get("id") for e in items):
        raise JournalError("журнал: запись без id")
    return [json.loads(canonical(e)) for e in items]


# ------------------------------------------------------ факты и эталоны


def actuals_of(facts) -> dict:
    """Факты по целям: {(цель, полугодие): значение} из `actuals.json`."""
    rows = ((facts.data.get("actuals") or {}).get("actuals") or []) if facts is not None else []
    out = {}
    for row in rows:
        value = row.get("value")
        if row.get("target") in TARGETS and row.get("period") and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            out[(row["target"], row["period"])] = float(value)
    return out


def reported_halves(facts) -> dict:
    """Отчётные полугодия: {полугодие: {margin, revenue_growth, revenue}}.

    Источники по старшинству: `actuals.json` (факты журнала), `accounting.json`
    (маржа = скорр. EBITDA / выручка), `history.json` (история полугодий).
    Рост г/г, если не дан, — выручка / выручка того же полугодия год назад − 1.
    """
    D = facts.data
    out: dict[str, dict] = {}

    def put(p, **kw):
        row = out.setdefault(p, {"margin": None, "revenue_growth": None, "revenue": None})
        for k, v in kw.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                row[k] = float(v)

    for h in (D.get("history") or {}).get("halves") or []:
        put(h["period"], margin=h.get("adj_margin"), revenue_growth=h.get("growth"),
            revenue=h.get("revenue"))
    for p, row in ((D.get("accounting") or {}).get("periods") or {}).items():
        rev, adj = row.get("revenue"), row.get("adj_ebitda")
        put(p, revenue=rev, margin=adj / rev if rev and adj is not None else None)
    for p in list(out):
        row, base = out[p], out.get(prev_same_half(p), {}).get("revenue")
        if row["revenue_growth"] is None and row["revenue"] and base:
            row["revenue_growth"] = row["revenue"] / base - 1.0
    for (target, p), value in actuals_of(facts).items():
        put(p, **{TARGET_FIELD[target]: value})
    return out


def benchmarks(facts, period: str) -> dict:
    """Наивные эталоны на полугодие: {эталон: {margin, revenue_growth, note}}."""
    halves = reported_halves(facts)
    last, same = prev_period(period), prev_same_half(period)
    L, S = halves.get(last, {}), halves.get(same, {})

    def mean2(key):
        a, b = L.get(key), S.get(key)
        return (a + b) / 2.0 if a is not None and b is not None else None

    return {"same_half_last_year": {"margin": S.get("margin"),
                                    "revenue_growth": S.get("revenue_growth"),
                                    "note": half_label(same)},
            "mean_two_halves": {"margin": mean2("margin"), "revenue_growth": mean2("revenue_growth"),
                                "note": f"{half_label(same)} и {half_label(last)}"},
            "last_half": {"margin": L.get("margin"), "revenue_growth": L.get("revenue_growth"),
                          "note": half_label(last)}}


# ------------------------------------------------------------- журнал


def errors_of(entry: dict, actual: float) -> dict:
    """Ошибки прогноза и эталонов: значение − факт."""
    bench = entry.get("benchmarks") or {}
    f = entry.get("forecast")
    return {"forecast": f - actual if isinstance(f, (int, float)) else None,
            "benchmarks": {k: (v - actual if isinstance(v, (int, float)) else None)
                           for k, v in bench.items()}}


def status(entries: list[dict]) -> str:
    """Зачёт по марже: сколько полугодий закрыто и, после допуска, отношение MSE."""
    closed = [e for e in entries if e.get("target") == TARGETS[0] and e.get("actual") is not None
              and isinstance((e.get("errors") or {}).get("forecast"), (int, float))]
    k = len(closed)
    if k < ADMISSION_REPORTS:
        return f"копим зачёт: {k} из {ADMISSION_REPORTS} полугодий"
    mse = fsum(e["errors"]["forecast"] ** 2 for e in closed) / k
    best, best_mse = None, None
    for name in BENCHMARKS:
        errs = [(e["errors"].get("benchmarks") or {}).get(name) for e in closed]
        if all(isinstance(x, (int, float)) for x in errs):
            m = fsum(x * x for x in errs) / k
            if best_mse is None or m < best_mse:
                best, best_mse = name, m
    if not best_mse:
        return f"закрыто {k} полугодий; эталона на всех полугодиях нет — отношение не считается"
    ratio = mse / best_mse
    verdict = "проходит" if ratio <= ADMISSION_RATIO else "не проходит"
    return (f"закрыто {k} полугодий: MSE прогноза / MSE эталона «{BENCHMARK_TITLES[best]}» = "
            f"{ratio:.2f} — {verdict} порог {ADMISSION_RATIO:g}".replace(".", ","))


def update(previous, *, period: str | None, forecasts: dict, bench: dict, recorded_at: str,
           actuals: dict) -> tuple[dict, list[str]]:
    """Новый журнал: прошлые записи как есть (+ факт, если пришёл), запись на
    открытое полугодие, если её нет. Возвращает (журнал, id новых записей).

    `forecasts` — {цель: прогноз}; `bench` — эталоны `benchmarks(...)`;
    `actuals` — `actuals_of(...)`. `release_sha` новых записей — None: его
    ставит выпуск после подсчёта своего хэша.
    """
    entries = entries_of(previous)
    for e in entries:
        key = (e.get("target"), e.get("period"))
        if e.get("actual") is None and key in actuals:
            e["actual"] = actuals[key]
            e["errors"] = errors_of(e, actuals[key])
    have = {(e.get("target"), e.get("period")) for e in entries}
    new = []
    if period is not None:
        for target in TARGETS:
            value = forecasts.get(target)
            if (target, period) in have or (target, period) in actuals or value is None:
                continue
            field = TARGET_FIELD[target]
            e = {"id": entry_id(target, period), "target": target, "period": period,
                 "recorded_at": recorded_at, "release_sha": None, "forecast": value,
                 "benchmarks": {k: bench[k][field] for k in BENCHMARKS},
                 "actual": None, "errors": None}
            entries.append(e)
            new.append(e["id"])
    return {"entries": entries, "rule": RULE, "status": status(entries)}, new


def immutability_problems(old, new) -> list[str]:
    """Прошлые записи на месте и не переписаны (побайтово); `actual` и `errors`
    меняются один раз, пока `actual` был null."""
    problems = []
    try:
        was, now = entries_of(old), entries_of(new)
    except JournalError as exc:
        return [str(exc)]
    seen: dict[str, dict] = {}
    for e in now:
        if e["id"] in seen:
            problems.append(f"журнал: запись {e['id']} повторяется")
        seen[e["id"]] = e
    for e in was:
        cur = seen.get(e["id"])
        if cur is None:
            problems.append(f"журнал: запись {e['id']} пропала")
            continue
        if e.get("actual") is None:
            a = {k: v for k, v in e.items() if k not in MUTABLE}
            b = {k: v for k, v in cur.items() if k not in MUTABLE}
        else:
            a, b = e, cur
        if canonical(a) != canonical(b):
            problems.append(f"журнал: запись {e['id']} переписана задним числом")
    return problems
