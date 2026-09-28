"""Банк России: ключевая ставка.

Основной путь — SOAP-сервис DailyInfo, метод `KeyRate` (XML, а не вёрстка:
редизайн сайта парсер не ломает). Запасной — страница
https://www.cbr.ru/hd_base/KeyRate/ с тем же периодом. Оба ответа ложатся в
сырой архив до разбора. Ставка — доля единицы; дата — день, на который
установлена ставка.

В оценку ключевая не входит (docs/MODEL.md §13.4): это наблюдение для плитки
и флага `book_update`.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Callable

from indicators.http import FetchError, fetch

SOAP_URL = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
PAGE_URL = "https://www.cbr.ru/hd_base/KeyRate/"
# cbr.ru закрыт DDoS-Guard: незнакомый User-Agent (в том числе сборщика) и
# заголовок, не совпадающий с клиентом («Mozilla/5.0» от Python), получают 403
# с JS-проверкой; стандартный заголовок urllib — ответ (проверено 28.09.2026:
# SOAP и страница — 200, свой заголовок — 403 на странице и временами на SOAP,
# «Mozilla/5.0» из Python — 403). Поэтому к ЦБ — родной заголовок библиотеки:
# честно называет клиента и не несёт сведений о владельце.
CBR_USER_AGENT = "Python-urllib/%d.%d" % sys.version_info[:2]
SOURCE = "cbr"
HISTORY_DAYS = 366
# Ставка вне 3–40 % годовых — проценты, прочитанные как доли, или мусор
# (с 13.09.2013 ключевая была 4,25–21 %).
RATE_MIN, RATE_MAX = 0.03, 0.40

Getter = Callable[..., object]


class CbrError(RuntimeError):
    """Ответ ЦБ пришёл, но ставки в нём нет или она не похожа на ставку."""


def soap_envelope(start: date, end: date) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
 <soap:Body><KeyRate xmlns="http://web.cbr.ru/">
  <fromDate>{start.isoformat()}T00:00:00</fromDate>
  <ToDate>{end.isoformat()}T00:00:00</ToDate>
 </KeyRate></soap:Body></soap:Envelope>""".encode("utf-8")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _checked(rows: list[tuple[str, float]], where: str) -> list[tuple[str, float]]:
    """Сортировка по дате, дубли дат — последнее значение; проверка ставки.

    Ставка вне коридора `RATE_MIN`–`RATE_MAX` — отказ (единицы или мусор).
    """
    if not rows:
        raise CbrError(f"{where}: в ответе нет ни одной ставки")
    by_day = {}
    for day, rate in rows:
        if not RATE_MIN <= rate <= RATE_MAX:
            raise CbrError(f"{where}: ставка {rate:.4f} на {day} вне "
                           f"{RATE_MIN:.0%}–{RATE_MAX:.0%} годовых (единицы?)")
        by_day[day] = rate
    return sorted(by_day.items())


def parse_soap(body: bytes) -> list[tuple[str, float]]:
    """Ответ `KeyRate` → [(дата ISO, ставка долей)] по возрастанию даты."""
    try:
        tree = ET.fromstring(body)
    except ET.ParseError as exc:
        raise CbrError(f"SOAP KeyRate: не XML ({exc})") from exc
    rows = []
    for node in tree.iter():
        if _local(node.tag) != "KR":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in node}
        try:
            day = datetime.fromisoformat(fields["DT"]).date().isoformat()
            rate = round(float(fields["Rate"].replace(",", ".")) / 100.0, 10)
        except (KeyError, ValueError) as exc:
            raise CbrError(f"SOAP KeyRate: строка без даты или ставки: {fields}") from exc
        rows.append((day, rate))
    return _checked(rows, "SOAP KeyRate")


_PAGE_ROW = re.compile(
    r"<td>\s*(\d{2})\.(\d{2})\.(\d{4})\s*</td>\s*<td>\s*(\d+(?:[.,]\d+)?)\s*</td>")


def parse_page(html: str) -> list[tuple[str, float]]:
    """Таблица страницы hd_base/KeyRate («Дата» | «Ставка») → как `parse_soap`."""
    rows = [(f"{y}-{m}-{d}", round(float(rate.replace(",", ".")) / 100.0, 10))
            for d, m, y, rate in _PAGE_ROW.findall(html)]
    return _checked(rows, "страница hd_base/KeyRate")


def page_url(start: date, end: date) -> str:
    return PAGE_URL + "?" + urllib.parse.urlencode({
        "UniDbQuery.Posted": "True",
        "UniDbQuery.From": start.strftime("%d.%m.%Y"),
        "UniDbQuery.To": end.strftime("%d.%m.%Y")})


def changes(rows: list[tuple[str, float]]) -> list[dict]:
    """Только дни смены ставки (первая строка — начало периода)."""
    out, last = [], None
    for day, rate in rows:
        if rate != last:
            out.append({"date": day, "value": rate})
            last = rate
    return out


def fetch_key_rate(today: date, *, getter: Getter = fetch,
                   days: int = HISTORY_DAYS) -> dict:
    """Последняя ключевая ставка и её смены за `days` дней.

    SOAP упал (сеть, HTTP, мусор) — запасной путь страницей; упали оба —
    `CbrError` с обеими причинами.
    """
    start = today - timedelta(days=days)
    try:
        response = getter(SOAP_URL, source=SOURCE, name="key_rate_soap.xml",
                          data=soap_envelope(start, today),
                          headers={"Content-Type": "text/xml; charset=utf-8",
                                   "SOAPAction": "http://web.cbr.ru/KeyRate",
                                   "User-Agent": CBR_USER_AGENT})
        rows, source, note = parse_soap(response.body), "ЦБ SOAP KeyRate", None
    except (FetchError, CbrError) as soap_error:
        try:
            response = getter(page_url(start, today), source=SOURCE, name="key_rate_page.html",
                              headers={"User-Agent": CBR_USER_AGENT})
            rows, source = parse_page(response.text), "ЦБ hd_base/KeyRate"
            note = f"SOAP не ответил годно ({soap_error}); взята страница"
        except (FetchError, CbrError) as page_error:
            raise CbrError(f"ключевая ставка: SOAP — {soap_error}; страница — {page_error}") \
                from page_error
    day, value = rows[-1]
    return {"value": value, "date": day, "source": source, "note": note,
            "history": changes(rows)}
