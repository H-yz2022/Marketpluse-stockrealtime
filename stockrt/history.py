"""Daily history: cached raw bars + ratio-adjusted prices + optional live bar for today."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import adjust, storage
from .calendar import now_bj
from .calendar import status as session_status
from .sources import sina, tencent
from .symbols import is_index, market

log = logging.getLogger(__name__)

FACTOR_MAX_AGE = 12 * 3600


def _refresh_interval(code: str) -> float:
    """Seconds before cached bars are topped up: short while the market trades."""
    return 60.0 if session_status(market(code)).is_trading else 3600.0


def raw_daily(code: str, force: bool = False) -> pd.DataFrame:
    """Full raw daily history, cached on disk and topped up incrementally."""
    cached = storage.load_frame("daily", code)
    age = storage.age_seconds("daily", code)
    if cached is not None and not cached.empty and not force and age is not None and age < _refresh_interval(code):
        return cached
    if cached is None or cached.empty or force:
        df = tencent.daily(code)
    else:
        last = pd.Timestamp(cached["date"].max()).date()
        recent = tencent.daily(code, start=last - timedelta(days=20))
        df = pd.concat([cached[cached["date"] < pd.Timestamp(last - timedelta(days=20))], recent])
        df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if not df.empty:
        storage.save_frame("daily", code, df)
    return df


def factor_segments(code: str) -> tuple[list[adjust.Segment], str]:
    """(segments, source). Cached for 12h; falls back to stale cache if Sina is down."""
    if is_index(code):
        return [], "none (index)"
    cached = storage.load_json("factors", code, max_age=FACTOR_MAX_AGE)
    if cached is not None:
        return [(date.fromisoformat(d), a, b) for d, a, b in cached], "sina"
    try:
        segs = sina.factor_segments(code)
        storage.save_json("factors", code, [(d.isoformat(), a, b) for d, a, b in segs])
        return segs, "sina"
    except Exception as e:  # noqa: BLE001
        log.warning("factor fetch failed for %s: %s", code, e)
        stale = storage.load_json("factors", code)
        if stale is not None:
            return [(date.fromisoformat(d), a, b) for d, a, b in stale], "sina (stale)"
        return [], "unavailable"


def adjusted_daily(code: str, raw: pd.DataFrame | None = None) -> tuple[pd.DataFrame, str]:
    """Ratio-adjusted daily bars and a label describing the adjustment method used."""
    raw = raw_daily(code) if raw is None else raw
    if raw.empty:
        return raw, "no data"
    if is_index(code):
        ret = raw["close"].pct_change().to_numpy()
        return adjust.apply_returns(raw, ret), "price index (no adjustment)"
    segs, src = factor_segments(code)
    if segs:
        ret = adjust.returns_from_segments(raw["date"], raw["close"], segs)
        return adjust.apply_returns(raw, ret), f"ratio-adjusted ({src} factors)"
    events = adjust.events_from_text(raw, market(code))
    ret = adjust.returns_from_events(raw["date"], raw["close"], events)
    label = "ratio-adjusted (dividend text)" if events else "unadjusted (no corporate actions found)"
    return adjust.apply_returns(raw, ret), label


def with_live_bar(raw: pd.DataFrame, quote: pd.Series | dict | None) -> pd.DataFrame:
    """Append (or refresh) today's bar from a real-time quote, so intraday screens see today."""
    if quote is None or raw.empty:
        return raw
    q = dict(quote)
    t = q.get("time")
    if t is None or pd.isna(t) or not q.get("price"):
        return raw
    day = pd.Timestamp(pd.Timestamp(t).date())
    last = pd.Timestamp(raw["date"].iloc[-1])
    if day < last:
        return raw
    bar = {
        "date": day, "open": q.get("open") or q["price"], "high": q.get("high") or q["price"],
        "low": q.get("low") or q["price"], "close": q["price"], "volume": q.get("volume"),
        "amount": q.get("amount"), "turnover_rate": q.get("turnover_rate"), "event": "",
    }
    base = raw[raw["date"] < day]
    return pd.concat([base, pd.DataFrame([bar])], ignore_index=True)


def append_live(adj: pd.DataFrame, quote: dict | None) -> pd.DataFrame:
    """Add today's bar from a live quote to an *adjusted* series.

    The day's return is the exchange's own change % (correct even on an
    ex-dividend day, when the previous close is replaced by the reference
    price); earlier bars are rescaled so they stay consistent with it."""
    if quote is None or adj.empty:
        return adj
    t, px, pct = quote.get("time"), quote.get("price"), quote.get("pct_change")
    if t is None or pd.isna(t) or not px or pct is None or pd.isna(pct):
        return adj
    day = pd.Timestamp(pd.Timestamp(t).date())
    base = adj[adj["date"] < day].copy()
    if base.empty:
        return adj
    ret = float(pct) / 100
    k = (px / (1 + ret)) / float(base["close"].iloc[-1])
    if abs(k - 1) > 1e-9:
        for col in ("open", "high", "low", "close"):
            base[col] = base[col] * k
        base["factor"] = base["factor"] * k
    bar = {"date": day, "open": quote.get("open") or px, "high": quote.get("high") or px,
           "low": quote.get("low") or px, "close": px, "close_raw": px, "volume": quote.get("volume"),
           "amount": quote.get("amount"), "turnover_rate": quote.get("turnover_rate"), "event": "",
           "ret": ret, "factor": 1.0}
    return pd.concat([base, pd.DataFrame([bar])], ignore_index=True)


def daily(code: str, start: date | None = None, end: date | None = None,
          quote: dict | None = None) -> tuple[pd.DataFrame, str]:
    """Adjusted daily bars for [start, end], optionally including a live bar for today."""
    raw = raw_daily(code)
    if quote is not None:
        raw = with_live_bar(raw, quote)
    adj, label = adjusted_daily(code, raw)
    if start is not None:
        adj = adj[adj["date"] >= pd.Timestamp(start)]
    if end is not None:
        adj = adj[adj["date"] <= pd.Timestamp(end)]
    return adj.reset_index(drop=True), label


def close_panel(codes: list[str], start: date | None = None, workers: int = 6) -> pd.DataFrame:
    """Wide frame of adjusted closes (columns = codes), outer-joined on date."""
    from . import http

    results = http.pmap(lambda c: adjusted_daily(c)[0][["date", "close"]].set_index("date")["close"].rename(c),
                        codes, workers=workers)
    series = [r for r in results if isinstance(r, pd.Series)]
    if not series:
        return pd.DataFrame()
    panel = pd.concat(series, axis=1, sort=True)
    if start is not None:
        panel = panel[panel.index >= pd.Timestamp(start)]
    return panel


def last_trading_day(code: str = "sh000300") -> date:
    df = raw_daily(code)
    return pd.Timestamp(df["date"].max()).date() if not df.empty else now_bj().date()


def ma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def trailing_dividends(code: str, price: float | None) -> tuple[float | None, list[tuple[date, float]]]:
    """Trailing-12-month cash distributions per share and yield (%), for funds/ETFs."""
    segs, _ = factor_segments(code)
    divs = adjust.dividends_from_segments(segs)
    if not divs:
        raw = raw_daily(code)
        if market(code) == "HK" and not raw.empty:
            divs = [(d, c) for d, c, _ in adjust.events_from_text(raw, "HK")]
    cutoff = now_bj().date() - timedelta(days=365)
    ttm = sum(c for d, c in divs if d > cutoff)
    if not price or not np.isfinite(price):
        return None, divs
    return 100 * ttm / price, divs
