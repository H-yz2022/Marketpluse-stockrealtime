"""China A-share & Hong Kong real-time market platform (prototype).

Run:  streamlit run streamlit_app.py
"""

import os

import streamlit as st

from stockrt import sample, watchlist
from stockrt.calendar import now_bj, status
from stockrt.config import DEFAULT_RISK_FREE

st.set_page_config(
    page_title="尾盘选股 · China & HK markets",
    page_icon=":material/candlestick_chart:",
    layout="wide",
)

# Settings shared by every page.
st.session_state.setdefault("up_red", True)
st.session_state.setdefault("rf_pct", DEFAULT_RISK_FREE * 100)
if "watchlist" not in st.session_state:
    st.session_state.watchlist = watchlist.load()

page = st.navigation(
    {
        "": [
            st.Page("app_pages/picks.py", title="尾盘选股 · A-shares", icon=":material/leaderboard:", default=True),
            st.Page("app_pages/picks_hk.py", title="尾盘选股 · Hong Kong (live test)", icon=":material/science:"),
        ],
        "Markets": [
            st.Page("app_pages/screener.py", title="Screener & query", icon=":material/filter_alt:"),
            st.Page("app_pages/intraday.py", title="Intraday at 14:00", icon=":material/schedule:"),
            st.Page("app_pages/live.py", title="Live dashboard", icon=":material/monitoring:"),
            st.Page("app_pages/compare.py", title="Compare & watchlist", icon=":material/compare_arrows:"),
            st.Page("app_pages/etfs.py", title="Index ETFs", icon=":material/account_balance:"),
        ],
        "Data": [
            st.Page("app_pages/downloads.py", title="Data downloads", icon=":material/download:"),
            st.Page("app_pages/prompts.py", title="AI prompt builder", icon=":material/content_copy:"),
            st.Page("app_pages/sources.py", title="Data sources", icon=":material/dns:"),
        ],
    },
    position="sidebar",
)


def quote_delays() -> dict[str, int]:
    """Minutes the latest index quote lags the clock (free HK quotes run ~15 min behind)."""
    if sample.is_sample():
        return {}
    try:
        from ui import cache  # loaded lazily: only while a market is trading

        q = cache.quotes(("sh000300", "hkHSI")).set_index("code")["time"]
        now = now_bj()
        return {"CN": int((now - q["sh000300"]).total_seconds() // 60),
                "HK": int((now - q["hkHSI"]).total_seconds() // 60)}
    except Exception:  # noqa: BLE001 - the clock still works without it
        return {}


@st.fragment(run_every="30s")
def market_clock() -> None:
    now = now_bj()
    st.markdown(f"**{now:%a %d %b %Y · %H:%M}** Beijing / HK time")
    delays = None
    for mkt, label in (("CN", "A-shares"), ("HK", "Hong Kong")):
        s = status(mkt, now)
        if s.is_trading:
            color = "green"
        elif s.phase in ("lunch", "pre-open", "before open", "closing auction"):
            color = "orange"
        else:
            color = "gray"
        st.markdown(f":{color}-badge[{label}] {s.label}")
        if s.is_trading:
            delays = quote_delays() if delays is None else delays
            lag = delays.get(mkt, 0)
            if lag >= 5:
                st.caption(f":material/schedule: Quotes about {lag} min behind (free {label} feed)")
    st.caption("Open / closed from the official SZSE trading calendar and HKEX (HK government) holidays.")


def switch_data_mode() -> None:
    sample.set_mode("live" if st.session_state.live_data else "sample")
    st.cache_data.clear()


with st.sidebar:
    market_clock()
    st.toggle("Live data", value=not sample.is_sample(), key="live_data", on_change=switch_data_mode,
              disabled=not sample.available() or os.environ.get("STOCKRT_LOCK_MODE") == "1",
              help="Off: the built-in sample dataset - instant, offline, nothing fetched. "
                   "On: real-time data from Tencent, Sina and East Money.")
    with st.expander("Settings", icon=":material/tune:"):
        st.toggle("Red = up (mainland convention)", key="up_red",
                  help="Off: green = up, red = down (Western / many HK apps).")
        st.number_input("Risk-free rate (% a year)", min_value=0.0, max_value=10.0, step=0.25, key="rf_pct",
                        help="Used by Sharpe and Sortino ratios. Default ≈ 1-year China government bond yield.")

st.session_state.rf = st.session_state.rf_pct / 100
if sample.is_sample():
    with st.container(horizontal=True, vertical_alignment="center"):
        st.badge("Sample data", icon=":material/inventory_2:", color="orange")
        st.caption(f"Saved session of {sample.meta().get('as_of', '')} - nothing is fetched. "
                   "Turn on **Live data** in the sidebar for real-time prices.")
page.run()
