"""Index ETF scorecard: the market reference for mainland and Hong Kong equities.

Columns (matching the US MarketPulse 'Markets' page):
Tracks, Period return, Volatility (ann.), Sharpe, Max drawdown, VaR 95% (1 day),
Beta, Correlation, Expense ratio, Fund assets, P/E (trailing), Dividend yield.

Sources: prices/fund size - Tencent; expense ratio - East Money fund fee page
(mainland ETFs) or the issuer figures in HK_EXPENSE_RATIOS; P/E - tracked
index P/E from the Tencent index quote (mainland indices only); dividend
yield - trailing-12-month distributions / price.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import analytics, history, http, realtime, storage
from .config import BENCHMARKS
from .sources import eastmoney
from .symbols import market

log = logging.getLogger(__name__)

ETFS = [
    {"code": "sh510300", "tracks": "CSI 300", "index": "sh000300"},
    {"code": "sh510050", "tracks": "SSE 50", "index": "sh000016"},
    {"code": "sh510500", "tracks": "CSI 500", "index": "sh000905"},
    {"code": "sh512100", "tracks": "CSI 1000", "index": "sh000852"},
    {"code": "sz159915", "tracks": "ChiNext", "index": "sz399006"},
    {"code": "sh588000", "tracks": "STAR 50", "index": "sh000688"},
    {"code": "sh510880", "tracks": "SSE Dividend", "index": "sh000015"},
    {"code": "sh512800", "tracks": "CSI Banks", "index": "sz399986"},
    {"code": "sh512690", "tracks": "CSI Liquor (baijiu)", "index": "sz399987"},
    {"code": "sh515880", "tracks": "CSI Telecom Equipment", "index": None},
    {"code": "hk02800", "tracks": "Hang Seng Index", "index": "hkHSI"},
    {"code": "hk02828", "tracks": "HS China Enterprises", "index": "hkHSCEI"},
    {"code": "hk03033", "tracks": "Hang Seng TECH", "index": "hkHSTECH"},
    {"code": "hk03188", "tracks": "CSI 300 (in HKD)", "index": "sh000300"},
]

# Ongoing charges (% a year) for HK ETFs, from issuer documents. East Money has
# no HK fund data, so these are maintained by hand - edit when issuers update.
# None renders as "n/a" rather than a guess.
HK_EXPENSE_RATIOS: dict[str, float | None] = {
    "hk02800": None,
    "hk02828": None,
    "hk03033": None,
    "hk03188": None,
}

FEE_MAX_AGE = 7 * 24 * 3600


def expense_ratio(code: str) -> float | None:
    if market(code) == "HK":
        return HK_EXPENSE_RATIOS.get(code)
    cached = storage.load_json("fees", code, max_age=FEE_MAX_AGE)
    if cached is not None:
        return cached.get("total")
    try:
        fees = eastmoney.fund_fees(code)
        storage.save_json("fees", code, fees)
        return fees.get("total")
    except Exception as e:  # noqa: BLE001
        log.info("fee lookup failed for %s: %s", code, e)
        stale = storage.load_json("fees", code)
        return stale.get("total") if stale else None


def scorecard(period: str, rf: float) -> pd.DataFrame:
    codes = [e["code"] for e in ETFS]
    index_codes = sorted({e["index"] for e in ETFS if e["index"]})
    q = realtime.quotes(codes + index_codes + list(BENCHMARKS.values())).set_index("code")
    panel = history.close_panel(codes + list(BENCHMARKS.values()))
    fees = dict(zip(codes, http.pmap(expense_ratio, codes, workers=6)))
    rows = []
    for e in ETFS:
        c = e["code"]
        if c not in panel:
            continue
        bench_code = BENCHMARKS[market(c)]
        sc = analytics.scorecard(panel[c].dropna(), panel[bench_code].dropna(), period, rf)
        price = q["price"].get(c)
        dy, _ = history.trailing_dividends(c, price)
        idx_pe = q["pe_ttm"].get(e["index"]) if e["index"] else None
        fee = fees.get(c)
        rows.append({
            "code": c,
            "name": q["name"].get(c, c),
            "tracks": e["tracks"],
            "price": price,
            "day_change": q["pct_change"].get(c),
            "period_return": sc["period_return"],
            "ann_vol": sc["ann_vol"],
            "sharpe": sc["sharpe"],
            "max_drawdown": sc["max_drawdown"],
            "var_95": sc["var_95"],
            "beta": sc["beta"],
            "corr": sc["corr"],
            "expense_ratio": fee if isinstance(fee, (int, float)) else None,
            "fund_assets": q["float_mcap"].get(c),
            "currency": q["currency"].get(c),
            "pe_ttm": idx_pe if idx_pe and not (isinstance(idx_pe, float) and np.isnan(idx_pe)) else None,
            "div_yield": dy,
            "benchmark": bench_code,
            "start": sc["start"],
        })
    return pd.DataFrame(rows)
