"""Tencent (gtimg.cn) - the real-time backbone.

* qt.gtimg.cn/q=...                       batch real-time quotes (~300 symbols per call, GBK text)
* web.ifzq.gtimg.cn/.../minute/query      today's 1-minute series (cumulative volume/amount)
* web.ifzq.gtimg.cn/.../day/query         last 5 sessions of 1-minute series
* web.ifzq.gtimg.cn/.../newfqkline/get    daily bars incl. turnover % and amount, 2000 per call

Units returned by Tencent are mixed (A-share volume in 100-share lots, amount
in 10k CNY, market cap in 100M); everything is converted to shares and
currency units here.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from .. import http, sample
from ..calendar import BJ, session_of
from ..symbols import is_index, market, volume_unit

QUOTE_URL = "https://qt.gtimg.cn/q="
MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
FIVEDAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get"
KLINE_MAX = 2000
QUOTE_BATCH = 250

QUOTE_COLUMNS = [
    "code", "name", "market", "type", "price", "prev_close", "open", "high", "low",
    "change", "pct_change", "volume", "amount", "turnover_rate", "volume_ratio",
    "amplitude", "vwap", "pe_ttm", "pb", "div_yield", "float_mcap", "total_mcap",
    "float_shares", "total_shares", "high_52w", "low_52w", "limit_up", "limit_down",
    "buy_volume", "sell_volume", "bid1", "ask1", "bid_depth", "ask_depth",
    "currency", "time",
]


def _f(fields: list[str], i: int, scale: float = 1.0) -> float | None:
    try:
        v = fields[i]
    except IndexError:
        return None
    if v in ("", "-", "--"):
        return None
    try:
        return float(v) * scale
    except ValueError:
        return None


def _parse_time(s: str) -> datetime | None:
    for fmt in ("%Y%m%d%H%M%S", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=BJ)
        except ValueError:
            continue
    return None


def _nz(v: float | None) -> float | None:
    """Tencent writes 0 for 'not applicable' valuation fields (index P/E, ETF P/B)."""
    return v if v else None


def parse_quote(key: str, fields: list[str]) -> dict | None:
    """Parse one `v_<code>="..."` record into a flat dict with plain units."""
    row = _parse_quote_raw(key, fields)
    if row:
        for k in ("pe_ttm", "pb", "float_mcap", "total_mcap", "volume_ratio", "vwap"):
            row[k] = _nz(row[k])
    return row


def _parse_quote_raw(key: str, fields: list[str]) -> dict | None:
    if len(fields) < 45:
        return None
    code = key
    if code.startswith("r_"):
        code = code[2:]
    if market(code) == "HK" and is_index(code):
        # HK index records carry turnover (in 10k HKD) where stocks carry volume.
        return {
            "code": code, "name": fields[1].strip(), "market": "HK", "type": "ZS",
            "price": _f(fields, 3), "prev_close": _f(fields, 4), "open": _f(fields, 5),
            "high": _f(fields, 33), "low": _f(fields, 34), "change": _f(fields, 31), "pct_change": _f(fields, 32),
            "volume": None, "amount": _f(fields, 37, 1e4), "turnover_rate": None, "volume_ratio": _f(fields, 50),
            "amplitude": _f(fields, 43), "vwap": None, "pe_ttm": None, "pb": None, "div_yield": None,
            "float_mcap": None, "total_mcap": None, "float_shares": None, "total_shares": None,
            "high_52w": _f(fields, 48), "low_52w": _f(fields, 49), "limit_up": None, "limit_down": None,
            "buy_volume": None, "sell_volume": None, "bid1": None, "ask1": None, "bid_depth": None, "ask_depth": None,
            "currency": "HKD", "time": _parse_time(fields[30]),
        }
    if market(code) == "HK":
        return {
            "code": code, "name": fields[1].strip(), "market": "HK", "type": fields[63] if len(fields) > 63 else "",
            "price": _f(fields, 3), "prev_close": _f(fields, 4), "open": _f(fields, 5),
            "high": _f(fields, 33), "low": _f(fields, 34), "change": _f(fields, 31), "pct_change": _f(fields, 32),
            "volume": _f(fields, 6), "amount": _f(fields, 37), "turnover_rate": _f(fields, 59),
            "volume_ratio": _f(fields, 50), "amplitude": _f(fields, 43), "vwap": _f(fields, 73),
            "pe_ttm": _f(fields, 39), "pb": _f(fields, 58), "div_yield": _f(fields, 47),
            "float_mcap": _f(fields, 44, 1e8), "total_mcap": _f(fields, 45, 1e8),
            "float_shares": _f(fields, 69), "total_shares": _f(fields, 70),
            "high_52w": _f(fields, 48), "low_52w": _f(fields, 49), "limit_up": None, "limit_down": None,
            "buy_volume": None, "sell_volume": None, "bid1": _f(fields, 9), "ask1": _f(fields, 19),
            "bid_depth": None, "ask_depth": None,
            "currency": fields[75] if len(fields) > 75 and fields[75] else "HKD",
            "time": _parse_time(fields[30]),
        }
    lot = volume_unit(code)  # 100-share lots, except STAR Market (shares)
    bid_depth = sum(_f(fields, i, lot) or 0 for i in (10, 12, 14, 16, 18))
    ask_depth = sum(_f(fields, i, lot) or 0 for i in (20, 22, 24, 26, 28))
    limit_up, limit_down = _f(fields, 47), _f(fields, 48)
    return {
        "code": code, "name": fields[1].replace(" ", ""), "market": "CN",
        "type": fields[61] if len(fields) > 61 else "",
        "price": _f(fields, 3), "prev_close": _f(fields, 4), "open": _f(fields, 5),
        "high": _f(fields, 33), "low": _f(fields, 34), "change": _f(fields, 31), "pct_change": _f(fields, 32),
        "volume": _f(fields, 6, lot), "amount": _f(fields, 37, 1e4), "turnover_rate": _f(fields, 38),
        "volume_ratio": _f(fields, 49), "amplitude": _f(fields, 43), "vwap": _f(fields, 51),
        "pe_ttm": _f(fields, 39), "pb": _f(fields, 46), "div_yield": _f(fields, 64),
        "float_mcap": _f(fields, 44, 1e8), "total_mcap": _f(fields, 45, 1e8),
        "float_shares": _f(fields, 72), "total_shares": _f(fields, 73),
        "high_52w": _f(fields, 67), "low_52w": _f(fields, 68),
        "limit_up": limit_up if limit_up and limit_up > 0 else None,
        "limit_down": limit_down if limit_down and limit_down > 0 else None,
        "buy_volume": _f(fields, 7, lot), "sell_volume": _f(fields, 8, lot),
        "bid1": _f(fields, 9), "ask1": _f(fields, 19), "bid_depth": bid_depth, "ask_depth": ask_depth,
        "currency": fields[82] if len(fields) > 82 and fields[82] else "CNY",
        "time": _parse_time(fields[30]),
    }


def parse_quote_text(text: str) -> list[dict]:
    rows = []
    for rec in text.split(";"):
        rec = rec.strip()
        if not rec.startswith("v_") or '"' not in rec:
            continue
        key = rec[2:rec.index("=")]
        body = rec.split('"', 2)[1]
        if "~" not in body:  # v_pv_none_match="1" -> unknown symbol
            continue
        row = parse_quote(key, body.split("~"))
        if row:
            rows.append(row)
    return rows


def _quote_batch(codes: list[str]) -> list[dict]:
    r = http.get(QUOTE_URL + ",".join(codes), timeout=15)
    return parse_quote_text(r.content.decode("gbk", errors="replace"))


@sample.replay("tencent.quotes")
def quotes(codes: list[str], workers: int = 6) -> pd.DataFrame:
    """Real-time quotes for any mix of A-share, HK, ETF and index symbols."""
    codes = list(dict.fromkeys(codes))
    batches = [codes[i:i + QUOTE_BATCH] for i in range(0, len(codes), QUOTE_BATCH)]
    rows: list[dict] = []
    errors = []
    for res in http.pmap(_quote_batch, batches, workers=workers):
        if isinstance(res, Exception):
            errors.append(res)
        else:
            rows.extend(res)
    if errors and not rows:
        raise errors[0]
    df = pd.DataFrame(rows, columns=QUOTE_COLUMNS)
    return df


# --------------------------------------------------------------------------- minute data

def _minute_rows(lines: list[str], day: date, code: str) -> pd.DataFrame:
    """Rows look like 'HHMM price cum_volume cum_amount'. Convert to per-minute bars."""
    lot = volume_unit(code)
    recs = []
    for ln in lines:
        parts = ln.split()
        if len(parts) < 4:
            continue
        hhmm, price, cv, ca = parts[:4]
        recs.append((hhmm, float(price), float(cv) * lot, float(ca)))
    df = pd.DataFrame(recs, columns=["hhmm", "price", "cum_volume", "cum_amount"])
    if df.empty:
        return df.assign(datetime=pd.Series(dtype="datetime64[ns]"))
    df["datetime"] = pd.to_datetime(day.isoformat() + " " + df["hhmm"].str[:2] + ":" + df["hhmm"].str[2:])
    df["volume"] = df["cum_volume"].diff().fillna(df["cum_volume"]).clip(lower=0)
    df["amount"] = df["cum_amount"].diff().fillna(df["cum_amount"]).clip(lower=0)
    mkt = market(code)
    df["session"] = [session_of(mkt, t) for t in df["datetime"].dt.time]
    return df[["datetime", "price", "volume", "amount", "cum_volume", "cum_amount", "session"]]


@sample.replay("tencent.minute_today")
def minute_today(code: str) -> tuple[date, float | None, pd.DataFrame]:
    """Latest session's 1-minute series: (session date, previous close, bars)."""
    j = http.get(MINUTE_URL, params={"code": code}).json()
    node = j["data"][code]
    day = datetime.strptime(node["data"]["date"], "%Y%m%d").date()
    qt = node.get("qt", {}).get(code)
    prev_close = float(qt[4]) if qt and len(qt) > 4 and qt[4] else None
    return day, prev_close, _minute_rows(node["data"]["data"], day, code)


