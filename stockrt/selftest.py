"""Live checks of every upstream endpoint: what works from this network right now.

Each check makes one small request and reports ok / latency / a sample value,
so a broken source is visible before it silently degrades a page.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from . import http
from .sources import eastmoney, sina, tencent


@dataclass
class Check:
    source: str
    what: str
    used_for: str
    fallback: str
    fn: Callable[[], str]


def _quote() -> str:
    q = tencent.quotes(["sh600519", "hk00700"])
    r = q.set_index("code")
    return f"600519 {r.loc['sh600519', 'price']} · 00700 {r.loc['hk00700', 'price']}"


def _minute() -> str:
    day, pc, bars = tencent.minute_today("sh600519")
    return f"{len(bars)} minutes on {day}"


def _fiveday() -> str:
    d = tencent.minute_5day("hk00700")
    return f"{d['date'].nunique()} sessions, {len(d)} minutes"


def _daily() -> str:
    d = tencent.daily("sh000300", start=None)
    return f"{len(d)} bars from {d['date'].min():%Y-%m-%d}"


def _mkline() -> str:
    d = tencent.minute_ohlc("sz000858", "m5", 50)
    return f"{len(d)} five-minute bars"


def _universe() -> str:
    n = sina._count("Market_Center.getHQNodeStockCount", "hs_a")
    return f"{n:,} A-shares listed"


def _factors() -> str:
    s = sina.factor_segments("sh600519")
    return f"{len(s)} adjustment segments, latest {s[-1][0]}"


def _flow_sina() -> str:
    f = sina.money_flow("sh600519")
    return f"main net {f['main_net'] / 1e6:,.1f}M"


def _flow_em() -> str:
    d = eastmoney.main_flow_batch(["sh600519"])
    return f"main net {d['main_net'].iloc[0] / 1e6:,.1f}M"


def _fees() -> str:
    f = eastmoney.fund_fees("sh510300")
    return f"510300 total fee {f['total']}%"


def _ann() -> str:
    a = eastmoney.announcements("sh600519", 3)
    return f"{len(a)} announcements, latest {a['date'].iloc[0] if len(a) else '-'}"


CHECKS = [
    Check("Tencent", "Real-time quotes (batch)", "every page, screener snapshot", "none (core)", _quote),
    Check("Tencent", "Today's 1-minute series", "intraday charts & 14:00 stats", "none (core)", _minute),
    Check("Tencent", "Last 5 sessions, 1-minute", "intraday downloads", "today's series", _fiveday),
    Check("Tencent", "Daily bars (amount, turnover)", "history, metrics, downloads", "disk cache", _daily),
    Check("Tencent", "Minute OHLC bars (A-shares)", "intraday OHLC downloads", "1-minute series", _mkline),
    Check("Sina", "Market universe lists", "screener, search, snapshots", "cached list (12 h)", _universe),
    Check("Sina", "Adjustment factors", "ratio-adjusted prices", "dividend text from Tencent", _factors),
    Check("Sina", "Money flow (per stock)", "main-funds rule, sentiment", "-", _flow_sina),
    Check("East Money", "Money flow (batch, incl. HK)", "main-funds rule (fast path)", "Sina per stock", _flow_em),
    Check("East Money", "Fund fee schedule", "ETF expense ratio", "cached value (7 days)", _fees),
    Check("East Money", "Company announcements", "sentiment, stock brief", "omitted", _ann),
]


def run_all() -> list[dict]:
    def one(c: Check) -> dict:
        t0 = time.perf_counter()
        try:
            sample = c.fn()
            ok, err = True, ""
        except Exception as e:  # noqa: BLE001 - reported per check
            sample, ok, err = "", False, f"{type(e).__name__}: {str(e)[:140]}"
        ms = round(1000 * (time.perf_counter() - t0))
        return {"source": c.source, "endpoint": c.what, "ok": ok, "latency_ms": ms,
                "sample": sample, "error": err, "used_for": c.used_for, "fallback": c.fallback}

    return [r for r in http.pmap(one, CHECKS, workers=6) if isinstance(r, dict)]
