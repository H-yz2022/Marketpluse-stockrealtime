"""Official trading calendars: is the market open on a given day?

* Mainland (SSE / SZSE / BSE share one calendar): Shenzhen Stock Exchange's own
  calendar API, szse.cn/api/report/exchange/onepersistenthour/monthList?month=YYYY-MM
  (jybz = 1 trading day, 0 closed).
* Hong Kong: HKEX closes on Hong Kong public holidays, published by the HK
  government as an iCalendar feed (1823.gov.hk). HKEX trades half a day
  (morning only) on Christmas Eve, New Year's Eve and Lunar New Year's Eve.

A copy for the current year ships in trading_calendar.json, so the app knows the
calendar offline and in sample mode; the live sources refresh it.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from . import http, sample, storage

log = logging.getLogger(__name__)

BUNDLED = Path(__file__).resolve().parent / "trading_calendar.json"
SZSE_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
HK_ICS_URL = "https://www.1823.gov.hk/common/ical/en.ics"
REFRESH = 24 * 3600

_lock = threading.Lock()
_mem: dict[str, tuple[float, object]] = {}


def _bundle() -> dict:
    if not hasattr(_bundle, "data"):
        try:
            _bundle.data = json.loads(BUNDLED.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _bundle.data = {"CN": {}, "HK_holidays": {}}
    return _bundle.data


def _remember(key: str, value, ttl: float = REFRESH):
    with _lock:
        _mem[key] = (time.time() + ttl, value)
    return value


def _recall(key: str):
    with _lock:
        hit = _mem.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


# --------------------------------------------------------------------------- fetchers

def fetch_cn_month(year: int, month: int) -> dict[str, int]:
    j = http.get(SZSE_URL, params={"month": f"{year}-{month:02d}"}, timeout=10, fast_fail=True).json()
    return {d["jyrq"]: int(d["jybz"]) for d in j.get("data") or []}


def fetch_hk_holidays() -> dict[str, str]:
    text = http.get(HK_ICS_URL, timeout=10, fast_fail=True).text
    out = {}
    for block in text.split("BEGIN:VEVENT")[1:]:
        d = re.search(r"DTSTART;VALUE=DATE:(\d{8})", block)
        s = re.search(r"SUMMARY:(.+)", block)
        if d and s:
            ds = d.group(1)
            out[f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"] = s.group(1).strip()
    return out


# --------------------------------------------------------------------------- lookups

def _cn_month(year: int, month: int) -> dict[str, int]:
    key = f"cn_{year}-{month:02d}"
    hit = _recall(key)
    if hit is not None:
        return hit
    bundled = {d: v for d, v in _bundle().get("CN", {}).items() if d.startswith(f"{year}-{month:02d}")}
    if sample.is_sample() and bundled:
        return _remember(key, bundled)
    cached = storage.load_json("calendar", key, max_age=REFRESH * 7)
    if cached:
        return _remember(key, cached)
    try:
        data = fetch_cn_month(year, month)
        if data:
            storage.save_json("calendar", key, data)
            return _remember(key, data)
    except Exception as e:  # noqa: BLE001 - fall back to the bundled copy
        log.info("SZSE calendar unavailable for %s: %s", key, e)
    return _remember(key, bundled, ttl=600)


def _hk_holidays() -> dict[str, str]:
    hit = _recall("hk")
    if hit is not None:
        return hit
    bundled = _bundle().get("HK_holidays", {})
    if sample.is_sample():
        return _remember("hk", bundled)
    cached = storage.load_json("calendar", "hk_holidays", max_age=REFRESH * 7)
    if cached:
        return _remember("hk", {**bundled, **cached})
    try:
        data = fetch_hk_holidays()
        if data:
            storage.save_json("calendar", "hk_holidays", data)
            return _remember("hk", {**bundled, **data})
    except Exception as e:  # noqa: BLE001
        log.info("HK holiday feed unavailable: %s", e)
    return _remember("hk", bundled, ttl=600)


def is_trading_day(mkt: str, d: date) -> bool | None:
    """True / False from the official calendar; None when the calendar doesn't cover d."""
    if d.weekday() >= 5:
        return False
    if mkt == "CN":
        flag = _cn_month(d.year, d.month).get(d.isoformat())
        return None if flag is None else bool(flag)
    hols = _hk_holidays()
    if not any(k.startswith(str(d.year)) for k in hols):
        return None
    return d.isoformat() not in hols


def holiday_name(mkt: str, d: date) -> str | None:
    if d.weekday() >= 5:
        return "weekend"
    if mkt == "HK":
        return _hk_holidays().get(d.isoformat())
    if is_trading_day("CN", d) is False:
        return {1: "New Year", 2: "Spring Festival", 4: "Qingming", 5: "Labour Day", 6: "Dragon Boat",
                9: "Mid-Autumn", 10: "National Day"}.get(d.month, "market holiday") + " holiday"
    return None


def half_day(mkt: str, d: date) -> bool:
    """HKEX trades the morning session only on Christmas Eve, New Year's Eve and Lunar New Year's Eve."""
    if mkt != "HK" or is_trading_day("HK", d) is not True:
        return False
    if (d.month, d.day) in ((12, 24), (12, 31)):
        return True
    lny = [k for k, v in _hk_holidays().items() if v.startswith("Lunar New Year") and k.startswith(str(d.year))]
    return bool(lny) and d == date.fromisoformat(min(lny)) - timedelta(days=1)


def next_trading_day(mkt: str, after: date, include: bool = False) -> date | None:
    d = after if include else after + timedelta(days=1)
    for _ in range(40):
        t = is_trading_day(mkt, d)
        if t is None:
            return None if d.weekday() < 5 else _skip_weekend(d)
        if t:
            return d
        d += timedelta(days=1)
    return None


def _skip_weekend(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def refresh_bundle(years: tuple[int, ...]) -> dict:
    """Rebuild trading_calendar.json from the official sources (run yearly, e.g. by build_sample)."""
    cn: dict[str, int] = {}
    for y in years:
        for m in range(1, 13):
            try:
                cn.update(fetch_cn_month(y, m))
            except Exception as e:  # noqa: BLE001 - future months may not be published yet
                log.info("SZSE %s-%02d: %s", y, m, e)
    hk = {k: v for k, v in fetch_hk_holidays().items() if int(k[:4]) in years}
    data = {"source": {"CN": SZSE_URL, "HK": HK_ICS_URL}, "CN": cn, "HK_holidays": hk}
    BUNDLED.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    if hasattr(_bundle, "data"):
        del _bundle.data
    return data
