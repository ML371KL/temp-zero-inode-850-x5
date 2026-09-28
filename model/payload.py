"""Выпуск X5: `build_payload(...)` → словарь по docs/PAYLOAD.md (контракт `x5-v1`),
`validate(payload)` → список нарушений (пусто — выпуск годен).

Сборка: входы (книга, факты, живые входы конвейера) → сетка на дату оценки →
полоса §9 и всё, что считается на её гиперкубе (суждения, обратный DCF §11,
«что даст отчёт» §12) → проверки §13 → блоки выпуска → журнал прогнозов →
«что изменилось» → хэш содержания → проверка контракта.

**Живые входы** (`live`, формат — `model/README.md`) дают только цену акции и
дату оценки (§13.4): дата оценки = дата принятой цены, не раньше даты книги.
Кривая ОФЗ, ключевая ставка, аналоги, облигации и история цены — наблюдение
(плитки, флаг `book_update`, таблицы). Без `live` — входы книги
(`meta.valuation_date`, `meta.market_price`).

**Хэш** (`meta.payload_sha256`) — sha256 канонического JSON содержания: без
момента сборки (`meta.generated_at`, `live.fetched_at`), собственного хэша и
размера, ссылок на выпуски (`meta.previous_sha256`, блок `changes`, поле
`release_sha` записей журнала). Одни входы — один хэш, сколько ни пересобирай.

**Печать.** Статистики полосы считаются по прогонам в том виде, в каком они
лежат в выпуске (0,1 ₽), — ползунок λ витрины воспроизводит заголовок бит в
бит; печать = округлению этих чисел к шагу книги.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from model import attribution, journal as journal_mod
from model.book import (BOOK_DIR, ROOT, get_node, load_book, open_period, path_value,
                        period_end, prev_same_half, today as _today, trajectory)
from model.book_schema import CAPEX_LEVELS, REGIMES, WORLDS
from model.checks import (BP, PAYLOAD_MAX_BYTES, blocking_reasons,
                          flag_book_update, flag_dividend_register, flag_price_fallback, gates,
                          invariants, load_gate_explanations, round_to_step)
from model.facts import Facts, core_facts, load_facts
from model.grid import (annual_path, evaluate, expected_path, point_of, rub_per_1pct_ev,
                        v0_from_price)
from model.uncertainty import band_stats, distribution, expectation

fsum = math.fsum

SCHEMA = "x5-v1"
# Контракт с витриной (docs/PAYLOAD.md): блоки верхнего уровня.
REQUIRED_TOP_LEVEL = (
    "schema", "meta", "market", "headline", "fair_value", "layers", "grid", "worlds",
    "regimes", "capex_levels", "paths", "debt", "dividends", "history", "reverse_dcf",
    "judgements", "uncertainty", "next_report", "journal", "calendar", "checks", "inputs",
    "live", "changes", "book", "indicators")

LAMBDA_ROWS = 21                # положений ползунка λ: 0; 0,05; …; 1
LAMBDA_STEP = 1 / (LAMBDA_ROWS - 1)
DRAW_DECIMALS = 1               # прогоны в выпуске — до 0,1 ₽
PRICE_DECIMALS = 2              # точные цены заголовка и точки — до копейки
HALF_CENT = 0.0051              # допуск сверки заголовка с прогонами (округление до копейки)
NUMBER_FORMAT = ".9g"           # прочие числа выпуска — 9 значащих цифр
# Защита заголовка (§13.4): скачок печатаемой медианы и V0 медианы.
MAX_HEADLINE_JUMP = 0.25
MAX_V0_JUMP = 0.10
NOTE_TOLERANCE_PCT = 5
# Плитки индикаторов и график цены.
TILE_POINTS = 60
PRICE_HISTORY_POINTS = 130
# Годность наблюдений для строк `inputs`: старше — «stale».
STALE_DAYS = 7
CALENDAR_DAYS = 365
HISTOGRAM_MAX_BINS = 40
GIT_TIMEOUT_S = 60

RELEASE_NOTES = BOOK_DIR / "release_notes.yaml"
CALENDAR = ROOT / "data" / "calendar.json"
NOT_CONTENT_META = ("generated_at", "payload_sha256", "bytes", "previous_sha256")

REGIME_TITLES = {"stress": "Стресс", "floor": "Пол", "partial": "Частичный возврат",
                 "full": "Полный возврат"}
CAPEX_TITLES = {"low": "Низкий", "base": "Базовый", "high": "Высокий"}
SECTIONS = (("worlds", "Миры ставок"), ("joint", "Сетка и вероятности"),
            ("network", "Сеть и площадь"), ("revenue", "Выручка: чек и трафик"),
            ("margin", "Маржа и режимы"), ("capex", "Capex и амортизация"),
            ("working_capital", "Оборотный капитал и касса"), ("tax", "Налог"),
            ("financing", "Финансирование и дивиденды"), ("bridge", "Мост к капиталу"),
            ("valuation", "Оценка"), ("checks", "Коридоры проверок"))
# Ключевые суждения для блока `book` (пути книги; нет пути — строка пропускается).
KEY_JUDGEMENTS = (
    ("A-P1", "joint.world_prob.N", "Вес мира «Нормализация» в своём взгляде", "pct"),
    ("A-P1c", "joint.lambda", "Вес своего взгляда на ставки λ", "number"),
    ("A-C1", "margin.targets.floor.LT", "Маржа далее в режиме «Пол»", "pct"),
    ("A-K1", "capex.maintenance.base.LT", "Поддерживающий capex далее, базовый уровень", "pct"),
    ("A-K3", "capex.price_per_m2", "Стоимость открытия", "bn_per_m2"),
    ("A-V1", "valuation.beta_u", "Бета активов", "number"),
    ("A-V2", "valuation.erp", "Премия за риск акций", "pct"),
    ("A-V3", "valuation.governance_discount", "Дисконт за управление", "pct"),
    ("A-F5", "financing.target_leverage", "Целевой чистый долг / EBITDA", "times"))
INVARIANT_TITLES = {"probabilities": "Вероятности клеток в сумме 1",
                    "fcff_identity": "FCFF из опубликованных строк",
                    "debt_identity": "Путь долга", "capex_identity": "Capex из трёх частей",
                    "finite": "Все числа конечны"}


class ReleaseBlocked(RuntimeError):
    """Сборка не выходит: инвариант, необъяснённый гейт или нарушение контракта."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# ------------------------------------------------------------ мелочи


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _r(x, nd: int = PRICE_DECIMALS):
    return round(x, nd) if _num(x) and math.isfinite(x) else x


def _tidy(obj: Any) -> Any:
    """Числа выпуска — 9 значащих цифр (хвосты двоичной арифметики и последние
    биты libm разных ОС не меняют хэш); словари и списки — рекурсивно."""
    if isinstance(obj, float):
        return float(format(obj, NUMBER_FORMAT)) if math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _tidy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_tidy(v) for v in obj]
    return obj


def _div(a, b):
    return a / b if _num(a) and _num(b) and b else None


def _day(x) -> dt.date | None:
    if x is None:
        return None
    if isinstance(x, dt.date):
        return x
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _raw_src(node) -> str | None:
    """Источник узла факта `{"v", "src"|"calc"}`."""
    if isinstance(node, dict):
        return node.get("src") or node.get("calc")
    return None


