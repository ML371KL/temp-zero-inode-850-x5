"""MOEX ISS: цена X5 и аналогов, дневная история закрытий, бескупонная кривая
ОФЗ (zcyc), облигации ООО «ИКС 5 ФИНАНС».

Бесплатный ISS отдаёт рынок с задержкой 15 минут. Каждый ответ ложится в сырой
архив до разбора (`indicators.http.fetch`). Разбор — чистые функции `parse_*`
(их проверяют тесты на сохранённых ответах), сбор — `fetch_*` с подменяемым
`getter` (в тестах — отдача фикстур).

Единицы на выходе: цены акций — ₽; цена облигации — % номинала (рыночная
конвенция); ставки, доходности и купоны — доли единицы; деньги — млрд ₽;
дюрация — годы; даты — ISO, время сделки — ЧЧ:ММ:СС по Москве.
"""

from __future__ import annotations

import urllib.parse
from datetime import date
from typing import Callable

from indicators.http import fetch

ISS = "https://iss.moex.com/iss"
SOURCE = "iss"
BOARD = "TQBR"
X5 = "X5"
PEERS = ("MGNT", "LENT", "FIXR", "OKEY")
# ООО «ИКС 5 ФИНАНС» (ИНН 7715630469) — эмитент облигаций группы:
# https://iss.moex.com/iss/emitters/1259.json (проверено 28.09.2026).
X5_BOND_EMITTER = 1259
BOND_BOARD = "TQCB"
CURVE_NODES = ("1", "3", "5", "10")
# Узел кривой вне 3–40 % годовых — не рынок, а единицы (проценты вместо долей,
# доли, поделённые ещё раз) или мусор. Коридор — как у Магнита 850oa.
RATE_MIN, RATE_MAX = 0.03, 0.40
# Предохранитель постраничного обхода истории: год — это 3 страницы по 100.
MAX_HISTORY_PAGES = 20

Getter = Callable[..., object]


class IssError(RuntimeError):
    """Ответ ISS пришёл, но не годится (нет блока, нет нужных полей, мусор)."""


def _rows(payload: dict, block: str) -> list[dict]:
    """Блок ISS `{columns, data}` → список словарей."""
    try:
        part = payload[block]
        columns = part["columns"]
        return [dict(zip(columns, row)) for row in part["data"]]
    except (KeyError, TypeError) as exc:
        raise IssError(f"в ответе нет блока {block!r}") from exc


def _query(params: dict) -> str:
    return urllib.parse.urlencode(params, safe=",.")


