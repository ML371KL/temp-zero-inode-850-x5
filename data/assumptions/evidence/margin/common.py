"""Общие функции листа «Маржа и режимы»: ряды X5, траектории книги, правило A-P2u, фильтр уровня.

Всё по docs/MODEL.md: траектории — §0.1, маржа и отклонение — §4.3, правило обновления — §10.
Только стандартная библиотека. Маржа везде — доля выручки (0,057 = 5,7 %).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
REGIMES = ("stress", "floor", "partial", "full")


def load_json(name: str) -> dict:
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))


def write_json(name: str, obj: dict) -> None:
    (HERE / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- ряды X5
def history() -> dict:
    """Кварталы, полугодия, годы: выручка, скорр. EBITDA, маржа; LTI где раскрыт."""
    d = load_json("x5_margin_history.json")
    q = d["quarters"]
    quarters = {p: {"rev": v["revenue"]["v"], "ebitda": v["adj_ebitda"]["v"],
                    "lti": v.get("lti", {}).get("v")} for p, v in q.items()}
    halves: dict[str, dict] = {}
    for y in range(2011, 2027):
        for h, qs in ((1, (1, 2)), (2, (3, 4))):
            ks = [f"{y}Q{i}" for i in qs]
            if all(k in quarters for k in ks):
                rev = sum(quarters[k]["rev"] for k in ks)
                eb = sum(quarters[k]["ebitda"] for k in ks)
                ltis = [quarters[k]["lti"] for k in ks]
                lti = sum(ltis) if all(x is not None for x in ltis) else None
                halves[f"{y}H{h}"] = {"rev": rev, "ebitda": eb, "m": eb / rev, "lti": lti}
    years = {int(y): {"rev": v["revenue"]["v"], "ebitda": v["adj_ebitda"]["v"],
                      "m": v["adj_ebitda"]["v"] / v["revenue"]["v"], "lti": v.get("lti", {}).get("v")}
             for y, v in d["years"].items()}
    for p, v in quarters.items():
        v["m"] = v["ebitda"] / v["rev"]
    return {"quarters": quarters, "halves": halves, "years": years}


def half_index(p: str) -> int:
    """'2026H1' → номер полугодия на общей линейке."""
    return int(p[:4]) * 2 + (int(p[-1]) - 1)


def half_name(i: int) -> str:
    return f"{i // 2}H{i % 2 + 1}"


# ---------------------------------------------------------------- траектории книги (MODEL §0.1)
def path_value(path: dict, period: str) -> float:
    """Значение траектории в полугодии: точный ключ → ключ года → интерполяция по годам → сход к LT к LT_from."""
    if period in path:
        return float(path[period])
    year = int(period[:4])
    if str(year) in path:
        return float(path[str(year)])
    years = sorted(int(k) for k in path if k.isdigit())
    lt = path.get("LT")
    lt_from = path.get("LT_from")
    if years and years[0] <= year <= years[-1]:
        lo = max(y for y in years if y <= year)
        hi = min(y for y in years if y >= year)
        if lo == hi:
            return float(path[str(lo)])
        a, b = float(path[str(lo)]), float(path[str(hi)])
        return a + (b - a) * (year - lo) / (hi - lo)
    if years and year > years[-1]:
        last = float(path[str(years[-1])])
        if lt is None:
            return last
        if lt_from is None or year >= int(lt_from):
            return float(lt)
        return last + (float(lt) - last) * (year - years[-1]) / (int(lt_from) - years[-1])
    # до первого года — ключи полугодий (якорь, первое прогнозное)
    halves = sorted((k for k in path if "H" in k), key=half_index)
    if halves:
        return float(path[halves[-1]])
    raise KeyError(period)


# ---------------------------------------------------------------- правило A-P2u (MODEL §10)
def cap_shift(prior: dict, post: dict, cap: float) -> dict:
    """Предел сдвига: обрезка ±cap, перенормировка, при выходе за предел — пропорциональное сжатие."""
    sh = {r: max(-cap, min(cap, post[r] - prior[r])) for r in prior}
    p = {r: prior[r] + sh[r] for r in prior}
    s = sum(p.values())
    p = {r: v / s for r, v in p.items()}
    sh = {r: p[r] - prior[r] for r in prior}
    mx = max(abs(v) for v in sh.values())
    if mx > cap + 1e-15:
        k = cap / mx
        p = {r: prior[r] + sh[r] * k for r in prior}
    return p


def regime_update(prior: dict, targets: dict, anchor: str, anchor_fact: float, observations: list,
                  sigma: float, rho: float, cap: float, season=lambda p: 0.0, trace: list | None = None) -> dict:
    """Вероятности режимов после наблюдений (MODEL §10). targets[r] — траектория книги (словарь ключей).

    observations: [{"period", "value", "se"}] в порядке времени. Возвращает posterior; trace — шаги.
    """
    state = {}
    for r in prior:
        dev0 = anchor_fact - path_value(targets[r], anchor) - season(anchor)
        state[r] = [dev0, 0.0, half_index(anchor)]          # m, v, индекс последнего наблюдения
    p = dict(prior)
    for ob in observations:
        per, val, se = ob["period"], ob["value"], ob.get("se", 0.0)
        idx = half_index(per)
        lik, errs, dev = {}, {}, {}
        for r in p:
            m, v, last = state[r]
            k = idx - last
            dev[r] = val - path_value(targets[r], per) - season(per)
            e = dev[r] - rho ** k * m
            P = sigma ** 2 * (1 - rho ** (2 * k)) + rho ** (2 * k) * v
            lik[r] = math.exp(-0.5 * e * e / (P + se * se))
            errs[r] = (e, P, k)
        z = sum(p[r] * lik[r] for r in p)
        post = {r: p[r] * lik[r] / z for r in p}
        newp = cap_shift(p, post, cap)
        if trace is not None:
            trace.append({"period": per, "value": val, "prior": dict(p), "raw": post, "post": dict(newp),
                          "err": {r: errs[r][0] for r in p}, "P": {r: errs[r][1] for r in p}})
        for r in p:
            e, P, k = errs[r]
            m, v, _ = state[r]
            if se == 0:
                state[r] = [dev[r], 0.0, idx]
            else:
                w = P / (P + se * se)
                state[r] = [rho ** k * m + w * e, (1 - w) * P, idx]
        p = newp
    return p


# ---------------------------------------------------------------- уровень + AR(1): фильтр Калмана
def kalman_level_ar1(y: list[float], rho: float, sig: float, q: float, season: list[float] | None = None):
    """Модель наблюдения: y_t = L_t + d_t + s_t; L — случайное блуждание (шаг q), d — AR(1) (ρ, стационарное σ).

    Уровень на старте диффузный. Возвращает (логарифм правдоподобия, [(L̂, d̂, sd L̂, инновация, F)]).
    """
    big = 1e4
    s = season or [0.0] * len(y)
    x = [y[0] - s[0], 0.0]
    P = [[big, 0.0], [0.0, sig * sig]]
    ll, out = 0.0, []
    for t, yt in enumerate(y):
        if t > 0:
            x = [x[0], rho * x[1]]
            P = [[P[0][0] + q * q, rho * P[0][1]],
                 [rho * P[1][0], rho * rho * P[1][1] + sig * sig * (1 - rho * rho)]]
        F = P[0][0] + P[0][1] + P[1][0] + P[1][1]
        v = yt - s[t] - x[0] - x[1]
        if P[0][0] < big / 10:
            ll += -0.5 * (math.log(2 * math.pi * F) + v * v / F)
        K = [(P[0][0] + P[0][1]) / F, (P[1][0] + P[1][1]) / F]
        x = [x[0] + K[0] * v, x[1] + K[1] * v]
        P = [[P[0][0] - K[0] * (P[0][0] + P[1][0]), P[0][1] - K[0] * (P[0][1] + P[1][1])],
             [P[1][0] - K[1] * (P[0][0] + P[1][0]), P[1][1] - K[1] * (P[0][1] + P[1][1])]]
        out.append((x[0], x[1], math.sqrt(max(P[0][0], 0.0)), v, F))
    return ll, out


def fit_level_ar1(y: list[float], rhos, sigs, qs, season=None):
    """Максимум правдоподобия на сетке; профиль по ρ. Возвращает (лучшее, профиль ρ → ll)."""
    best, prof = None, {}
    for rho in rhos:
        for sg in sigs:
            for q in qs:
                ll, _ = kalman_level_ar1(y, rho, sg, q, season)
                if best is None or ll > best[0]:
                    best = (ll, rho, sg, q)
                if ll > prof.get(rho, -1e18):
                    prof[rho] = ll
    return best, prof


def frange(a: float, b: float, step: float) -> list[float]:
    n = int(round((b - a) / step))
    return [round(a + i * step, 10) for i in range(n + 1)]


def pct(x: float, nd: int = 2) -> str:
    """0,05698 → '5,70'."""
    return f"{x * 100:.{nd}f}".replace(".", ",")