def compact_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def content_digest(payload: dict) -> str:
    """sha256 содержания выпуска (без времени сборки, своего хэша, размера и
    ссылок на выпуски)."""
    body = {k: v for k, v in payload.items() if k not in ("meta", "changes")}
    body["meta"] = {k: v for k, v in (payload.get("meta") or {}).items()
                    if k not in NOT_CONTENT_META}
    if isinstance(body.get("live"), dict):
        body["live"] = {k: v for k, v in body["live"].items() if k != "fetched_at"}
    if isinstance(body.get("journal"), dict):
        J = body["journal"]
        body["journal"] = {**J, "entries": [{k: v for k, v in e.items() if k != "release_sha"}
                                            for e in J.get("entries") or []]}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def engine_commit() -> str:
    """Коммит кода сборки: `GITHUB_SHA` в Actions, иначе `git rev-parse HEAD`."""
    env = os.environ.get("GITHUB_SHA")
    if env:
        return env
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def generated_at() -> str:
    """Момент сборки UTC; при `FAKE_TODAY` — полдень этого дня."""
    if os.environ.get("FAKE_TODAY"):
        return f"{_today().isoformat()}T12:00:00Z"
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ входы


def live_inputs(A: dict, live: dict | None) -> dict:
    """Дата оценки, цена рынка и её происхождение (§13.4)."""
    M = A["meta"]
    if not live or not isinstance(live.get("price"), dict) or not _num(live["price"].get("value")):
        return {"valuation_date": dt.date.fromisoformat(M["valuation_date"]),
                "market_price": float(M["market_price"]), "price_date": M["valuation_date"],
                "price_time": None, "price_source": "книга (meta.market_price)",
                "status": "book", "accepted": None, "reason": None}
    price = live["price"]
    day = _day(price.get("date"))
    if day is None:
        raise ReleaseBlocked([f"живые входы: у цены {price.get('value')} нет даты"])
    status = price.get("status") or ("live" if price.get("accepted") else "fallback")
    return {"valuation_date": max(day, dt.date.fromisoformat(M["date"])),
            "market_price": float(price["value"]), "price_date": day.isoformat(),
            "price_time": price.get("time"), "price_source": price.get("source") or "ISS TQBR",
            "status": status, "accepted": price.get("accepted"), "reason": price.get("reason")}


# --------------------------------------------------------- блоки: рынок


def _dividend_rows(F: Facts) -> list[dict]:
    """Все известные выплаты с датой отсечки: реестр и история."""
    D = F.data.get("dividends") or {}
    out = []
    for row in (D.get("register") or []) + (D.get("history") or []):
        if _num(row.get("dps")) and row.get("ex_date"):
            out.append({"date": str(row["ex_date"]), "dps": float(row["dps"])})
    return sorted({(r["date"], r["dps"]): r for r in out}.values(), key=lambda r: r["date"])


def _dividend_yield(F: Facts, v: dt.date, price: float) -> float | None:
    since = v - dt.timedelta(days=CALENDAR_DAYS)
    dps = [r["dps"] for r in _dividend_rows(F) if since < dt.date.fromisoformat(r["date"]) <= v]
    return fsum(dps) / price if price > 0 else None