def _day(stamp) -> str | None:
    """«ГГГГ-ММ-ДД…» → ISO-дата; мусор и нули ISS («0000-00-00») → None."""
    try:
        return date.fromisoformat(str(stamp)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# ------------------------------------------------------------------ котировки

QUOTE_SECURITY_COLUMNS = "SECID,SHORTNAME,ISIN,PREVPRICE,PREVLEGALCLOSEPRICE,PREVDATE"
QUOTE_MARKET_COLUMNS = "SECID,LAST,TIME,UPDATETIME,SYSTIME,TRADINGSTATUS"


def quotes_url(tickers) -> str:
    return (f"{ISS}/engines/stock/markets/shares/boards/{BOARD}/securities.json?" + _query({
        "iss.meta": "off", "iss.only": "securities,marketdata",
        "securities": ",".join(tickers),
        "securities.columns": QUOTE_SECURITY_COLUMNS,
        "marketdata.columns": QUOTE_MARKET_COLUMNS}))


def parse_quotes(payload: dict) -> dict[str, dict]:
    """Котировки доски TQBR по тикерам.

    Идут торги (есть последняя сделка `LAST`) — цена последней сделки, дата —
    дата среза `SYSTIME`, время — время сделки `TIME` (Москва). Вне торгов
    (новый торговый день без сделок) — последняя цена прошлого дня `PREVPRICE`
    (нет её — официальное закрытие `PREVLEGALCLOSEPRICE`) с датой `PREVDATE`.
    """
    securities = {row["SECID"]: row for row in _rows(payload, "securities")}
    market = {row["SECID"]: row for row in _rows(payload, "marketdata")}
    out = {}
    for secid, sec in securities.items():
        md = market.get(secid) or {}
        last, day = _positive(md.get("LAST")), _day(md.get("SYSTIME"))
        if last is not None and day is not None:
            quote = {"price": last, "date": day,
                     "time": md.get("TIME") or md.get("UPDATETIME"), "kind": "last"}
        else:
            prev = _positive(sec.get("PREVPRICE")) or _positive(sec.get("PREVLEGALCLOSEPRICE"))
            prev_day = _day(sec.get("PREVDATE"))
            if prev is not None and prev_day is not None:
                quote = {"price": prev, "date": prev_day, "time": None, "kind": "prev"}
            else:
                quote = {"price": None, "date": None, "time": None, "kind": "none"}
        quote.update(source=f"ISS {BOARD}", shortname=sec.get("SHORTNAME"), isin=sec.get("ISIN"),
                     trading_status=md.get("TRADINGSTATUS"))
        out[secid] = quote
    return out


def fetch_quotes(tickers=(X5, *PEERS), *, getter: Getter = fetch) -> dict[str, dict]:
    response = getter(quotes_url(tickers), source=SOURCE, name="quotes_tqbr.json")
    return parse_quotes(response.json())


# ---------------------------------------------------------- история закрытий

HISTORY_COLUMNS = "TRADEDATE,LEGALCLOSEPRICE,CLOSE,VOLUME"


def history_url(secid: str, start: date, till: date, offset: int = 0) -> str:
    return (f"{ISS}/history/engines/stock/markets/shares/boards/{BOARD}/securities/"
            f"{secid}.json?" + _query({
                "iss.meta": "off", "from": start.isoformat(), "till": till.isoformat(),
                "start": offset, "history.columns": HISTORY_COLUMNS}))


def parse_history_page(payload: dict) -> tuple[list[dict], tuple[int, int, int] | None]:
    """Страница истории → ([{date, close}], (INDEX, TOTAL, PAGESIZE) | None).

    Закрытие — официальная цена закрытия `LEGALCLOSEPRICE` (аукцион основной
    сессии); нет её — последняя сделка дня `CLOSE`. День без обеих — пропуск.
    """
    rows = []
    for row in _rows(payload, "history"):
        day = _day(row.get("TRADEDATE"))
        close = _positive(row.get("LEGALCLOSEPRICE")) or _positive(row.get("CLOSE"))
        if day is not None and close is not None:
            rows.append({"date": day, "close": close})
    cursor = None
    if "history.cursor" in payload:
        found = _rows(payload, "history.cursor")
        if found:
            cursor = (int(found[0]["INDEX"]), int(found[0]["TOTAL"]), int(found[0]["PAGESIZE"]))
    return rows, cursor


def fetch_price_history(secid: str, *, start: date, till: date,
                        getter: Getter = fetch) -> list[dict]:
    """Дневные закрытия за период, постранично по `history.cursor`."""
    by_day: dict[str, float] = {}
    offset = 0
    for page in range(MAX_HISTORY_PAGES):
        response = getter(history_url(secid, start, till, offset), source=SOURCE,
                          name=f"history_{secid}_{offset}.json")
        rows, cursor = parse_history_page(response.json())
        for row in rows:
            by_day[row["date"]] = row["close"]
        if cursor is None:
            break
        index, total, size = cursor
        if size <= 0 or index + size >= total:
            break
        offset = index + size
    else:
        raise IssError(f"история {secid}: больше {MAX_HISTORY_PAGES} страниц — обход прерван")
    return [{"date": day, "close": by_day[day]} for day in sorted(by_day)]


# ------------------------------------------------------- бескупонная кривая

ZCYC_URL = f"{ISS}/engines/stock/zcyc.json?iss.meta=off&iss.only=params,yearyields"


def parse_zcyc(payload: dict) -> dict:
    """Узлы 1/3/5/10 лет бескупонной кривой на последнюю дату ответа.

    ISS отдаёт проценты годовых; на выходе доли. Узел вне коридора
    `RATE_MIN`–`RATE_MAX` — чужие единицы или мусор: отказ.
    """
    rows = _rows(payload, "yearyields")
    days = [d for d in (_day(r.get("tradedate")) for r in rows) if d]
    if not days:
        raise IssError("zcyc: в yearyields нет ни одной даты")
    as_of = max(days)
    nodes, time_ = {}, None
    for row in rows:
        if _day(row.get("tradedate")) != as_of:
            continue
        try:
            period = float(row["period"])
            value = _share(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if value is None:
            continue
        key = f"{period:g}"
        if key in CURVE_NODES:
            if not RATE_MIN <= value <= RATE_MAX:
                raise IssError(f"zcyc: узел {key} лет = {row['value']} — вне "
                               f"{RATE_MIN:.0%}–{RATE_MAX:.0%} годовых (единицы?)")
            nodes[key] = value
            time_ = row.get("tradetime") or time_
    missing = [k for k in CURVE_NODES if k not in nodes]
    if missing:
        raise IssError(f"zcyc {as_of}: нет узлов {', '.join(missing)} лет")
    return {"as_of": as_of, "time": time_, "nodes": {k: nodes[k] for k in CURVE_NODES},
            "source": "ISS zcyc"}


def fetch_zcyc(*, getter: Getter = fetch) -> dict:
    return parse_zcyc(getter(ZCYC_URL, source=SOURCE, name="zcyc.json").json())


# ------------------------------------------------------- облигации X5

BOND_SECURITY_COLUMNS = ("SECID,SHORTNAME,SECNAME,ISIN,MATDATE,OFFERDATE,PUTOPTIONDATE,"
                         "COUPONPERCENT,FACEVALUE,ISSUESIZE,ISSUESIZEPLACED,BONDTYPE,"
                         "COUPON_DETAILS,PREVPRICE,PREVDATE,YIELDATPREVWAPRICE")
BOND_MARKET_COLUMNS = "SECID,LAST,YIELD,DURATION,VALTODAY,SYSTIME,TIME"


def emitter_url(emitter: int = X5_BOND_EMITTER) -> str:
    return f"{ISS}/emitters/{emitter}/securities.json?iss.meta=off"


def parse_emitter_bonds(payload: dict, board: str = BOND_BOARD) -> list[str]:
    """Торгуемые бумаги эмитента с основной доской `board`."""
    out = []
    for row in _rows(payload, "securities"):
        if str(row.get("IS_TRADED")) == "1" and row.get("PRIMARY_BOARDID") == board:
            out.append(str(row["SECID"]))
    return sorted(set(out))


def bonds_url(secids) -> str:
    return (f"{ISS}/engines/stock/markets/bonds/boards/{BOND_BOARD}/securities.json?" + _query({
        "iss.meta": "off", "iss.only": "securities,marketdata",
        "securities": ",".join(secids),
        "securities.columns": BOND_SECURITY_COLUMNS,
        "marketdata.columns": BOND_MARKET_COLUMNS}))


def _share(percent) -> float | None:
    """Проценты ISS → доли; округление до 1e-10 снимает хвосты двоичной
    арифметики (19,95 / 100 = 0,19949999…)."""
    try:
        return round(float(percent) / 100.0, 10) if percent is not None else None
    except (TypeError, ValueError):
        return None


def parse_bonds(payload: dict) -> list[dict]:
    """Облигации доски TQCB: цена (% номинала), доходность, дюрация, объём.

    Была сделка сегодня — `LAST` и `YIELD` на дату среза; не было — последняя
    цена прошлого дня `PREVPRICE` и доходность к средневзвешенной прошлого дня
    `YIELDATPREVWAPRICE` с датой `PREVDATE`. Объём в обращении — размещённые
    бумаги × текущий номинал; оборот — за день цены (нет сделок — null).
    """
    market = {row["SECID"]: row for row in _rows(payload, "marketdata")}
    out = []
    for sec in _rows(payload, "securities"):
        md = market.get(sec["SECID"]) or {}
        last, day = _positive(md.get("LAST")), _day(md.get("SYSTIME"))
        if last is not None and day is not None:
            price, price_date, ytm = last, day, _share(md.get("YIELD"))
            turnover = _positive(md.get("VALTODAY"))
        else:
            price, price_date = _positive(sec.get("PREVPRICE")), _day(sec.get("PREVDATE"))
            ytm, turnover = _share(sec.get("YIELDATPREVWAPRICE")), None
        pieces = _positive(sec.get("ISSUESIZEPLACED")) or _positive(sec.get("ISSUESIZE"))
        face = _positive(sec.get("FACEVALUE"))
        kind = f"{sec.get('BONDTYPE') or ''} {sec.get('COUPON_DETAILS') or ''}".lower()
        duration = _positive(md.get("DURATION"))
        out.append({
            "isin": sec.get("ISIN") or sec["SECID"], "secid": sec["SECID"],
            "name": sec.get("SECNAME"), "shortname": sec.get("SHORTNAME"),
            "coupon_type": "floating" if ("плав" in kind or "ключев" in kind) else "fixed",
            "coupon": _share(sec.get("COUPONPERCENT")),
            "put_date": _day(sec.get("OFFERDATE")) or _day(sec.get("PUTOPTIONDATE")),
            "maturity": _day(sec.get("MATDATE")),
            "price": price, "price_date": price_date,
            "ytm": ytm if ytm else None,
            "duration_years": duration / 365.0 if duration else None,
            "outstanding": pieces * face / 1e9 if pieces and face else None,
            "turnover": turnover / 1e9 if turnover else None,
            "source": f"ISS {BOND_BOARD}"})
    out.sort(key=lambda b: (b["put_date"] or b["maturity"] or "9999", b["isin"]))
    return out


def fetch_x5_bonds(*, getter: Getter = fetch) -> list[dict]:
    secids = parse_emitter_bonds(
        getter(emitter_url(), source=SOURCE, name="emitter_securities.json").json())
    if not secids:
        raise IssError(f"у эмитента {X5_BOND_EMITTER} нет торгуемых бумаг на {BOND_BOARD}")
    return parse_bonds(getter(bonds_url(secids), source=SOURCE, name="bonds_tqcb.json").json())
