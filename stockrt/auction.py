"""Pre-open auction (集合竞价 / HK pre-opening session): prices published before the open.

A-shares run a call auction 09:15-09:25 (orders may be cancelled until 09:20); the
matched price becomes the 09:25 opening price, and continuous trading starts at
09:30. Hong Kong's pre-opening session runs 09:00-09:20 with matching at about
09:20. During an auction the feed shows an *indicative* price - what would trade
if the auction ended now - in the quote's best bid and best ask, which sit at the
same level (Tencent may also put it in the price field). Nothing has traded yet.
After the open, the auction result is simply the opening price.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def indicative(q: pd.DataFrame) -> pd.DataFrame:
    """Add indicative auction price and gap vs previous close to a quote frame."""
    d = q.copy()
    bid, ask, px = d["bid1"].fillna(0), d["ask1"].fillna(0), d["price"].fillna(0)
    crossed = (bid > 0) & (bid == ask)
    d["indicative"] = np.where(crossed, bid, np.where(px > 0, px, np.nan))
    d["gap_pct"] = 100 * (d["indicative"] / d["prev_close"] - 1)
    return d


def opening(q: pd.DataFrame) -> pd.DataFrame:
    """After the open: the auction's result is the opening price."""
    d = q.copy()
    d["gap_pct"] = 100 * (d["open"] / d["prev_close"] - 1)
    return d[d["open"].fillna(0) > 0]


def breadth(d: pd.DataFrame) -> dict:
    g = d["gap_pct"].dropna()
    if g.empty:
        return {}
    return {"up": int((g > 0).sum()), "down": int((g < 0).sum()), "flat": int((g == 0).sum()),
            "median": float(g.median()), "up_3": int((g >= 3).sum()), "down_3": int((g <= -3).sum())}


def movers(d: pd.DataFrame, n: int = 10, min_value: float = 0.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Largest gaps up and down, ignoring ST names and (optionally) thin stocks."""
    x = d.dropna(subset=["gap_pct"])
    if "is_st" in x:
        x = x[~x["is_st"].fillna(False)]
    if min_value and "float_mcap" in x:
        x = x[x["float_mcap"].fillna(0) >= min_value]
    return x.nlargest(n, "gap_pct"), x.nsmallest(n, "gap_pct")