def _thin(rows: list, limit: int) -> list:
    """Не больше `limit` точек, последняя — всегда."""
    if len(rows) <= limit:
        return list(rows)
    step = -(-len(rows) // limit)
    picked = rows[::-1][::step][::-1]
    return picked[-limit:]


def _peers(F: Facts, live: dict | None, price: float, price_date: str) -> dict:
    P = F.data.get("peers") or {}
    raw = {r.get("ticker"): r for r in (F.raw.get("peers") or {}).get("rows") or []}
    quotes = (live or {}).get("peers") or {}
    rows = []
    for r in P.get("rows") or []:
        t = r.get("ticker")
        if t == "X5":
            px, pd = price, price_date
        else:
            q = quotes.get(t) or {}
            px, pd = (float(q["price"]), q.get("date")) if _num(q.get("price")) else (None, None)
        shares, nd = r.get("shares_outstanding"), r.get("net_debt")
        nd_total = nd + (r.get("dividends_after_balance") or 0.0) if _num(nd) else None
        cap = px * shares / 1000.0 if _num(px) and _num(shares) else None
        ev = cap + nd_total if _num(cap) and _num(nd_total) else None
        profit = r.get("net_profit_ltm")
        rows.append({"ticker": t, "name": r.get("name"), "price": px, "price_date": pd,
                     "market_cap": cap, "net_debt": nd_total, "ev": ev,
                     "ebitda_ltm": r.get("ebitda_ltm"), "ev_ebitda": _div(ev, r.get("ebitda_ltm")),
                     "pe": _div(cap, profit) if _num(profit) and profit > 0 else None,
                     "basis": P.get("basis"), "as_of": r.get("reported_on") or P.get("as_of"),
                     "src": _raw_src((raw.get(t) or {}).get("net_debt"))})
    rows.sort(key=lambda x: x["ticker"] != "X5")
    return {"rows": rows, "as_of": P.get("as_of")}


def _brokers(F: Facts) -> dict:
    B = F.data.get("brokers") or {}
    median_key = next((k for k in B if k.startswith("median")), None)
    return {"rows": [{"broker": r.get("broker"), "date": r.get("date"), "target": r.get("target"),
                      "rating": r.get("rating"), "horizon": r.get("horizon"), "src": r.get("src")}
                     for r in B.get("rows") or []],
            "median": B.get(median_key) if median_key else None,
            "after_report": B.get("after_report"), "as_of": B.get("as_of")}


def market_block(A, F, cf, grid, inputs, live, previous) -> dict:
    v, mp = inputs["valuation_date"], inputs["market_price"]
    bal = F.data.get("balance") or {}
    x5 = next((r for r in (F.data.get("peers") or {}).get("rows") or [] if r.get("ticker") == "X5"),
              {})
    cap = mp * cf.shares_mln / 1000.0
    v_star = grid.point.v_star
    history = [{"date": str(r["date"]), "close": float(r["close"])}
               for r in (live or {}).get("price_history") or [] if _num(r.get("close"))]
    if not history and previous:
        history = list((previous.get("market") or {}).get("price_history") or [])
    history = _thin(history, PRICE_HISTORY_POINTS)
    first = history[0]["date"] if history else None
    ex_div = [r for r in _dividend_rows(F) if first and r["date"] >= first]
    profit = x5.get("net_profit_ltm")
    return {"price": mp, "price_date": inputs["price_date"], "price_time": inputs["price_time"],
            "price_source": inputs["price_source"], "price_status": inputs["status"],
            "book_price": float(A["meta"]["market_price"]),
            "market_cap": cap, "claims": grid.layers["analytical"].d, "market_ev": v_star,
            "ebitda_rep_ltm": bal.get("ebitda_rep_ltm"), "adj_ebitda_ltm": bal.get("adj_ebitda_ltm"),
            "ev_ebitda_ltm": _div(v_star, bal.get("ebitda_rep_ltm")),
            "pe_ltm": _div(cap, profit) if _num(profit) and profit > 0 else None,
            "dividend_yield_ltm": _dividend_yield(F, v, mp),
            "peers": _peers(F, live, mp, inputs["price_date"]), "brokers": _brokers(F),
            "price_history": history, "ex_dividend": ex_div}


# ------------------------------------------------ блоки: заголовок, точка


def headline_block(A, dist, mp) -> dict:
    step = float(A["valuation"]["headline"]["print_step"])
    S = dist.stats
    central = _r(S["median"])
    band = [_r(S["p10"]), _r(S["p90"])]
    inner = [_r(S["p25"]), _r(S["p75"])]
    return {"central": central, "printed_central": round_to_step(central, step), "band": band,
            "printed_band": [round_to_step(x, step) for x in band], "inner": inner,
            "printed_inner": [round_to_step(x, step) for x in inner], "mean": _r(S["mean"]),
            "p_central_below_market": S["p_below"], "market_price": mp, "print_step": step,
            "draws": dist.band.n, "lambda": dist.band.lam}


def fair_value_block(A, grid, dist, headline, draws_low, draws_high, mp) -> dict:
    step = float(A["valuation"]["headline"]["print_step"])
    P, ctx = grid.point, grid.ctx
    low, central, high = _r(P.low), _r(P.central), _r(P.high)
    rows = []
    for i in range(LAMBDA_ROWS):
        lam = i / (LAMBDA_ROWS - 1)
        s = band_stats(draws_low, draws_high, lam, mp)
        rows.append({"lambda": lam, "point": point_of(ctx, grid.layers, lam).central,
                     "median": s["median"], "p10": s["p10"], "p25": s["p25"], "p75": s["p75"],
                     "p90": s["p90"], "mean": s["mean"], "p_below": s["p_below"]})
    d, g, shares = grid.layers["analytical"].d, ctx.governance, ctx.facts.shares_mln
    v0_med = v0_from_price(headline["central"], d, g, shares)
    return {"low": low, "central": central, "high": high,
            "printed": {"low": round_to_step(low, step), "central": round_to_step(central, step),
                        "high": round_to_step(high, step)},
            "lambda": P.lam, "lambda_step": LAMBDA_STEP, "rates_view": {"rub": P.rates_view},
            "by_lambda": rows, "draws_low": list(draws_low), "draws_high": list(draws_high),
            "center_ev": {"v0_median": v0_med, "v0_point": P.v0_point, "v_star": P.v_star,
                          "gap_median": v0_med / P.v_star - 1.0, "gap_point": P.gap_point,
                          "rub_per_1pct_ev_median": rub_per_1pct_ev(v0_med, g, shares),
                          "rub_per_1pct_ev_point": P.rub_per_1pct_ev_point},
            "equity_share_of_ev": P.equity_share_of_ev}


def _layer(L) -> dict:
    return {"title": L.title, "world_weights": L.world_weights, "v0": L.v0, "d": L.d,
            "equity": L.equity, "price": L.price, "pv_fcff": L.pv_fcff, "pv_shield": L.pv_shield,
            "pv_terminal": L.pv_terminal, "terminal_share": L.terminal_share,
            "ev_ebitda_fwd": L.ev_ebitda_fwd, "v0_to_d": L.v0_to_d}


def grid_block(grid) -> dict:
    ctx = grid.ctx
    cells = []
    for c in grid.cells:
        r, cell = c.result, c.result.cell
        cells.append({"world": cell.world, "regime": cell.regime, "capex": cell.capex,
                      "p_analytical": c.p["analytical"], "p_market_implied": c.p["market_implied"],
                      "p_neutral": c.p["macro_neutral"], "ev": r.ev, "d": r.claims.total,
                      "equity": r.equity, "price": r.price,
                      "margin_lt": ctx.regime(cell.regime).target_lt,
                      "ev_ebitda_fwd": r.ev_ebitda_fwd, "terminal_share": r.terminal_share,
                      "max_leverage": r.max_leverage, "growth": cell.growth,
                      "credit": cell.credit, "demand": cell.demand})
    return {"cells": cells, "regime_order": list(REGIMES), "capex_order": list(CAPEX_LEVELS),
            "world_order": list(WORLDS)}


def _by_year(values, P) -> list[dict]:
    """Среднее траектории по полугодиям года (годы сетки)."""
    years: dict[int, list] = {}
    for p, x in zip(P, values):
        years.setdefault(int(p[:4]), []).append(x)
    return [{"year": y, "value": fsum(xs) / len(xs)} for y, xs in sorted(years.items())]


def worlds_block(A, grid) -> dict:
    ctx, P = grid.ctx, grid.ctx.P
    J = A["joint"]
    out = {}
    for w in WORLDS:
        W, WP, only = A["worlds"][w], ctx.world(w), grid.worlds_only[w]
        out[w] = {"name": W["name"],
                  "weights": {"analytical": float(J["world_prob"][w]),
                              "market_implied": float(J["market_implied_prob"][w]),
                              "macro_neutral": 1.0 if J["neutral_world"] == w else 0.0},
                  "key_rate": _by_year(WP.key, P), "cpi": _by_year(WP.cpi, P),
                  "food_cpi": _by_year(WP.food, P),
                  "zero_curve": {k: float(W["zero_curve"][k]) for k in W["zero_curve"]},
                  "lt_inflation": WP.pi_lt, "r_terminal": WP.r_terminal,
                  "real_terminal": WP.r_terminal - WP.pi_lt, "price": only.price, "v0": only.v0}
    out["source"] = dict(A["worlds"]["source"])
    return out


def regimes_block(A, F, grid) -> dict:
    ctx = grid.ctx
    H = F.data.get("history") or {}
    out = {}
    for r in REGIMES:
        RG = ctx.regime(r)
        target = [{"period": ctx.anchor, "value": RG.target_anchor}]
        target += [{"period": p, "value": x} for p, x in zip(ctx.P, RG.target)]
        target.append({"period": "LT", "value": RG.target_lt})
        out[r] = {"title": REGIME_TITLES[r], "target": target, "lt": RG.target_lt,
                  "prior": grid.regime_prior[r], "posterior": grid.regime_posterior[r],
                  "demand": A["joint"]["regime_demand"][r]}
    upd = A["joint"]["regime_update"]
    out["history"] = [{"period": h["period"], "adj_margin": h.get("adj_margin"),
                       "rep_margin": h.get("rep_margin")} for h in H.get("halves") or []]
    out["annual_history"] = [{"year": h["year"], "adj_margin": h.get("adj_margin")}
                             for h in H.get("annual") or []]
    out["expected_lt"] = fsum(grid.regime_posterior[r] * ctx.regime(r).target_lt for r in REGIMES)
    out["update"] = {"sigma_pp": float(upd["sigma_pp"]),
                     "rho": float(A["margin"]["deviation_persistence"]),
                     "cap_pp": float(upd["cap_pp"]),
                     "observations": [dict(o) for o in upd["observations"]],
                     "steps": [{"period": s.period, "value": s.value, "se": s.se,
                                "prior": s.prior, "posterior": s.posterior}
                               for s in grid.regime_steps]}
    return out


def capex_block(A, F, grid) -> dict:
    ctx, C = grid.ctx, A["capex"]
    J = A["joint"]["capex_prob_given_regime"]
    out = {}
    for level in CAPEX_LEVELS:
        spec = C["maintenance"][level]
        out[level] = {"title": CAPEX_TITLES[level],
                      "maintenance": _by_year(trajectory(spec, ctx.P), ctx.P),
                      "lt": path_value(spec, ctx.P[-1]),
                      "p_given_regime": {r: float(J[r][level]) for r in REGIMES}}
    out["history"] = [{"year": h["year"], "capex_pct": h.get("capex_pct"), "da_pct": h.get("da_pct")}
                      for h in (F.data.get("history") or {}).get("annual") or []]
    out["price_per_m2"] = float(C["price_per_m2"])
    out["infra_per_m2"] = float(C["infra_per_m2"])
    out["maintenance_area_share"] = float(C["maintenance_area_share"])
    return out


def paths_block(F, cf, grid) -> dict:
    halves = expected_path(grid)
    annual = annual_path(grid, halves)
    acc = (F.data.get("accounting") or {}).get("periods") or {}
    area_hist = (F.data.get("network") or {}).get("area_end") or {}
    anchor, anchor_year = cf.anchor, int(cf.anchor[:4])
    rows = []
    prev_rev, prev_area = None, None
    y0 = annual[0]["year"] if annual else anchor_year
    base_rev = [acc.get(f"{y0 - 1}H{h}", {}).get("revenue") for h in (1, 2)]
    if all(_num(x) for x in base_rev):
        prev_rev = fsum(base_rev)
    prev_area = area_hist.get(f"{y0 - 1}H2")
    by_year: dict[int, list] = {}
    for h in halves:
        by_year.setdefault(int(h["period"][:4]), []).append(h)
    for a in annual:
        hs = by_year.get(a["year"], [])
        weight = fsum(h["revenue"] for h in hs)
        ticket = fsum(h["revenue"] * h["ticket"] for h in hs) / weight if weight else None
        traffic = fsum(h["revenue"] * h["traffic"] for h in hs) / weight if weight else None
        rows.append({"year": a["year"], "revenue": a["revenue"],
                     "revenue_growth": _div(a["revenue"], prev_rev) - 1.0 if prev_rev else None,
                     "ticket": ticket, "traffic": traffic, "area_end": a["area_end"],
                     "area_growth": a["area_end"] / prev_area - 1.0 if _num(prev_area) else None,
                     "margin": a["margin"], "adj_ebitda": a["adj_ebitda"], "lti": a["lti"],
                     "da": a["da"], "capex": a["capex"], "capex_maintenance": a["capex_maintenance"],
                     "capex_growth": a["capex_growth"], "capex_infra": a["capex_infra"],
                     "capex_pct": a["capex_pct"], "nwc_change": a["nwc_change"],
                     "tax_unlevered": a["tax_unlevered"], "fcff": a["fcff"], "shield": a["shield"],
                     "interest": a["interest"], "dividends": a["dividends"],
                     "net_debt": a["net_debt_company"], "leverage": a["leverage"], "fact": a["fact"]})
        prev_rev, prev_area = a["revenue"], a["area_end"]
    first = [{"period": anchor, "revenue": cf.revenue[anchor], "margin": cf.margin_anchor,
              "adj_ebitda": cf.adj_ebitda[anchor], "capex": cf.capex_anchor, "fcff": None,
              "net_debt": cf.net_debt}]
    hrows = first + [{"period": h["period"], "revenue": h["revenue"], "margin": h["margin"],
                      "adj_ebitda": h["adj_ebitda"], "capex": h["capex"], "fcff": h["fcff"],
                      "net_debt": h["net_debt_company"]} for h in halves]
    return {"annual": rows, "halves": hrows,
            "fact_marks": {"annual": [a["year"] for a in annual if a["fact"]], "halves": [anchor]}}


# ------------------------------------------------ блоки: долг, дивиденды


def _latest_key(row: dict, prefix: str):
    """Значение ключа `<prefix>_ГГГГ_ММ_ДД` с самой поздней датой."""
    keys = sorted(k for k in row if k.startswith(prefix + "_") and k[len(prefix) + 1:][:4].isdigit())
    return row.get(keys[-1]) if keys else None


def _bonds(F: Facts, live: dict | None) -> list[dict]:
    R = F.data.get("debt_register") or {}
    quotes = {b.get("isin"): b for b in (live or {}).get("bonds") or []}
    out = []
    for b in R.get("bonds") or []:
        q = quotes.get(b.get("isin")) or {}
        coupon = b.get("coupon_now") if b.get("coupon_now") is not None else b.get("coupon_fixed")
        out.append({"isin": b.get("isin"), "name": b.get("name"), "series": b.get("series"),
                    "outstanding": q.get("outstanding") if _num(q.get("outstanding"))
                    else _latest_key(b, "outstanding"),
                    "coupon_type": b.get("coupon_type"), "coupon": coupon,
                    "spread": b.get("spread_to_key_rate"), "put_date": b.get("put_date"),
                    "maturity": b.get("maturity"),
                    "price": q.get("price") if _num(q.get("price")) else b.get("price"),
                    "ytm": q.get("ytm") if _num(q.get("ytm")) else b.get("ytm"),
                    "duration_years": q.get("duration_years"),
                    "as_of": q.get("price_date") or b.get("as_of")})
    return out


def debt_block(A, F, cf, grid, live) -> dict:
    bal = F.data.get("balance") or {}
    R = F.data.get("debt_register") or {}
    RA = R.get("anchor") or {}
    ctx = grid.ctx
    an = grid.layers["analytical"]
    rolled = fsum(c.p["analytical"] * c.result.claims.rolled for c in grid.cells)
    raw_lines = {ln.get("key"): ln for ln in (F.raw.get("bridge") or {}).get("lines") or []}
    lines = [{"key": b.key, "label": b.label, "amount": b.amount, "included": b.included,
              "src": _raw_src((raw_lines.get(b.key) or {}).get("amount"))} for b in cf.bridge]
    v = ctx.timing.valuation_date
    paid = [d.id for d in cf.dividends if d.in_company and d.ex_date and d.ex_date <= v]
    rows = [{"key": "net_debt", "label": f"Чистый долг на {period_end(cf.anchor).isoformat()}",
             "amount": cf.net_debt},
            {"key": "operating_cash", "label": "Операционная касса", "amount": ctx.opcash_anchor},
            {"key": "bridge_lines", "label": "Строки моста из отчётности", "amount": ctx.bridge_total},
            {"key": "dividends", "label": "Объявленные дивиденды с отсечкой до даты оценки"
             + (f" ({', '.join(paid)})" if paid else ""), "amount": ctx.dividends_declared},
            {"key": "roll", "label": f"Денежный поток с {(period_end(cf.anchor) + dt.timedelta(days=1)).isoformat()} "
             "по дату оценки", "amount": -rolled}]
    return {"anchor": {"as_of": bal.get("as_of"), "total_debt": bal.get("total_debt"),
                       "cash": bal.get("cash"), "net_debt": bal.get("net_debt"),
                       "leverage": bal.get("net_debt_to_ebitda") or _div(bal.get("net_debt"),
                                                                         bal.get("ebitda_rep_ltm")),
                       "leasing": bal.get("leasing"),
                       "lease_liabilities_ifrs16": bal.get("lease_liabilities_ifrs16"),
                       "credit_lines_unused": bal.get("credit_lines_unused"),
                       "floating_share": RA.get("floating_share"),
                       "effective_rate": RA.get(f"effective_rate_{cf.anchor}"),
                       "ratings": [{k: r.get(k) for k in ("agency", "rating", "outlook", "date")}
                                   for r in R.get("ratings") or []]},
            "bridge": {"lines": lines, "rows_at_valuation": rows, "total": an.d},
            "bonds": _bonds(F, live), "bank_loans": RA.get("bank_loans") and {
                k: RA["bank_loans"].get(k) for k in ("short", "long", "total")},
            "wall": [{"period": w.get("period"), "bonds": w.get("bonds"), "banks": w.get("banks")}
                     for w in R.get("wall") or []]}


def dividends_block(A, F, cf, grid, paths, inputs, period) -> dict:
    D = F.data.get("dividends") or {}
    pol = D.get("policy") or {}
    v = inputs["valuation_date"]
    shares = cf.shares_mln
    reg = []
    in_claims = {d.id for d in cf.dividends if d.in_company and d.ex_date and d.ex_date <= v}
    for r in D.get("register") or []:
        paid = r.get("paid_share_on") or {}
        last = paid[sorted(paid)[-1]] if isinstance(paid, dict) and paid else None
        reg.append({"id": r.get("id"), "label": r.get("label"), "dps": r.get("dps"),
                    "amount": r.get("amount"), "record_date": r.get("record_date"),
                    "ex_date": r.get("ex_date"), "pay_until": r.get("pay_until"),
                    "status": r.get("status"), "paid_share": last,
                    "in_claims": r.get("id") in in_claims})
    hist = [{"period": h.get("period"), "label": h.get("label"), "dps": h.get("dps"),
             "amount": h.get("amount"), "record_date": h.get("record_date")}
            for h in D.get("history") or []]
    model = [{"year": a["year"], "amount": a["dividends"], "dps": a["dividends"] * 1000.0 / shares}
             for a in paths["annual"]]
    halves = expected_path(grid)
    nxt = D.get("next_expected") or {}
    dps_model = None
    for h in halves:
        if period and h["period"] == period:
            dps_model = h["dividends"] * 1000.0 / shares
    return {"policy": {"target_leverage": pol.get("target_leverage"),
                       "no_pay_above": pol.get("no_pay_above"), "frequency": pol.get("frequency"),
                       "text": pol.get("base")},
            "register": reg, "history": hist, "model": model,
            "next_expected": {"label": nxt.get("label"), "record_date_est": nxt.get("record_date_est"),
                              "dps_model": dps_model,
                              "note": "; ".join(x for x in (nxt.get("status"), nxt.get("board_est"))
                                                if x)},
            "yield_ltm": _dividend_yield(F, v, inputs["market_price"])}


def history_block(F) -> dict:
    H = F.data.get("history") or {}
    return {"annual": [dict(r) for r in H.get("annual") or []],
            "halves": [{"period": r.get("period"), "revenue": r.get("revenue"),
                        "growth": r.get("growth"), "adj_margin": r.get("adj_margin"),
                        "rep_margin": r.get("rep_margin"), "capex_pct": r.get("capex_pct")}
                       for r in H.get("halves") or []],
            "formats": [dict(r) for r in H.get("formats") or []],
            "format_area": [dict(r) for r in H.get("format_area") or []]}


# ------------------------------------ блоки: суждения, полоса, отчёт


def judgement_unit(kind: str, path: str) -> str:
    """Единица суждения для витрины по имени ключа (числа книги не читаются)."""
    leaf = path.split(".")[-1]
    if kind == "shift":
        return "pp"
    if kind == "dict":
        return "weights"
    if leaf.endswith("_per_m2"):
        return "bn_per_m2"
    if leaf.endswith("_pct") or leaf in ("erp", "governance_discount", "inflation", "rate"):
        return "pct"
    if leaf.endswith("leverage"):
        return "times"
    return "number"


def judgements_block(dist) -> dict:
    rows = []
    for j in dist.judgements:
        rows.append({"id": f"U{j['index'] + 1:02d}", "name": j["name"],
                     "unit": judgement_unit(j["kind"], j["paths"][0]), "kind": j["kind"],
                     "paths": j["paths"], "book": j["book"], "low": j["low"], "high": j["high"],
                     "price_low": j["price_low"], "price_high": j["price_high"],
                     "swing": j["swing"], "share": j["share"]})
    rows.sort(key=lambda r: -r["swing"])
    return {"rows": rows}


def uncertainty_block(A, dist, draws_low, draws_high) -> dict:
    U = A["valuation"]["uncertainty"]
    lam = dist.band.lam
    c = [a + lam * (b - a) for a, b in zip(draws_low, draws_high)]
    step = float(A["valuation"]["headline"]["print_step"])
    lo, hi = min(c), max(c)
    # Ширина корзины — кратное шага печати, корзин не больше HISTOGRAM_MAX_BINS.
    width = step * max(1, math.ceil((hi - lo) / (step * HISTOGRAM_MAX_BINS)))
    start, end = math.floor(lo / width) * width, math.ceil(hi / width) * width
    edges = [start + i * width for i in range(int(round((end - start) / width)) + 1)]
    if len(edges) < 2:
        edges = [start, start + width]
    contrib = sorted(({"axis": x["axis"], "share": x["share"], "rank_corr": x["rank_corr"]}
                      for x in dist.contributions), key=lambda x: -x["share"])
    return {"contributions": contrib, "draws": dist.band.n, "seed": int(U["seed"]),
            "axes_count": len(dist.band.axes), "mean": dist.stats["mean"],
            "histogram_bins": edges, "subsample": dist.subsample, "delta": dist.delta}


def reverse_block(dist, mp) -> dict:
    rows = [{k: r[k] for k in ("name", "unit", "kind", "paths", "book", "solved", "delta",
                               "in_range", "range", "search", "status", "point_solved",
                               "point_status")} for r in dist.reverse_dcf]
    return {"rows": rows, "target": mp,
            "method": f"медиана на подвыборке {dist.subsample} + сдвиг"}


def _events(v: dt.date, until: dt.date | None = None) -> list[dict]:
    if not CALENDAR.exists():
        return []
    raw = json.loads(CALENDAR.read_text(encoding="utf-8"))
    end = until or v + dt.timedelta(days=CALENDAR_DAYS)
    out = []
    for e in raw.get("events") or []:
        d = _day(e.get("date"))
        if d and v <= d <= end:
            out.append({"date": d.isoformat(), "title": e.get("title"), "kind": e.get("kind"),
                        "confirmed": bool(e.get("confirmed")), "note": e.get("note") or None})
    return sorted(out, key=lambda e: e["date"])


def _guidance(F: Facts, period: str | None) -> dict:
    G = F.data.get("guidance")
    empty = {"year": None, "revenue_growth": None, "margin_min": None, "capex_pct": None,
             "openings": None, "required_h2_margin": None, "required_h2_growth": None,
             "net_debt_to_ebitda": None, "src": None}
    if not G:
        return {**empty, "note": "прогноза компании нет (data/facts/guidance.json)"}
    year = G.get("year")
    implied = G.get("implied_" + str(year)) or {}
    h2 = period is not None and period == f"{year}H2"
    growth_h2 = implied.get(f"revenue_{year}H2_growth") if h2 else None
    raw = F.raw.get("guidance") or {}
    return {"year": year, "published": G.get("published"),
            "revenue_growth": G.get("revenue_growth"), "margin_min": G.get("adj_margin_min"),
            "capex_pct": G.get("capex_pct"), "openings": G.get("openings_min"),
            "net_debt_to_ebitda": G.get("net_debt_to_ebitda"),
            "required_h2_margin": implied.get(f"adj_margin_{year}H2_min") if h2 else None,
            "required_h2_growth": growth_h2[0] if isinstance(growth_h2, list) else growth_h2,
            "required_h2_growth_range": growth_h2 if isinstance(growth_h2, list) else None,
            "src": _raw_src(raw.get("revenue_growth")), "note": None}


def next_report_block(A, F, cf, grid, dist, period, inputs) -> tuple[dict, dict]:
    """Блок `next_report` и прогнозы/эталоны для журнала."""
    v = inputs["valuation_date"]
    NR = dist.next_report
    if period is None:
        return ({"period": None, "events": _events(v), "expectation": None, "guidance": _guidance(F, None),
                 "benchmarks": [], "table": [], "neutral": {"median": None, "point": None},
                 "rub_per_01pp": None}, {})
    exp = expectation(grid, period)
    halves = journal_mod.reported_halves(F)
    same = prev_same_half(period)
    base_rev = halves.get(same, {}).get("revenue")
    if base_rev is None:
        i = grid.ctx.P.index(same) if same in grid.ctx.P else None
        base_rev = expected_path(grid)[i]["revenue"] if i is not None else None
    growth = exp["revenue"] / base_rev - 1.0 if base_rev else None
    bench = journal_mod.benchmarks(F, period)
    ends = [e for e in _events(v, v + dt.timedelta(days=2 * CALENDAR_DAYS))
            if e["kind"] == "ifrs" and _day(e["date"]) > period_end(period)]
    until = _day(ends[0]["date"]) if ends else None
    block = {"period": period, "events": _events(v, until),
             "expectation": {"revenue_growth": growth, "revenue": exp["revenue"],
                             "margin": exp["margin"], "adj_ebitda": exp["adj_ebitda"],
                             "by_regime": exp["by_regime"]},
             "guidance": _guidance(F, period),
             "benchmarks": [{"key": k, "name": journal_mod.BENCHMARK_TITLES[k],
                             "margin": bench[k]["margin"], "revenue_growth": bench[k]["revenue_growth"],
                             "note": bench[k]["note"]} for k in journal_mod.BENCHMARKS],
             "table": NR["table"], "neutral": NR["neutral"], "rub_per_01pp": NR["rub_per_01pp"],
             "book_period": A["valuation"]["next_report"]["period"]}
    return block, {"forecasts": {"x5.adj_margin": exp["margin"], "x5.revenue_growth": growth},
                   "bench": bench}


# ----------------------------------------- проверки, входы, плитки


def _expected_ex_dates() -> list[dt.date]:
    if not CALENDAR.exists():
        return []
    raw = json.loads(CALENDAR.read_text(encoding="utf-8"))
    out = []
    for e in raw.get("events") or []:
        title = str(e.get("title") or "").lower()
        if e.get("kind") == "dividend" and ("отсечк" in title or "реестр" in title):
            d = _day(e.get("date"))
            if d:
                out.append(d)
    return out


def checks_block(A, F, grid, gate_list, inv, live, inputs, today) -> dict:
    curve = (live or {}).get("curve") or {}
    nodes = curve.get("nodes") if isinstance(curve, dict) else None
    reg = (F.data.get("dividends") or {}).get("register") or []
    flags = [flag_book_update(A, nodes, today),
             flag_dividend_register(_expected_ex_dates(), reg,
                                    dt.date.fromisoformat(A["meta"]["facts_date"]), today),
             flag_price_fallback(inputs["status"])]
    return {"invariants": [{"name": i.name, "title": INVARIANT_TITLES.get(i.name, i.name),
                            "ok": i.ok, "detail": i.detail} for i in inv],
            "gates": [{"name": g.name, "title": g.title, "fired": g.fired, "mass": g.mass,
                       "status": g.status, "blocking": g.blocking,
                       "explanation": g.explanation or None,
                       "valid_until": g.valid_until.isoformat() if g.valid_until else None,
                       "expected_mass": list(g.expected_mass) if g.expected_mass else None,
                       "cells": len(g.cells), "detail": g.detail if g.fired else None}
                      for g in gate_list],
            "flags": [{"name": f.name, "title": f.title, "raised": f.raised, "detail": f.detail}
                      for f in flags]}


def _curve_z(nodes: dict, t: float) -> float:
    pts = sorted((float(k), float(v)) for k, v in nodes.items())
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, z0), (t1, z1) in zip(pts, pts[1:]):
        if t <= t1:
            return z0 + (z1 - z0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def bond_spread_bp(live: dict | None) -> float | None:
    """Спред фиксированных облигаций X5 к бескупонной кривой ОФЗ на их дюрации,
    б.п., взвешенный по объёму (наблюдение для плитки)."""
    curve = (live or {}).get("curve") or {}
    nodes = curve.get("nodes") if isinstance(curve, dict) else None
    if not nodes:
        return None
    parts = []
    for b in (live or {}).get("bonds") or []:
        d, y, w = b.get("duration_years"), b.get("ytm"), b.get("outstanding")
        if b.get("coupon_type") == "fixed" and _num(d) and _num(y) and _num(w) and d >= 0.25:
            parts.append((w, y - _curve_z(nodes, d)))
    total = fsum(w for w, _ in parts)
    return fsum(w * s for w, s in parts) / total * BP if total > 0 else None


def _tile(tid, title, unit, value, date, history, previous_tiles,
          step_series: bool = False) -> dict | None:
    """Плитка: история — своя (ряд источника) или копится из прошлых выпусков."""
    if value is None:
        return previous_tiles.get(tid)
    if history is None:
        history = list((previous_tiles.get(tid) or {}).get("history") or [])
        history = [h for h in history if h.get("date") != date]
        history.append({"date": date, "value": value})
        history.sort(key=lambda h: h["date"])
    history = _thin(history, TILE_POINTS)
    earlier = [h["value"] for h in history if h["date"] < str(date)]
    if step_series:
        # Ряд смен (ключевая ставка): изменение — к прошлому ОТЛИЧНОМУ значению.
        earlier = [x for x in earlier if x != value]
    return {"id": tid, "title": title, "unit": unit, "value": value, "date": date,
            "change": value - earlier[-1] if earlier else None, "history": history}


def indicators_block(inputs, live, previous) -> dict:
    prev = {t.get("id"): t for t in ((previous or {}).get("indicators") or {}).get("tiles") or []}
    live = live or {}
    hist = [{"date": str(r["date"]), "value": float(r["close"])}
            for r in live.get("price_history") or [] if _num(r.get("close"))]
    tiles = []
    if inputs["status"] != "book":
        price_hist = [h for h in hist if h["date"] < inputs["price_date"]]
        price_hist.append({"date": inputs["price_date"], "value": inputs["market_price"]})
        tiles.append(_tile("x5.price", "Акция X5", "rub", inputs["market_price"],
                           inputs["price_date"], price_hist, prev))
    key = live.get("key_rate") or {}
    if _num(key.get("value")):
        kh = [{"date": str(h["date"]), "value": float(h["value"])} for h in key.get("history") or []
              if _num(h.get("value"))]
        tiles.append(_tile("cbr.key_rate", "Ключевая ставка", "pct", float(key["value"]),
                           str(key.get("date")), kh or None, prev, step_series=True))
    curve = live.get("curve") or {}
    nodes = curve.get("nodes") or {}
    for node, tid in (("5", "ofz.5y"), ("10", "ofz.10y")):
        value = nodes.get(node)
        tiles.append(_tile(tid, f"ОФЗ {node} лет, бескупонная", "pct",
                           float(value) if _num(value) else None, curve.get("as_of"), None, prev))
    spread = bond_spread_bp(live)
    tiles.append(_tile("x5.bond_spread", "Спред облигаций X5 к ОФЗ", "bp", spread,
                       curve.get("as_of"), None, prev))
    return {"tiles": [t for t in tiles if t]}


def live_block(A, live, inputs) -> dict:
    live = live or {}
    price = live.get("price") or {}
    curve = live.get("curve") or {}
    nodes = curve.get("nodes") if isinstance(curve, dict) else None
    book_curve = A["worlds"][A["joint"]["neutral_world"]]["zero_curve"]
    key = live.get("key_rate") or {}
    return {"price": {"value": inputs["market_price"], "date": inputs["price_date"],
                      "time": inputs["price_time"], "source": inputs["price_source"],
                      "status": inputs["status"], "accepted": inputs["accepted"],
                      "reason": inputs["reason"], "kind": price.get("kind"),
                      "last_accepted": price.get("last_accepted")},
            "curve": ({"as_of": curve.get("as_of"), "nodes": nodes,
                       "book_nodes": {k: float(book_curve[k]) for k in nodes if k in book_curve},
                       "shift_bp": {k: (float(nodes[k]) - float(book_curve[k])) * BP
                                    for k in ("5", "10") if k in nodes and k in book_curve}}
                      if nodes else None),
            "key_rate": {"value": key.get("value"), "date": key.get("date")} if key else None,
            "valuation_date": inputs["valuation_date"].isoformat(),
            "fetched_at": live.get("fetched_at"), "errors": live.get("errors") or {},
            "degraded": bool(live.get("errors"))}


def inputs_block(A, inputs, live, flags, v: dt.date) -> dict:
    live = live or {}
    curve, key = live.get("curve") or {}, live.get("key_rate") or {}

    def fresh(date) -> str:
        d = _day(date)
        return "ok" if d and (v - d).days <= STALE_DAYS else "stale"

    status_price = {"live": "ok", "book": "ok", "fallback": "fallback"}.get(inputs["status"], "ok")
    src = inputs["price_source"] + (f", сделка {inputs['price_time']}" if inputs["price_time"] else "")
    book_old = any(f["name"] == "book_update" and f["raised"] for f in flags)
    anchor = A["meta"]["anchor_period"]
    return {"rows": [
        {"name": "Цена акции", "value": inputs["market_price"], "unit": "rub",
         "as_of": inputs["price_date"], "source": src, "status": status_price},
        {"name": "Кривая ОФЗ", "value": "узлы 1, 3, 5, 10 лет" if curve else None, "unit": None,
         "as_of": curve.get("as_of"), "source": curve.get("source") or "ISS zcyc",
         "status": fresh(curve.get("as_of")) if curve else "stale"},
        {"name": "Ключевая ставка", "value": key.get("value"), "unit": "pct",
         "as_of": key.get("date"), "source": key.get("source") or "Банк России",
         "status": "ok" if key else "stale"},
        {"name": "Книга допущений", "value": str(A["meta"]["version"]), "unit": None,
         "as_of": A["meta"]["date"], "source": "data/assumptions",
         "status": "stale" if book_old else "ok"},
        {"name": "Факты отчётности", "value": journal_mod.half_label(anchor), "unit": None,
         "as_of": A["meta"]["facts_date"], "source": "МСФО и databook X5 (data/facts)",
         "status": "ok"}]}


def book_block(A) -> dict:
    src = A["worlds"]["source"]
    kj = []
    for jid, path, name, unit in KEY_JUDGEMENTS:
        try:
            node = get_node(A, path)
        except KeyError:
            continue
        kj.append({"id": jid, "name": name, "value": float(node) if _num(node) else node,
                   "unit": unit, "path": path})
    return {"version": str(A["meta"]["version"]), "date": A["meta"]["date"],
            "tag": f"book-{A['meta']['version']}",
            "sections": [{"id": k, "title": t} for k, t in SECTIONS if k in A],
            "worlds_source": f"миры общие с моделью Магнита 850oa: {src['book']}, кривая "
                             f"{src['curve_date']}",
            "facts_date": A["meta"]["facts_date"], "key_judgements": kj}


# ---------------------------------------------------------------- сборка


def build_payload(live: dict | None = None, previous: dict | None = None, journal=None,
                  fast: bool = False, *, book: dict | None = None, facts: Facts | None = None,
                  strict: bool = True, explanations=None, notes_path=None,
                  n_workers: int | None = None) -> dict:
    """Выпуск `x5-v1` на книге, фактах и живых входах.

    `live` — живые входы конвейера (формат — `model/README.md`) или None (входы
    книги); `previous` — прошлый выпуск (атрибуция, защита заголовка, плитки);
    `journal` — журнал прогнозов ветки `data` (или берётся из `previous`);
    `fast` — полоса на 200 прогонах (в выпуск не идёт).
    `strict` — при инварианте, необъяснённом гейте или нарушении контракта
    бросить `ReleaseBlocked` (конвейер); False — записать как есть (образец).
    """
    A = book if book is not None else load_book()
    F = facts if facts is not None else load_facts()
    cf = core_facts(F, A)
    today = _today()
    inputs = live_inputs(A, live)
    v, mp = inputs["valuation_date"], inputs["market_price"]
    grid = evaluate(A, cf, valuation_date=v, market_price=mp)
    period = open_period(A)
    ex = load_gate_explanations(explanations) if not isinstance(explanations, dict) else explanations
    inv = invariants(grid)
    gate_list = gates(grid, ex, today)
    blocking = blocking_reasons(inv, gate_list)
    if strict and blocking:
        raise ReleaseBlocked(blocking)

    dist = distribution(A, cf, grid, valuation_date=v, market_price=mp, fast=fast,
                        n_workers=n_workers, period=period, round_draws=DRAW_DECIMALS)
    draws_low = [round(x, DRAW_DECIMALS) for x in dist.band.low]
    draws_high = [round(x, DRAW_DECIMALS) for x in dist.band.high]
    headline = headline_block(A, dist, mp)
    paths = paths_block(F, cf, grid)
    next_report, for_journal = next_report_block(A, F, cf, grid, dist, period, inputs)
    checks = checks_block(A, F, grid, gate_list, inv, live, inputs, today)

    previous_journal = journal if journal is not None else (previous or {}).get("journal")
    new_journal, new_ids = journal_mod.update(
        previous_journal, period=period, forecasts=for_journal.get("forecasts") or {},
        bench=for_journal.get("bench") or {}, recorded_at=today.isoformat(),
        actuals=journal_mod.actuals_of(F))

    meta = {"generated_at": generated_at(), "valuation_date": v.isoformat(),
            "facts_date": A["meta"]["facts_date"], "book_version": str(A["meta"]["version"]),
            "book_date": A["meta"]["date"], "engine_commit": engine_commit(),
            "basis": A["meta"]["basis"], "shares_mln": cf.shares_mln,
            "governance_discount": grid.ctx.governance, "anchor_period": A["meta"]["anchor_period"],
            "first_period": A["meta"]["first_period"], "last_period": A["meta"]["last_period"],
            "open_period": period, "curve_as_of": A["meta"]["curve_as_of"],
            "closed_periods": grid.ctx.timing.closed, "elapsed": grid.ctx.timing.elapsed,
            "fast": bool(fast), "payload_sha256": None, "bytes": 0,
            "previous_sha256": ((previous or {}).get("meta") or {}).get("payload_sha256")}
    payload = {
        "schema": SCHEMA, "meta": meta,
        "market": market_block(A, F, cf, grid, inputs, live, previous),
        "headline": headline,
        "fair_value": fair_value_block(A, grid, dist, headline, draws_low, draws_high, mp),
        "layers": {name: _layer(L) for name, L in grid.layers.items()},
        "grid": grid_block(grid), "worlds": worlds_block(A, grid),
        "regimes": regimes_block(A, F, grid), "capex_levels": capex_block(A, F, grid),
        "paths": paths, "debt": debt_block(A, F, cf, grid, live),
        "dividends": dividends_block(A, F, cf, grid, paths, inputs, period),
        "history": history_block(F), "reverse_dcf": reverse_block(dist, mp),
        "judgements": judgements_block(dist),
        "uncertainty": uncertainty_block(A, dist, draws_low, draws_high),
        "next_report": next_report, "journal": new_journal,
        "calendar": {"events": _events(v)}, "checks": checks,
        "inputs": inputs_block(A, inputs, live, checks["flags"], v),
        "live": live_block(A, live, inputs),
        "changes": {"vs_previous": attribution.vs_previous(
            A, cf, previous, valuation_date=v, market_price=mp, point_now=grid.point.central,
            meta_now=meta)},
        "book": book_block(A), "indicators": indicators_block(inputs, live, previous)}
    payload = _tidy(payload)
    _seal(payload, new_ids)

    problems = validate(payload, notes_path=notes_path, previous_journal=previous_journal)
    if problems:
        if strict:
            raise ReleaseBlocked([f"контракт: {p}" for p in problems])
        print("выпуск с нарушениями (strict=False):", *problems, sep="\n  ", file=sys.stderr)
    if not strict and blocking:
        print("сборка была бы заблокирована:", *blocking, sep="\n  ", file=sys.stderr)
    return payload


def _seal(payload: dict, new_ids: list[str]) -> None:
    """Хэш содержания, `release_sha` новых записей журнала, размер."""
    digest = content_digest(payload)
    payload["meta"]["payload_sha256"] = digest
    for e in payload["journal"]["entries"]:
        if e["id"] in new_ids:
            e["release_sha"] = digest
    size = 0
    for _ in range(5):
        payload["meta"]["bytes"] = size
        new = len(compact_json(payload).encode("utf-8"))
        if new == size:
            break
        size = new


# ------------------------------------------------------------ проверка


def _nonfinite(node, where: str, out: list[str]) -> None:
    if isinstance(node, float) and not math.isfinite(node):
        out.append(f"{where} = {node!r}")
    elif isinstance(node, dict):
        for k, v in node.items():
            _nonfinite(v, f"{where}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _nonfinite(v, f"{where}[{i}]", out)


def _read_notes(path=None) -> list[dict]:
    path = Path(path) if path else RELEASE_NOTES
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [n for n in (data.get("notes") or []) if isinstance(n, dict)]


def release_notes_problems(path=None) -> list[str]:
    """Записка без срока или без ожидаемой медианы — ошибка сборки (бессрочное
    разрешение на любой скачок существовать не должно)."""
    out = []
    for i, n in enumerate(_read_notes(path), start=1):
        where = f"release_notes.yaml, записка {i} ({n.get('date', '—')})"
        exp = n.get("expected_median")
        if not _num(exp) or exp <= 0:
            out.append(f"{where}: нет expected_median — ожидаемой медианы после правки, ₽")
        tol = n.get("tolerance_pct", NOTE_TOLERANCE_PCT)
        if not _num(tol) or not 0 < tol <= MAX_HEADLINE_JUMP * 100:
            out.append(f"{where}: tolerance_pct = {tol!r} — нужен процент в (0; 25]")
        if _day(n.get("valid_until")) is None:
            out.append(f"{where}: нет valid_until ГГГГ-ММ-ДД — бессрочная записка запрещена")
    return out


def headline_problems(payload: dict, *, notes_path=None, today: dt.date | None = None) -> list[str]:
    """Защита заголовка (§13.4) против прошлого выпуска (`changes.vs_previous.reference`)."""
    ref = ((payload.get("changes") or {}).get("vs_previous") or {}).get("reference") or {}
    head, meta = payload.get("headline") or {}, payload.get("meta") or {}
    was_head, now_head = ref.get("printed_central"), head.get("printed_central")
    was_v0 = ref.get("v0_median")
    now_v0 = ((payload.get("fair_value") or {}).get("center_ev") or {}).get("v0_median")
    if ref.get("fast") or meta.get("fast"):
        return []           # быстрая сборка — не выпуск, её медиана на 200 прогонах
    jumps = []
    if _num(was_head) and _num(now_head) and was_head > 0 and \
            abs(now_head / was_head - 1) > MAX_HEADLINE_JUMP:
        jumps.append(f"медиана {was_head:.0f} → {now_head:.0f} ₽ ({now_head / was_head - 1:+.0%})")
    if _num(was_v0) and _num(now_v0) and was_v0 > 0 and abs(now_v0 / was_v0 - 1) > MAX_V0_JUMP:
        jumps.append(f"V0 медианы {was_v0:.1f} → {now_v0:.1f} млрд ₽ ({now_v0 / was_v0 - 1:+.1%})")
    if not jumps:
        return []
    if ref.get("book_version") != meta.get("book_version") or \
            ref.get("facts_date") != meta.get("facts_date"):
        return []
    today = today or _today()
    central = head.get("central")
    for n in _read_notes(notes_path):
        until, exp = _day(n.get("valid_until")), n.get("expected_median")
        tol = n.get("tolerance_pct", NOTE_TOLERANCE_PCT)
        if until is None or until < today or not _num(exp) or exp <= 0 or not _num(tol):
            continue
        if _num(central) and abs(central / exp - 1) <= tol / 100:
            return []
    return ["скачок заголовка без новой книги, новых фактов или действующей записки "
            "(data/assumptions/release_notes.yaml): " + "; ".join(jumps)]


def validate(payload: dict, *, notes_path=None, previous_journal=None,
             today: dt.date | None = None) -> list[str]:
    """Нарушения контракта `x5-v1` (пусто — годен)."""
    out: list[str] = []
    if not isinstance(payload, dict):
        return ["выпуск — не объект"]
    if payload.get("schema") != SCHEMA:
        out.append(f"schema = {payload.get('schema')!r}, ожидается {SCHEMA}")
    missing = [b for b in REQUIRED_TOP_LEVEL if b not in payload]
    extra = [b for b in payload if b not in REQUIRED_TOP_LEVEL]
    if missing:
        out.append(f"нет блоков: {', '.join(missing)}")
    if extra:
        out.append(f"необъявленные блоки: {', '.join(extra)}")
    if missing:
        return out
    bad: list[str] = []
    _nonfinite(payload, "$", bad)
    if bad:
        out.append("неконечные числа: " + "; ".join(bad[:5]))
    try:
        size = len(compact_json(payload).encode("utf-8"))
    except ValueError as exc:
        out.append(f"не строгий JSON: {exc}")
        size = None
    if size is not None:
        if size > PAYLOAD_MAX_BYTES:
            out.append(f"размер {size} байт при потолке {PAYLOAD_MAX_BYTES}")
        if payload["meta"].get("bytes") != size:
            out.append(f"meta.bytes = {payload['meta'].get('bytes')} ≠ {size}")
    try:
        if payload["meta"].get("payload_sha256") != content_digest(payload):
            out.append("meta.payload_sha256 не совпадает с содержанием")
    except ValueError:
        pass                        # не строгий JSON — уже названо выше

    head, fv = payload["headline"], payload["fair_value"]
    step = head.get("print_step")
    if not _num(step) or step <= 0:
        out.append(f"headline.print_step = {step!r}")
    else:
        pairs = [("headline.central", head.get("central"), head.get("printed_central"))]
        pairs += [(f"headline.band[{i}]", a, b)
                  for i, (a, b) in enumerate(zip(head.get("band") or [], head.get("printed_band") or []))]
        pairs += [(f"headline.inner[{i}]", a, b)
                  for i, (a, b) in enumerate(zip(head.get("inner") or [], head.get("printed_inner") or []))]
        pairs += [(f"fair_value.{k}", fv.get(k), (fv.get("printed") or {}).get(k))
                  for k in ("low", "central", "high")]
        for where, value, printed in pairs:
            if not _num(value) or printed != round_to_step(value, step):
                out.append(f"печать {where}: {printed!r} ≠ округлению {value!r} к шагу {step}")
        if len(head.get("band") or []) != 2 or len(head.get("inner") or []) != 2:
            out.append("headline.band и inner — пары [нижняя, верхняя]")
    n = head.get("draws")
    if not isinstance(n, int) or n < 1 or len(fv.get("draws_low") or []) != n \
            or len(fv.get("draws_high") or []) != n:
        out.append(f"длины draws_low/draws_high ≠ headline.draws ({n})")
    elif not bad and _num(head.get("lambda")) and _num(head.get("market_price")):
        # Заголовок — статистика прогонов выпуска (ползунок витрины считает так же).
        s = band_stats(fv["draws_low"], fv["draws_high"], head["lambda"], head["market_price"])
        got = [s["median"], s["p10"], s["p90"], s["p25"], s["p75"]]
        want = [head.get("central"), *(head.get("band") or [None] * 2),
                *(head.get("inner") or [None] * 2)]
        if not all(_num(w) and abs(g - w) <= HALF_CENT for g, w in zip(got, want)):
            out.append("заголовок не совпадает со статистикой прогонов draws_low/draws_high")
    rows = fv.get("by_lambda") or []
    if len(rows) != LAMBDA_ROWS or any(
            not _num(r.get("lambda")) or abs(r["lambda"] - i / (LAMBDA_ROWS - 1)) > 1e-9
            for i, r in enumerate(rows)):
        out.append(f"fair_value.by_lambda — не {LAMBDA_ROWS} строк λ = 0; 0,05; …; 1")
    if abs(fv.get("lambda_step", 0) * (LAMBDA_ROWS - 1) - 1) > 1e-9:
        out.append("fair_value.lambda_step ≠ 0,05")
    cells = (payload.get("grid") or {}).get("cells") or []
    if len(cells) != len(WORLDS) * len(REGIMES) * len(CAPEX_LEVELS):
        out.append(f"grid.cells: {len(cells)} клеток")
    ids = [e.get("id") for e in (payload.get("journal") or {}).get("entries") or []]
    if len(ids) != len(set(ids)) or None in ids:
        out.append("journal: id записей не уникальны или пусты")
    if previous_journal is not None:
        out += journal_mod.immutability_problems(previous_journal, payload["journal"])
    out += release_notes_problems(notes_path)
    out += headline_problems(payload, notes_path=notes_path, today=today)
    return out
