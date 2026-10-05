"""Cached data loaders for the Streamlit app. TTLs follow how fast each dataset changes."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stockrt import (
    analytics,
    capture,
    etfs,
    history,
    intraday,
    realtime,
)
from stockrt.calendar import now_bj
from stockrt.sources import eastmoney


@st.cache_data(ttl="15s", max_entries=200, show_spinner=False)
def quotes(codes: tuple[str, ...]) -> pd.DataFrame:
    return realtime.quotes(list(codes))


@st.cache_data(ttl="1m", max_entries=4, show_spinner=False)
def snapshot(mkt: str) -> tuple[pd.DataFrame, str]:
    df = realtime.market_snapshot(mkt)
    return df, now_bj().strftime("%Y-%m-%d %H:%M:%S")


@st.cache_data(ttl="12h", max_entries=4, show_spinner=False)
def universe(mkt: str) -> pd.DataFrame:
    return realtime.universe(mkt)


@st.cache_data(ttl="12h", show_spinner=False)
def name_map() -> dict[str, str]:
    names: dict[str, str] = {}
    for mkt in ("CN", "HK"):
        try:
            u = universe(mkt)
            names.update(dict(zip(u["code"], u["name"])))
        except Exception:  # noqa: BLE001 - names are a nicety
            pass
    return names


@st.cache_data(ttl="30s", max_entries=100, show_spinner=False)
def minute_today(code: str):
    return intraday.today(code)


@st.cache_data(ttl="2m", max_entries=100, show_spinner=False)
def minute_5day(code: str) -> pd.DataFrame:
    return intraday.five_days(code)


@st.cache_data(ttl="1m", max_entries=200, show_spinner=False)
def daily(code: str) -> tuple[pd.DataFrame, str]:
    return history.adjusted_daily(code)


@st.cache_data(ttl="2m", max_entries=50, show_spinner=False)
def panel(codes: tuple[str, ...]) -> pd.DataFrame:
    return history.close_panel(list(codes))


@st.cache_data(ttl="1m", max_entries=100, show_spinner=False)
def main_flow(codes: tuple[str, ...]) -> pd.DataFrame:
    return realtime.main_flow(list(codes))


@st.cache_data(ttl="15m", max_entries=100, show_spinner=False)
def announcements(code: str) -> pd.DataFrame:
    try:
        return eastmoney.announcements(code, 30)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["date", "title", "category", "url", "pdf"])


@st.cache_data(ttl="2m", max_entries=8, show_spinner=False)
def intraday_table(codes: tuple[str, ...], cut_time: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    return intraday.watchlist_table(list(codes), name_map(), cut_time)


@st.cache_data(ttl="10m", max_entries=16, show_spinner=False)
def etf_scorecard(period: str, rf: float) -> pd.DataFrame:
    return etfs.scorecard(period, rf)


@st.cache_resource
def auto_capture() -> capture.AutoCapture:
    return capture.AutoCapture()


def bench_quote(mkt: str) -> dict | None:
    from stockrt.config import BENCHMARKS

    q = quotes((BENCHMARKS[mkt],))
    return q.iloc[0].to_dict() if not q.empty else None


def period_metrics(code: str, period: str, rf: float) -> dict:
    from stockrt.config import BENCHMARKS
    from stockrt.symbols import market

    df, _ = daily(code)
    bench, _ = daily(BENCHMARKS[market(code)])
    close = df.set_index("date")["close"]
    bclose = bench.set_index("date")["close"]
    return analytics.scorecard(close, bclose, period, rf)
