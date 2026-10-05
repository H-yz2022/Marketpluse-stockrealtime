"""Ratio-based (total-return) price adjustment.

Chinese providers publish *additive* forward-adjusted prices: dividends are
subtracted as fixed amounts. Over long histories that distorts returns and can
even go negative (Kweichow Moutai's Tencent forward-adjusted price is below
zero before 2018). Volatility, drawdown and multi-year returns need the ratio
method instead: on each ex-date the day's return is measured against the
exchange's ex-rights reference price, and the series is rebuilt by
compounding those returns backward from today's real price.

Two inputs are supported:

* **segments** (start, a, b): the provider's adjusted price is a*raw + b from
  `start` until the next segment (Sina factor files, any affine scheme).
  On every day, true return r_t = (adj_t - b_t) / (adj_{t-1} - b_t) - 1,
  which equals raw_t / raw_{t-1} - 1 inside a segment and
  raw_t / reference_price - 1 on an ex-date.
* **events** (ex_date, cash_per_share, bonus_ratio): parsed from dividend text
  (Tencent fallback). Reference price = (prev_close - cash) / (1 + bonus).
"""

from __future__ import annotations

import bisect
import re
from datetime import date

import numpy as np
import pandas as pd

Segment = tuple[date, float, float]
Event = tuple[date, float, float]


def returns_from_segments(dates: pd.Series, close: pd.Series, segments: list[Segment]) -> np.ndarray:
    d = pd.to_datetime(dates).dt.date.to_numpy()
    c = close.to_numpy(dtype=float)
    n = len(c)
    r = np.full(n, np.nan)
    if n < 2:
        return r
    if not segments:
        r[1:] = c[1:] / c[:-1] - 1
        return r
    starts = [s[0] for s in segments]
    a = np.empty(n)
    b = np.empty(n)
    for i, day in enumerate(d):
        k = bisect.bisect_right(starts, day) - 1
        if k < 0:
            a[i], b[i] = segments[0][1], segments[0][2]
        else:
            a[i], b[i] = segments[k][1], segments[k][2]
    adj = a * c + b
    denom = adj[:-1] - b[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        rr = (adj[1:] - b[1:]) / denom - 1
    raw = c[1:] / c[:-1] - 1
    bad = ~np.isfinite(rr) | (denom <= 0)
    rr[bad] = raw[bad]
    r[1:] = rr
    return r


def returns_from_events(dates: pd.Series, close: pd.Series, events: list[Event]) -> np.ndarray:
    d = pd.to_datetime(dates).dt.date.to_numpy()
    c = close.to_numpy(dtype=float)
    n = len(c)
    r = np.full(n, np.nan)
    if n < 2:
        return r
    r[1:] = c[1:] / c[:-1] - 1
    for ex, cash, bonus in events:
        i = bisect.bisect_left(list(d), ex)
        if i <= 0 or i >= n:
            continue
        ref = (c[i - 1] - cash) / (1 + bonus)
        if ref > 0:
            r[i] = c[i] / ref - 1
    return r


def apply_returns(df: pd.DataFrame, ret: np.ndarray) -> pd.DataFrame:
    """Rebuild OHLC so the last close equals the real price and daily changes equal `ret`.

    Adds: close_raw, ret, factor (adjusted = raw * factor)."""
    out = df.copy()
    c = out["close"].to_numpy(dtype=float)
    n = len(c)
    adj = np.empty(n)
    if n:
        adj[-1] = c[-1]
        for i in range(n - 1, 0, -1):
            g = 1 + (ret[i] if np.isfinite(ret[i]) else 0.0)
            adj[i - 1] = adj[i] / g if g > 0 else adj[i]
    factor = np.divide(adj, c, out=np.ones(n), where=c > 0)
    out["close_raw"] = out["close"]
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * factor
    out["ret"] = ret
    out["factor"] = factor
    return out


# --------------------------------------------------------------------------- dividend text

_NUM = r"(\d+(?:\.\d+)?)"


def parse_cn_dividend(text: str) -> tuple[float, float] | None:
    """'10送3股转2股派5元' -> (cash 0.5, bonus 0.5) per share. None if nothing parseable."""
    if not text:
        return None
    m = re.match(_NUM, text)
    base = float(m.group(1)) if m else 10.0
    cash = sum(float(x) for x in re.findall(r"派" + _NUM + "元", text))
    bonus = sum(float(x) for x in re.findall(r"送" + _NUM + "股", text))
    bonus += sum(float(x) for x in re.findall(r"转(?:增)?" + _NUM + "股", text))
    if not cash and not bonus:
        return None
    return cash / base, bonus / base


USD_HKD = 7.8  # HKD is pegged to USD within 7.75-7.85


def parse_hk_dividend(text: str) -> float | None:
    """'中期息0.19港元' -> 0.19 HKD. USD amounts converted at the peg; RMB amounts are skipped."""
    if not text:
        return None
    hkd = sum(float(x) for x in re.findall(_NUM + r"\s*港元", text))
    usd = sum(float(x) for x in re.findall(_NUM + r"\s*美元", text))
    total = hkd + usd * USD_HKD
    return total or None


def events_from_text(df: pd.DataFrame, mkt: str) -> list[Event]:
    """Corporate actions from the 'event' text column of raw daily bars."""
    events: list[Event] = []
    for d, txt in zip(pd.to_datetime(df["date"]).dt.date, df["event"].fillna("")):
        if not txt:
            continue
        if mkt == "HK":
            cash = parse_hk_dividend(txt)
            if cash:
                events.append((d, cash, 0.0))
        else:
            parsed = parse_cn_dividend(txt)
            if parsed:
                events.append((d, parsed[0], parsed[1]))
    return events


def dividends_from_segments(segments: list[Segment]) -> list[tuple[date, float]]:
    """Cash paid per current share at each segment boundary (funds and HK; 0 for ratio-only factors)."""
    out = []
    for prev, cur in zip(segments, segments[1:]):
        if cur[1] == 0:
            continue
        cash = (cur[2] - prev[2]) / cur[1]
        if cash > 1e-9:
            out.append((cur[0], cash))
    return out
