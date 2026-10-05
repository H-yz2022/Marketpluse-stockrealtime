"""Intraday (1-minute) data: today's session, last five sessions, and session statistics.

The 14:00 use case: everything from the morning session plus the first one or
two hours of the afternoon, summarised so a decision can be made before the
close (15:00 Shanghai/Shenzhen, 16:00 Hong Kong).
"""

from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd

from .calendar import total_minutes
from .sources import tencent
from .symbols import market


def enrich(bars: pd.DataFrame, prev_close: float | None) -> pd.DataFrame:
    """Add % change vs previous close and the running VWAP."""
    df = bars.copy()
    if df.empty:
        return df
    regular = df["session"].isin(["AM", "PM"])
    cum_amt = df["amount"].where(regular, 0).cumsum()
    cum_vol = df["volume"].where(regular, 0).cumsum()
    df["vwap"] = (cum_amt / cum_vol.replace(0, np.nan)).ffill()
    if prev_close:
        df["pct"] = 100 * (df["price"] / prev_close - 1)
    else:
        df["pct"] = np.nan
    return df


def today(code: str) -> tuple[date, float | None, pd.DataFrame]:
    day, prev_close, bars = tencent.minute_today(code)
    return day, prev_close, enrich(bars, prev_close)


def five_days(code: str) -> pd.DataFrame:
    bars = tencent.minute_5day(code)
    if bars.empty:
        return bars
    frames = [enrich(g, g["prev_close"].iloc[0]) for _, g in bars.groupby("date", sort=True)]
    return pd.concat(frames, ignore_index=True)


def cut(bars: pd.DataFrame, hhmm: str | None) -> pd.DataFrame:
    """Keep bars up to and including a clock time such as '14:00'. None keeps everything."""
    if not hhmm or bars.empty:
        return bars
    h, m = (int(x) for x in hhmm.split(":"))
    return bars[bars["datetime"].dt.time <= time(h, m)]


def stats(bars: pd.DataFrame, prev_close: float | None, mkt: str) -> dict:
    """Session statistics for (possibly cut) intraday bars."""
    reg = bars[bars["session"].isin(["AM", "PM"])]
    if reg.empty:
        return {}
    am = reg[reg["session"] == "AM"]
    pm = reg[reg["session"] == "PM"]
    last = float(reg["price"].iloc[-1])
    first = float(reg["price"].iloc[0])
    hi_i, lo_i = reg["price"].idxmax(), reg["price"].idxmin()
    am_close = float(am["price"].iloc[-1]) if not am.empty else None
    rets = reg["price"].pct_change().dropna()
    running_max = reg["price"].cummax()
    vwap = float(reg["vwap"].iloc[-1]) if "vwap" in reg else None
    last30 = reg.tail(31)
    out = {
        "as_of": reg["datetime"].iloc[-1],
        "last": last,
        "open": first,
        "high": float(reg.loc[hi_i, "price"]),
        "high_time": reg.loc[hi_i, "datetime"].strftime("%H:%M"),
        "low": float(reg.loc[lo_i, "price"]),
        "low_time": reg.loc[lo_i, "datetime"].strftime("%H:%M"),
        "day_return": (last / prev_close - 1) if prev_close else None,
        "open_gap": (first / prev_close - 1) if prev_close else None,
        "am_return": (am_close / prev_close - 1) if (am_close and prev_close) else None,
        "pm_return": (last / am_close - 1) if (am_close and not pm.empty) else None,
        "last_30m_return": float(last30["price"].iloc[-1] / last30["price"].iloc[0] - 1) if len(last30) > 1 else None,
        "vwap": vwap,
        "vs_vwap": (last / vwap - 1) if vwap else None,
        "range_position": ((last - reg["price"].min()) / (reg["price"].max() - reg["price"].min())
                           if reg["price"].max() > reg["price"].min() else 0.5),
        "volume": float(reg["volume"].sum()),
        "amount": float(reg["amount"].sum()),
        "am_volume_share": float(am["volume"].sum() / reg["volume"].sum()) if reg["volume"].sum() else None,
        # Realised volatility of 1-minute returns, scaled to one full session.
        "intraday_vol": float(rets.std(ddof=1) * np.sqrt(total_minutes(mkt))) if len(rets) > 2 else None,
        "max_intraday_drawdown": float((reg["price"] / running_max - 1).min()),
        "minutes": int(len(reg)),
    }
    return out


def watchlist_table(codes: list[str], names: dict[str, str], hhmm: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Session stats per symbol at a cut-off time, plus the long-format minute dataset."""
    from . import http

    def one(code: str):
        day, pc, bars = today(code)
        b = cut(bars, hhmm)
        s = stats(b, pc, market(code))
        b = b.assign(code=code, name=names.get(code, code), trade_date=day, prev_close=pc)
        return code, day, s, b

    rows, frames = [], []
    for res in http.pmap(one, codes, workers=6):
        if isinstance(res, Exception):
            continue
        code, day, s, b = res
        if s:
            rows.append({"code": code, "name": names.get(code, code), "trade_date": day, **s})
        frames.append(b)
    table = pd.DataFrame(rows)
    minutes = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return table, minutes