@sample.replay("tencent.minute_5day")
def minute_5day(code: str) -> pd.DataFrame:
    """Last five sessions of 1-minute bars (oldest first) with each day's previous close."""
    j = http.get(FIVEDAY_URL, params={"code": code}).json()
    frames = []
    for d in j["data"][code]["data"]:
        day = datetime.strptime(d["date"], "%Y%m%d").date()
        df = _minute_rows(d["data"], day, code)
        df["prev_close"] = float(d["prec"]) if d.get("prec") else None
        df["date"] = day
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).sort_values("datetime").reset_index(drop=True)


MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
MKLINE_PERIODS = {"1 min": "m1", "5 min": "m5", "15 min": "m15", "30 min": "m30", "60 min": "m60"}


@sample.replay("tencent.minute_ohlc")
def minute_ohlc(code: str, period: str = "m1", count: int = 800) -> pd.DataFrame:
    """Minute OHLC bars (A-shares only; 800 max = ~3 sessions of 1-min, ~16 of 5-min)."""
    j = http.get(MKLINE_URL, params={"param": f"{code},{period},,{count}"}).json()
    data = j.get("data")
    if not isinstance(data, dict) or code not in data:
        return pd.DataFrame()
    rows = data[code].get(period) or []
    lot = volume_unit(code)
    df = pd.DataFrame([{
        "datetime": pd.to_datetime(r[0], format="%Y%m%d%H%M"), "open": float(r[1]), "high": float(r[3]),
        "low": float(r[4]), "close": float(r[2]), "volume": float(r[5]) * lot,
    } for r in rows])
    return df


