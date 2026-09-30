"""Закрытая схема книги допущений X5: ключи, типы, обязательность.

Ядро не угадывает опечаток: ключ, которого нет в схеме, пропуск обязательного
ключа и значение не того типа — отказ `BookError` с путём и причиной. Схема —
снимок книги (все ключи обязательны), а не вывод из текущего файла на лету:
иначе опечатка в новой книге сама себя узаконила бы. Новый ключ книги
регистрируется здесь тем же изменением, что и код, который его читает.

Кроме типов проверяется то, без чего книга читается неоднозначно
(docs/MODEL.md §0.1, §3):

* траектории компании — без ключа полугодия на `meta.last_period` (терминал
  прочёл бы его как LT); ключи — строки полугодий, годов, `LT`, `LT_from`;
* траектории миров — ровно полугодия от `first_period` до `last_period`;
* вероятности — в сумме 1; периоды мета согласованы;
* `worlds.source.kernel_sha256` — целостность миров самой книги: хэш их содержимого
  (`kernel_sha256`); происхождение миров (книга Магнита на теге `worlds.source`) сверяет
  `ops/tools/check_worlds.py`, не схема;
* пути осей полосы и обратного DCF существуют в книге и подходят к `kind`.

Числа книги здесь не встречаются: только имена ключей и форма значений.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from typing import Any


class BookError(ValueError):
    """Книга допущений не читается однозначно — считать по ней нельзя."""


# Имена осей сетки (docs/MODEL.md §3). Порядок — порядок печати.
WORLDS = ("N", "H", "M")
REGIMES = ("stress", "floor", "partial", "full")
CAPEX_LEVELS = ("low", "base", "high")
DEMANDS = ("bear", "base", "bull")
TARIFFS = ("low", "mid", "high")
CREDITS = ("base", "stress")
AXIS_KINDS = ("value", "shift", "dict")
REVERSE_KINDS = ("value", "shift")
CURVE_NODES = ("1", "3", "5", "10", "LT")

PERIOD_RE = re.compile(r"^(\d{4})H([12])$")
YEAR_RE = re.compile(r"^\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# Допуск суммы вероятностей книги.
PROB_SUM_TOL = 1e-9


def _keyed(keys: tuple[str, ...], spec: Any) -> tuple:
    """Словарь ровно с ключами `keys`, значения — `spec`."""
    return ("keyed", keys, spec)


def _list(spec: Any) -> tuple:
    return ("list", spec)


_OBSERVATION = {"period": "period", "value": "num", "se": "nonneg"}
_WORLD = {"name": "str", "key_rate": "wtraj", "cpi": "wtraj", "food_cpi": "wtraj",
          "zero_curve": "curve", "lt": {"inflation": "num"}}

# Схема книги. Листья — типы (`_check_leaf`), словари — закрытые блоки.
SCHEMA: dict[str, Any] = {
    "meta": {"version": "str", "date": "date", "facts_date": "date", "anchor_period": "period",
             "first_period": "period", "last_period": "period", "valuation_date": "date",
             "market_price": "pos", "curve_as_of": "date", "basis": "str"},
    "worlds": {"source": {"book": "str", "curve_date": "date", "tag_commit": "str",
                          "kernel_sha256": "sha256"},
               **{w: _WORLD for w in WORLDS}},
    "joint": {"world_prob": _keyed(WORLDS, "prob"),
              "market_implied_prob": _keyed(WORLDS, "prob"),
              "neutral_world": "world", "lambda": "prob",
              "regime_prob": _keyed(REGIMES, "prob"),
              "capex_prob_given_regime": _keyed(REGIMES, _keyed(CAPEX_LEVELS, "prob")),
              "regime_demand": _keyed(REGIMES, "demand"),
              "world_links": _keyed(WORLDS, {"growth": "tariff", "credit": "credit"}),
              "stress_growth": "tariff",
              "regime_update": {"sigma_pp": "pos", "cap_pp": "prob",
                                "observations": _list(_OBSERVATION)}},
    "network": {"net_growth": _keyed(TARIFFS, "traj"), "close_rate": "traj",
                "maturity_curve": "maturity", "new_space_density": "pos",
                "closed_productivity": "nonneg"},
    "revenue": {"ticket_k": _keyed(DEMANDS, "num"), "ticket_shift": _keyed(DEMANDS, "traj"),
                "traffic": _keyed(DEMANDS, "traj"), "other_growth": "traj", "vat_effect": "traj",
                "homogeneity": {"ramp_from": "year", "ramp_to": "year",
                                "reference_world": "world"}},
    "margin": {"targets": _keyed(REGIMES, "traj"), "seasonal_h1_pp": "num",
               "deviation_persistence": "persistence", "lti_pct": "nonneg"},
    "capex": {"maintenance": _keyed(CAPEX_LEVELS, "traj"), "maintenance_area_share": "prob",
              "price_per_m2": "nonneg", "infra_per_m2": "nonneg", "infra_from_year": "year",
              "asset_life_years": "life", "disposal_proceeds_pct": "nonneg"},
    "working_capital": {"nwc_pct": "traj", "h1_excess_pct": "num",
                        "operating_cash_pct": "nonneg", "lease_adj_pct": "num"},
    "tax": {"rate": "prob", "permanent_add_pct": "num"},
    "financing": {"fixed_share": "traj01", "legacy_rate": "num", "legacy_weight": "traj01",
                  "spread_float": _keyed(CREDITS, "num"), "spread_fixed": _keyed(CREDITS, "num"),
                  "fixed_coupon_freq": "count", "issuance_cost": "nonneg",
                  "cash_buffer_pct": "nonneg", "cash_yield_k": "nonneg",
                  "target_leverage": "nonneg", "dividends_from": "period"},
    "bridge": {"include": _list("str")},
    "valuation": {"beta_u": "nonneg", "erp": "num", "governance_discount": "discount",
                  "treasury_sale_price_k": "nonneg",
                  "headline": {"print_step": "pos"},
                  "uncertainty": {"draws": "count", "seed": "int", "axes": _list("axis")},
                  "reverse_dcf": {"subsample": "count", "axes": _list("raxis")},
                  "next_report": {"period": "period", "demo_values": _list("num")}},
    "checks": {"ev_ebitda": _keyed(WORLDS, "range"), "margin_range": "range",
               "capex_range": "range", "terminal_share": "range", "real_rate": "range",
               "max_leverage": "pos", "min_v0_to_d": "pos",
               "book_update": {"shift_bp": "pos", "max_age_days": "count"}},
}

# Вероятностные словари книги: сумма — 1.
PROB_DICTS = ("joint.world_prob", "joint.market_implied_prob", "joint.regime_prob",
              *(f"joint.capex_prob_given_regime.{r}" for r in REGIMES))

_AXIS_KEYS = ("name", "kind", "paths", "low", "high")
_RAXIS_KEYS = ("name", "kind", "paths", "search", "range", "unit")


# ------------------------------------------------------------------ листья


def is_number(x: Any) -> bool:
    """Число книги: int или float, не bool, конечное."""
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(float(x)))


def is_period(key: Any) -> bool:
    return isinstance(key, str) and PERIOD_RE.match(key) is not None


def is_year_key(key: Any) -> bool:
    return isinstance(key, str) and YEAR_RE.match(key) is not None


def is_trajectory(node: Any) -> bool:
    """Словарь-траектория: все ключи — полугодия, годы, `LT` или `LT_from`."""
    return (isinstance(node, dict) and bool(node)
            and all(is_period(k) or is_year_key(k) or k in ("LT", "LT_from") for k in node))


def _date_ok(x: Any) -> bool:
    if not isinstance(x, str) or not DATE_RE.match(x):
        return False
    try:
        dt.date.fromisoformat(x)
    except ValueError:
        return False
    return True


def _type_error(where: str, want: str, got: Any) -> str:
    hint = ""
    if isinstance(got, (dt.date, dt.datetime)):
        hint = " (дата без кавычек читается YAML объектом — пишите \"ГГГГ-ММ-ДД\")"
    elif isinstance(got, bool):
        hint = " (true/false не число)"
    return f"{where}: ожидается {want}, а не {got!r}{hint}"


def _check_trajectory(node: Any, where: str, out: list[str], unit: bool = False) -> None:
    """Траектория компании: форма ключей и значений (docs/MODEL.md §0.1)."""
    if not isinstance(node, dict) or not node:
        out.append(_type_error(where, "траектория {ключ: значение}", node))
        return
    for key, value in node.items():
        if not isinstance(key, str):
            out.append(f"{where}: ключ {key!r} — не строка; ключи траекторий пишутся строками "
                       "(\"2027\", \"2026H2\", LT, LT_from)")
            continue
        if key == "LT_from":
            if isinstance(value, bool) or not isinstance(value, int):
                out.append(_type_error(f"{where}.LT_from", "целый год", value))
            continue
        if not (is_period(key) or is_year_key(key) or key == "LT"):
            out.append(f"{where}: ключ {key!r} — не полугодие ГГГГH1/ГГГГH2, не год ГГГГ, "
                       "не LT и не LT_from")
            continue
        if not is_number(value):
            out.append(_type_error(f"{where}.{key}", "число", value))
        elif unit and not 0.0 <= value <= 1.0:
            out.append(f"{where}.{key}: доля {value!r} вне [0; 1]")
    if "LT_from" in node and "LT" not in node:
        out.append(f"{where}: LT_from без LT")
    if set(node) <= {"LT_from"}:
        out.append(f"{where}: в траектории нет ни одного значения")


def _check_leaf(kind: str, node: Any, where: str, out: list[str]) -> None:
    """Проверка листа схемы по его типу."""
    if kind == "str":
        if not isinstance(node, str) or not node:
            out.append(_type_error(where, "непустая строка", node))
    elif kind == "date":
        if not _date_ok(node):
            out.append(_type_error(where, "дата \"ГГГГ-ММ-ДД\"", node))
    elif kind == "period":
        if not is_period(node):
            out.append(_type_error(where, "полугодие \"ГГГГH1\" или \"ГГГГH2\"", node))
    elif kind == "sha256":
        if not isinstance(node, str) or not SHA_RE.match(node):
            out.append(_type_error(where, "sha256 (64 шестнадцатеричных знака)", node))
    elif kind in ("num", "pos", "nonneg", "prob", "persistence", "discount", "life"):
        if not is_number(node):
            out.append(_type_error(where, "число", node))
            return
        x = float(node)
        bad = {"pos": x <= 0, "nonneg": x < 0, "prob": not 0 <= x <= 1,
               "persistence": not -1 < x < 1, "discount": not 0 <= x < 1,
               "life": x <= 0 or not float(2 * x).is_integer()}.get(kind, False)
        if bad:
            want = {"pos": "положительное число", "nonneg": "неотрицательное число",
                    "prob": "доля в [0; 1]", "persistence": "коэффициент AR(1) в (−1; 1)",
                    "discount": "доля в [0; 1)",
                    "life": "положительный срок, кратный половине года"}[kind]
            out.append(f"{where}: {node!r} — ожидается {want}")
    elif kind in ("int", "count", "year"):
        if isinstance(node, bool) or not isinstance(node, int):
            out.append(_type_error(where, "целое число", node))
        elif kind == "count" and node < 1:
            out.append(f"{where}: {node!r} — ожидается целое ≥ 1")
    elif kind in ("world", "demand", "tariff", "credit"):
        allowed = {"world": WORLDS, "demand": DEMANDS, "tariff": TARIFFS, "credit": CREDITS}[kind]
        if node not in allowed:
            out.append(f"{where}: {node!r} — ожидается одно из {', '.join(allowed)}")
    elif kind in ("traj", "traj01"):
        _check_trajectory(node, where, out, unit=kind == "traj01")
    elif kind == "wtraj":
        if not isinstance(node, dict) or not node:
            out.append(_type_error(where, "траектория мира {полугодие: значение}", node))
            return
        for key, value in node.items():
            if not is_period(key):
                out.append(f"{where}: ключ {key!r} — траектории миров задаются только "
                           "полугодиями ГГГГH1/ГГГГH2")
            elif not is_number(value):
                out.append(_type_error(f"{where}.{key}", "число", value))
    elif kind == "curve":
        if not isinstance(node, dict):
            out.append(_type_error(where, "кривая {\"1\", \"3\", \"5\", \"10\", LT}", node))
            return
        if set(node) != set(CURVE_NODES):
            out.append(f"{where}: узлы кривой {sorted(map(str, node))} — ожидаются ровно "
                       f"{', '.join(CURVE_NODES)} (строками)")
        for key, value in node.items():
            if not is_number(value):
                out.append(_type_error(f"{where}.{key}", "число", value))
    elif kind == "maturity":
        if (not isinstance(node, list) or not node or not all(is_number(x) for x in node)):
            out.append(_type_error(where, "список долей [μ0, …, 1]", node))
            return
        if any(not 0 <= x <= 1 for x in node) or any(b < a for a, b in zip(node, node[1:])):
            out.append(f"{where}: доли кривой созревания — неубывающие в [0; 1]")
        if node[-1] != 1:
            out.append(f"{where}: кривая созревания кончается 1 (зрелая продуктивность)")
    elif kind == "range":
        if (not isinstance(node, list) or len(node) != 2
                or not all(is_number(x) for x in node)):
            out.append(_type_error(where, "коридор [нижняя, верхняя]", node))
        elif node[0] > node[1]:
            out.append(f"{where}: нижняя граница коридора больше верхней")
    elif kind == "axis":
        _check_axis(node, where, out, _AXIS_KEYS, AXIS_KINDS)
    elif kind == "raxis":
        _check_axis(node, where, out, _RAXIS_KEYS, REVERSE_KINDS)
    else:  # pragma: no cover — опечатка в самой схеме
        raise AssertionError(f"схема: неизвестный тип {kind}")


def _check_axis(node: Any, where: str, out: list[str], keys: tuple, kinds: tuple) -> None:
    """Ось полосы (`axis`) или обратного DCF (`raxis`): ключи, kind, пути, концы."""
    if not isinstance(node, dict):
        out.append(_type_error(where, "ось {name, kind, paths, …}", node))
        return
    name = node.get("name")
    where = f"{where} «{name}»" if isinstance(name, str) else where
    _closed(node, keys, where, out)
    if not isinstance(name, str) or not name:
        out.append(_type_error(f"{where}.name", "непустая строка", name))
    kind = node.get("kind")
    if kind not in kinds:
        out.append(f"{where}.kind = {kind!r} — ожидается одно из {', '.join(kinds)}")
    paths = node.get("paths")
    if (not isinstance(paths, list) or not paths
            or not all(isinstance(p, str) and p for p in paths)):
        out.append(_type_error(f"{where}.paths", "непустой список путей-строк", paths))
    if "search" in keys:
        for end in ("search", "range"):
            if end in node:
                _check_leaf("range", node[end], f"{where}.{end}", out)
        if "unit" in node and not isinstance(node["unit"], str):
            out.append(_type_error(f"{where}.unit", "строка", node["unit"]))
        return
    for end in ("low", "high"):
        if end not in node:
            continue
        value = node[end]
        if kind == "dict":
            if (not isinstance(value, dict) or set(value) != set(WORLDS)
                    or not all(is_number(v) and v >= 0 for v in value.values())):
                out.append(_type_error(f"{where}.{end}", "веса миров {N, H, M}", value))
        elif not is_number(value):
            out.append(_type_error(f"{where}.{end}", "число", value))


def _closed(node: dict, keys, where: str, out: list[str]) -> None:
    """Закрытый блок: незнакомые и пропущенные ключи."""
    unknown = sorted(str(k) for k in node if k not in keys)
    if unknown:
        out.append(f"{where or 'книга'}: незнакомый ключ {', '.join(unknown)} "
                   f"(известны: {', '.join(keys)})")
    missing = [k for k in keys if k not in node]
    if missing:
        out.append(f"{where or 'книга'}: нет обязательного ключа {', '.join(missing)}")


def _walk(spec: Any, node: Any, where: str, out: list[str]) -> None:
    if isinstance(spec, dict):
        if not isinstance(node, dict):
            out.append(_type_error(where or "книга", "блок {ключ: значение}", node))
            return
        _closed(node, tuple(spec), where, out)
        for key, sub in spec.items():
            if key in node:
                _walk(sub, node[key], f"{where}.{key}" if where else key, out)
    elif isinstance(spec, tuple) and spec[0] == "keyed":
        _, keys, sub = spec
        if not isinstance(node, dict):
            out.append(_type_error(where, f"словарь {{{', '.join(keys)}}}", node))
            return
        _closed(node, keys, where, out)
        for key in keys:
            if key in node:
                _walk(sub, node[key], f"{where}.{key}", out)
    elif isinstance(spec, tuple) and spec[0] == "list":
        if not isinstance(node, list):
            out.append(_type_error(where, "список", node))
            return
        for i, item in enumerate(node):
            _walk(spec[1], item, f"{where}[{i}]", out)
    else:
        _check_leaf(spec, node, where, out)


# ------------------------------------------------------- смысловые проверки


def kernel_sha256(worlds: dict) -> str:
    """Хэш содержимого миров (без `source`): JSON с сортировкой ключей,
    без пробелов, UTF-8 — так он записан в `worlds.source.kernel_sha256`.

    Схема сверяет его только с собственными мирами книги: хэш ловит правку миров без
    пересчёта хэша. Совпадение миров с книгой Магнита на теге `worlds.source` (общая
    макро-основа) хэш не доказывает — это проверяет `ops/tools/check_worlds.py`."""
    kernel = {k: v for k, v in worlds.items() if k != "source"}
    text = json.dumps(kernel, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _period_index(p: str) -> int:
    m = PERIOD_RE.match(p)
    return 2 * int(m.group(1)) + int(m.group(2)) - 1


def _period_from_index(i: int) -> str:
    return f"{i // 2}H{i % 2 + 1}"


def _span(first: str, last: str) -> list[str]:
    return [_period_from_index(i) for i in range(_period_index(first), _period_index(last) + 1)]


def get_node(A: dict, path: str) -> Any:
    """Узел книги по пути через точку; нет узла — KeyError с путём."""
    node: Any = A
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


# Траектории компании (docs/MODEL.md §0.1): ключ полугодия на last_period запрещён.
def company_trajectories(A: dict) -> list[tuple[str, dict]]:
    """Пары (путь, траектория) всех траекторий компании книги."""
    out = []

    def visit(spec: Any, node: Any, where: str) -> None:
        if isinstance(spec, dict) and isinstance(node, dict):
            for key, sub in spec.items():
                if key in node:
                    visit(sub, node[key], f"{where}.{key}" if where else key)
        elif isinstance(spec, tuple) and spec[0] == "keyed" and isinstance(node, dict):
            for key in spec[1]:
                if key in node:
                    visit(spec[2], node[key], f"{where}.{key}")
        elif spec in ("traj", "traj01") and isinstance(node, dict):
            out.append((where, node))

    visit(SCHEMA, A, "")
    return out


def _semantic(A: dict, out: list[str]) -> None:
    """Согласованность книги после проверки формы."""
    M = A["meta"]
    first, last, anchor = M["first_period"], M["last_period"], M["anchor_period"]
    if _period_index(anchor) != _period_index(first) - 1:
        out.append(f"meta.anchor_period {anchor} — не полугодие перед first_period {first}")
    if _period_index(last) < _period_index(first):
        out.append(f"meta.last_period {last} раньше first_period {first}")
    if not last.endswith("H2"):
        out.append(f"meta.last_period {last}: явный участок кончается вторым полугодием "
                   "(терминал — два полугодия следующего года)")
    year, half = int(anchor[:4]), anchor[5]
    anchor_end = f"{year}-06-30" if half == "1" else f"{year}-12-31"
    if M["facts_date"] != anchor_end:
        out.append(f"meta.facts_date {M['facts_date']} — не конец якоря {anchor} ({anchor_end})")
    if A["worlds"]["source"]["curve_date"] != M["curve_as_of"]:
        out.append(f"worlds.source.curve_date {A['worlds']['source']['curve_date']} ≠ "
                   f"meta.curve_as_of {M['curve_as_of']}: миры сняты на другую дату")

    span = set(_span(first, last))
    for w in WORLDS:
        for name in ("key_rate", "cpi", "food_cpi"):
            keys = set(A["worlds"][w][name])
            lack, extra = sorted(span - keys), sorted(keys - span)
            if lack or extra:
                out.append(f"worlds.{w}.{name}: траектория мира задаётся ровно полугодиями "
                           f"{first}…{last}" + (f"; нет {', '.join(lack[:3])}" if lack else "")
                           + (f"; лишние {', '.join(extra[:3])}" if extra else ""))
    got, stated = kernel_sha256(A["worlds"]), A["worlds"]["source"]["kernel_sha256"]
    if got != stated:
        out.append(f"worlds.source.kernel_sha256 {stated[:12]}… не совпадает с содержимым "
                   f"миров книги ({got[:12]}…): миры правлены без пересчёта хэша (происхождение "
                   "миров из книги Магнита сверяет ops/tools/check_worlds.py)")

    for where, node in company_trajectories(A):
        if last in node:
            out.append(f"{where}: ключ полугодия {last} на последнем периоде явного участка "
                       "запрещён — терминал прочёл бы его как LT (docs/MODEL.md §0.1)")

    for path in PROB_DICTS:
        values = get_node(A, path).values()
        if all(is_number(v) for v in values) and abs(math.fsum(values) - 1) > PROB_SUM_TOL:
            out.append(f"{path}: вероятности в сумме {math.fsum(values)!r}, а не 1")

    J = A["joint"]
    seen = set()
    for i, obs in enumerate(J["regime_update"]["observations"]):
        p = obs["period"]
        if not (_period_index(first) <= _period_index(p) <= _period_index(last)):
            out.append(f"joint.regime_update.observations[{i}]: период {p} вне {first}…{last} "
                       "(факт якоря — в фактах, а не в наблюдениях)")
        if p in seen:
            out.append(f"joint.regime_update.observations[{i}]: период {p} уже наблюдён")
        seen.add(p)

    H = A["revenue"]["homogeneity"]
    if H["ramp_to"] <= H["ramp_from"]:
        out.append("revenue.homogeneity: ramp_to должен быть позже ramp_from")
    nr = A["valuation"]["next_report"]["period"]
    if not (_period_index(first) <= _period_index(nr) <= _period_index(last)):
        out.append(f"valuation.next_report.period {nr} вне {first}…{last}")
    include = A["bridge"]["include"]
    if len(set(include)) != len(include):
        out.append("bridge.include: строка моста повторяется")

    for block in ("uncertainty", "reverse_dcf"):
        for i, axis in enumerate(A["valuation"][block]["axes"]):
            _check_axis_paths(A, axis, f"valuation.{block}.axes[{i}] «{axis['name']}»", out)


def _check_axis_paths(A: dict, axis: dict, where: str, out: list[str]) -> None:
    """Путь оси существует и подходит к `kind` (опечатка в пути — пустой прогон)."""
    for path in axis["paths"]:
        try:
            node = get_node(A, path)
        except KeyError:
            out.append(f"{where}: пути {path} нет в книге")
            continue
        kind = axis["kind"]
        if kind == "value" and not is_number(node):
            out.append(f"{where}: kind value — путь {path} должен вести к числу")
        elif kind == "shift" and not (is_number(node) or is_trajectory(node)):
            out.append(f"{where}: kind shift — путь {path} должен вести к числу или траектории")
        elif kind == "dict" and not (isinstance(node, dict) and set(node) == set(WORLDS)):
            out.append(f"{where}: kind dict — путь {path} должен вести к весам миров")


def validate_book(A: Any) -> None:
    """Громкий отказ на книге, которую ядро прочло бы не так, как она написана."""
    out: list[str] = []
    _walk(SCHEMA, A, "", out)
    if not out:
        _semantic(A, out)
    if out:
        rest = out[8:]
        more = f"; и ещё {len(rest)}" if rest else ""
        raise BookError("книга: " + "; ".join(out[:8]) + more)
