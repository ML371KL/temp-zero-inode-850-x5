"""Живые входы выпуска X5: `collect_live()` и `python -m indicators.live`.

Живыми в оценку идут только цена акции и дата оценки (docs/MODEL.md §13.4);
кривая ОФЗ, ключевая ставка, аналоги, облигации и история цены — наблюдение
для плиток, графиков и флагов. Формат (`schema: x5-live-v1`):

    {"schema", "fetched_at" (UTC ISO), "run_date" (дата прогона по Москве),
     "price": {"value", "date", "time", "source", "kind", "status" ("live" |
               "fallback"), "accepted", "reason", "collected", "reference",
               "last_accepted": {"value", "date"}},
     "curve": {"as_of", "time", "nodes": {"1","3","5","10"}, "source"} | null,
     "key_rate": {"value", "date", "source", "note", "history"} | null,
     "peers": {TICKER: {"price", "date", "time", "kind"}},
     "bonds": [...], "price_history": [{"date", "close"}],
     "errors": {источник: причина}}

**Годность цены** (§13.4; пороги перенесены из Магнита 850oa):
возраст ≤ 7 дней; отклонение от ПОСЛЕДНЕЙ ПРИНЯТОЙ ≤ 30 %; отличие в 10 раз и
больше — чужие единицы, не принимается никогда. Негодная цена заменяется
последней принятой из прошлого выпуска (`status: "fallback"` + причина). Нет
годной цены и нет запасной — `PriceUnavailable` (ненулевой код конвейера:
выпуск не собирается, на витрине прежний).

**Эталон едет в выпуске.** `price.last_accepted` — последняя принятая цена
ПОСЛЕ этого прогона; ядро переносит блок в выпуск (`live.price`), следующий
прогон читает его оттуда. Без эталона с датой проверка скачка у Магнита либо
выключала себя, либо блокировала навсегда после простоя.

**Скачок после простоя.** Если последняя принятая старше недели к дате новой
цены, скачок больше 30 % (но меньше ×10) принимается, когда его подтверждает
соседний торговый день истории ISS (не старше недели, позже эталона, в
пределах 5 %) — с пометкой в `reason`: снятая проверка обязана быть видна.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indicators import cbr, iss  # noqa: E402
from indicators.http import FetchError, fetch, read_json_source  # noqa: E402

SCHEMA = "x5-live-v1"
MSK = timezone(timedelta(hours=3))

MAX_PRICE_AGE_DAYS = 7
MAX_PRICE_JUMP = 0.30
PRICE_UNITS_FACTOR = 10.0
PRICE_REFERENCE_MAX_AGE_DAYS = 7
PRICE_CONFIRM_BAND = 0.05
HISTORY_DAYS = 365

# Сбой разбора второстепенного источника не должен ронять выпуск: причина
# уходит в `errors`, а выпуск выходит без этого наблюдения.
SOURCE_ERRORS = (FetchError, iss.IssError, cbr.CbrError, ValueError, KeyError,
                 TypeError, IndexError)


class PriceUnavailable(RuntimeError):
    """Годной цены X5 нет и заменить её нечем."""


def msk_today() -> date:
    return datetime.now(MSK).date()


def _day(stamp) -> date | None:
    try:
        return date.fromisoformat(str(stamp)[:10])
    except (TypeError, ValueError):
        return None


def last_accepted(previous: dict | None) -> dict | None:
    """Последняя принятая цена из прошлого выпуска: {value, date, origin}.

    Порядок: `live.price.last_accepted` (эталон, который несёт выпуск) →
    `market.price` с `market.price_date` (у принятой цены это она сама, у
    запасной — та же последняя принятая). Нет ни того, ни другого — None.
    """
    if not previous:
        return None
    price = ((previous.get("live") or {}).get("price") or {})
    carried = price.get("last_accepted") if isinstance(price, dict) else None
    if isinstance(carried, dict) and _positive(carried.get("value")) and _day(carried.get("date")):
        return {"value": float(carried["value"]), "date": str(carried["date"])[:10],
                "origin": "последняя принятая, из прошлого выпуска"}
    market = previous.get("market") or {}
    if _positive(market.get("price")) and _day(market.get("price_date")):
        return {"value": float(market["price"]), "date": str(market["price_date"])[:10],
                "origin": "цена прошлого выпуска"}
    return None


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _history_reference(history: list[dict], before: date) -> dict | None:
    """Последнее закрытие истории строго раньше даты цены — эталон, когда
    прошлого выпуска нет (первый прогон). Запасной ценой оно не служит."""
    earlier = [row for row in history if (_day(row["date"]) or before) < before]
    if not earlier:
        return None
    row = earlier[-1]
    return {"value": float(row["close"]), "date": row["date"],
            "origin": "последнее закрытие истории ISS (прошлого выпуска нет)"}


def _confirming_day(history: list[dict], price: float, price_date: date,
                    reference_date: date | None) -> dict | None:
    """Соседний торговый день, подтверждающий скачок после простоя."""
    earlier = [row for row in history if (_day(row["date"]) or price_date) < price_date]
    if not earlier:
        return None
    row = earlier[-1]
    day = _day(row["date"])
    if (price_date - day).days > PRICE_REFERENCE_MAX_AGE_DAYS:
        return None
    if reference_date is not None and day <= reference_date:
        return None
    if abs(float(row["close"]) / price - 1) > PRICE_CONFIRM_BAND:
        return None
    return row


def judge_price(collected: dict | None, *, reference: dict | None, history: list[dict],
                today: date, error: str | None = None) -> dict:
    """Решение о цене X5 по правилам годности (шапка модуля).

    `collected` — {value, date, time, kind, source} или None; `reference` —
    последняя принятая из прошлого выпуска (она же запасная) или None.
    """
    def fallback(reason: str) -> dict:
        if reference is None:
            raise PriceUnavailable(f"{reason}; запасной цены нет (прошлого выпуска нет "
                                   "или в нём нет принятой цены)")
        return {"value": reference["value"], "date": reference["date"], "time": None,
                "source": reference["origin"], "kind": "fallback", "status": "fallback",
                "accepted": False, "reason": reason, "collected": collected,
                "reference": reference,
                "last_accepted": {"value": reference["value"], "date": reference["date"]}}

    if not collected or _positive(collected.get("value")) is None:
        why = error or "в ответе ISS нет цены X5"
        return fallback(f"цена X5 не собрана: {why}")
    value = float(collected["value"])
    price_date = _day(collected.get("date"))
    if price_date is None:
        return fallback(f"у цены {value:g} ₽ нет даты торгов")
    age = (today - price_date).days
    if age < 0:
        return fallback(f"дата цены {price_date} позже даты прогона {today}")
    if age > MAX_PRICE_AGE_DAYS:
        return fallback(f"цена {value:g} ₽ от {price_date} устарела на {age} дн. "
                        f"при пределе {MAX_PRICE_AGE_DAYS}")

    check = reference or _history_reference(history, price_date)
    note = None
    if check is None:
        note = "эталона нет (ни прошлого выпуска, ни истории) — скачок не проверялся"
    else:
        ratio = value / check["value"]
        against = f"эталона {check['value']:g} ₽ от {check['date']} ({check['origin']})"
        if ratio >= PRICE_UNITS_FACTOR or ratio <= 1 / PRICE_UNITS_FACTOR:
            return fallback(f"цена {value:g} ₽ отличается от {against} в "
                            f"{max(ratio, 1 / ratio):.0f} раз — чужие единицы, "
                            "нужна правка сборщика")
        if abs(ratio - 1) > MAX_PRICE_JUMP:
            ref_date = _day(check["date"])
            gap = (price_date - ref_date).days if ref_date else None
            if gap is None or gap <= PRICE_REFERENCE_MAX_AGE_DAYS:
                return fallback(f"цена {value:g} ₽ отличается от {against} на "
                                f"{abs(ratio - 1):.0%} при пределе {MAX_PRICE_JUMP:.0%}")
            confirm = _confirming_day(history, value, price_date, ref_date)
            if confirm is None:
                return fallback(f"цена {value:g} ₽ отличается от {against} на "
                                f"{abs(ratio - 1):.0%}; эталон старше "
                                f"{PRICE_REFERENCE_MAX_AGE_DAYS} дн. ({gap} дн.), скачок "
                                "принимается при подтверждении соседним торговым днём "
                                f"в пределах {PRICE_CONFIRM_BAND:.0%} — ждём подтверждения")
            note = (f"проверка скачка снята: эталон {check['value']:g} ₽ от {check['date']} "
                    f"старше {PRICE_REFERENCE_MAX_AGE_DAYS} дн.; скачок {ratio - 1:+.0%} "
                    f"подтверждён закрытием {confirm['date']} ({confirm['close']:g} ₽)")
    return {"value": value, "date": price_date.isoformat(), "time": collected.get("time"),
            "source": collected.get("source"), "kind": collected.get("kind"),
            "status": "live", "accepted": True, "reason": note, "collected": collected,
            "reference": check,
            "last_accepted": {"value": value, "date": price_date.isoformat()}}


def _attempt(errors: dict, name: str, action: Callable[[], object]):
    try:
        return action()
    except SOURCE_ERRORS as exc:
        errors[name] = f"{type(exc).__name__}: {exc}"[:500]
        return None


def collect_live(previous: dict | None = None, *, today: date | None = None,
                 getter: Callable[..., object] = fetch) -> dict:
    """Собирает живые входы. `previous` — прошлый выпуск (для эталона цены).

    Бросает `PriceUnavailable`, если годной цены нет и заменить её нечем;
    отказ прочих источников — строка в `errors`, блок — null/пусто.
    """
    today = today or msk_today()
    errors: dict[str, str] = {}
    quotes = _attempt(errors, "quotes",
                      lambda: iss.fetch_quotes((iss.X5, *iss.PEERS), getter=getter)) or {}
    history = _attempt(errors, "price_history", lambda: iss.fetch_price_history(
        iss.X5, start=today - timedelta(days=HISTORY_DAYS), till=today, getter=getter)) or []

    collected = None
    quote = quotes.get(iss.X5)
    if quote and quote.get("price") is not None:
        collected = {"value": quote["price"], "date": quote["date"], "time": quote["time"],
                     "kind": quote["kind"], "source": quote["source"]}
    elif history:
        # Котировки не пришли, история пришла: последнее закрытие дня — тоже
        # цена ISS, с датой торгов. Правила годности те же.
        row = history[-1]
        collected = {"value": row["close"], "date": row["date"], "time": None,
                     "kind": "history_close", "source": f"ISS {iss.BOARD} history"}
        if quotes and iss.X5 not in quotes:
            errors.setdefault("quotes", "в ответе ISS нет строки X5")
    price = judge_price(collected, reference=last_accepted(previous), history=history,
                        today=today, error=errors.get("quotes"))

    peers = {}
    for ticker in iss.PEERS:
        q = quotes.get(ticker)
        if q and q.get("price") is not None:
            peers[ticker] = {"price": q["price"], "date": q["date"], "time": q["time"],
                             "kind": q["kind"]}
        elif quotes:
            errors.setdefault(f"peer_{ticker}", "нет цены в ответе ISS")

    curve = _attempt(errors, "curve", lambda: iss.fetch_zcyc(getter=getter))
    key_rate = _attempt(errors, "key_rate", lambda: cbr.fetch_key_rate(today, getter=getter))
    bonds = _attempt(errors, "bonds", lambda: iss.fetch_x5_bonds(getter=getter)) or []
    return {"schema": SCHEMA,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_date": today.isoformat(),
            "price": price, "curve": curve, "key_rate": key_rate, "peers": peers,
            "bonds": bonds, "price_history": history, "errors": errors}


def describe(live: dict) -> list[str]:
    """Строки журнала прогона: что взято и что отказало."""
    price = live["price"]
    when = f"{price['date']}" + (f" {price['time']}" if price.get("time") else "")
    if price["accepted"]:
        lines = [f"цена X5: {price['value']:g} ₽ на {when} ({price['source']}, {price['kind']}) — принята"]
        if price.get("reason"):
            lines.append(f"  пометка: {price['reason']}")
    else:
        lines = [f"цена X5: ЗАПАСНАЯ {price['value']:g} ₽ от {price['date']} — {price['reason']}"]
    curve = live.get("curve")
    if curve:
        nodes = ", ".join(f"{k} г. {v:.2%}" for k, v in curve["nodes"].items())
        lines.append(f"кривая ОФЗ на {curve['as_of']}: {nodes}")
    key = live.get("key_rate")
    if key:
        lines.append(f"ключевая ставка: {key['value']:.2%} на {key['date']} ({key['source']})")
    lines.append(f"аналоги: {', '.join(sorted(live.get('peers') or {})) or 'нет'}; "
                 f"облигаций X5: {len(live.get('bonds') or [])}; "
                 f"дней истории: {len(live.get('price_history') or [])}")
    for name, reason in sorted((live.get("errors") or {}).items()):
        lines.append(f"ОТКАЗ ИСТОЧНИКА {name}: {reason}")
    return lines


def write_live(live: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(live, ensure_ascii=False, indent=1, allow_nan=False) + "\n",
                   encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сбор живых входов X5")
    parser.add_argument("--previous", help="прошлый выпуск: путь или URL (эталон цены)")
    parser.add_argument("--out", default=str(ROOT / "var" / "live" / "live.json"))
    parser.add_argument("--today", help="дата прогона ГГГГ-ММ-ДД (по умолчанию — сегодня, Москва)")
    args = parser.parse_args(argv)
    try:
        previous = read_json_source(args.previous) if args.previous else None
    except (OSError, ValueError, FetchError) as exc:
        print(f"ПРОВАЛ на шаге чтение прошлого выпуска: {exc}", file=sys.stderr, flush=True)
        return 1
    try:
        live = collect_live(previous, today=date.fromisoformat(args.today) if args.today else None)
    except PriceUnavailable as exc:
        print(f"ПРОВАЛ на шаге сбор живых входов: {exc}", file=sys.stderr, flush=True)
        return 1
    for line in describe(live):
        print(line)
    write_live(live, Path(args.out))
    print(f"готово: живые входы записаны в {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