# --------------------------------------------------------------------------- daily bars

DAILY_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "turnover_rate", "event"]


def _kline_page(code: str, start: str, end: str, fq: str) -> list[list]:
    param = f"{code},day,{start},{end},{KLINE_MAX},{fq}"
    j = http.get(KLINE_URL, params={"param": param}, timeout=20).json()
    data = j.get("data")
    if not isinstance(data, dict) or code not in data:
        return []
    node = data[code]
    return node.get(f"{fq}day") or node.get("day") or []


@sample.replay("tencent.daily")
def daily(code: str, start: date | None = None, end: date | None = None, fq: str = "") -> pd.DataFrame:
    """Daily OHLCV with amount and turnover rate. fq='' raw, 'qfq' forward, 'hfq' backward
    (Tencent's adjustments are additive - see stockrt.adjust for the ratio method)."""
    lot = volume_unit(code)
    s = start.isoformat() if start else ""
    e = (end or date.today() + timedelta(days=1)).isoformat()
    rows: list[list] = []
    for _ in range(20):  # 20 x 2000 bars = 80 years
        page = _kline_page(code, s, e, fq)
        if not page:
            break
        rows = page + rows
        if len(page) < KLINE_MAX:
            break
        first = datetime.strptime(page[0][0], "%Y-%m-%d").date()
        e = (first - timedelta(days=1)).isoformat()
        if start and first <= start:
            break
    recs = []
    for r in rows:
        ev = r[6] if len(r) > 6 and isinstance(r[6], dict) else {}
        event = ev.get("FHcontent") or ""
        recs.append({
            "date": r[0], "open": float(r[1]), "close": float(r[2]), "high": float(r[3]), "low": float(r[4]),
            "volume": float(r[5]) * lot,
            "turnover_rate": float(r[7]) if len(r) > 7 and r[7] not in ("", None) else None,
            "amount": float(r[8]) * 1e4 if len(r) > 8 and r[8] not in ("", None) else None,
            "event": event,
        })
    df = pd.DataFrame(recs, columns=DAILY_COLUMNS)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]
    return df.reset_index(drop=True)
