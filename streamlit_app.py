"""China A-share & Hong Kong real-time market platform (prototype).

Run:  streamlit run streamlit_app.py
"""

import os
from datetime import time

import streamlit as st

from stockrt import sample, watchlist
from stockrt.calendar import now_bj, status
from stockrt.config import DEFAULT_RISK_FREE
from ui import cache

st.set_page_config(
    page_title="China & HK markets",
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
            st.Page("app_pages/picks.py", title="Picks at 14:00 / 14:30", icon=":material/leaderboard:",
                    default=True),
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


def last_print_dates() -> dict[str, object]:
    """Date of each market's latest index quote - a holiday shows up as no print today."""
    if sample.is_sample():
        return {}
    try:
        q = cache.quotes(("sh000300", "hkHSI")).set_index("code")["time"]
        return {"CN": q["sh000300"].date(), "HK": q["hkHSI"].date()}
    except Exception:  # noqa: BLE001 - the clock still works without it
        return {}


@st.fragment(run_every="30s")
def market_clock() -> None:
    now = now_bj()
    st.markdown(f"**{now:%a %d %b %Y · %H:%M}** Beijing / HK time")
    last = last_print_dates()
    for mkt, label in (("CN", "A-shares"), ("HK", "Hong Kong")):
        s = status(mkt, now)
        text = s.label
        color = "green" if s.is_trading else ("orange" if s.phase in ("lunch", "pre-open", "closing auction") else "gray")
        in_hours = s.phase in ("morning", "lunch", "afternoon", "closing auction") and now.time() >= time(9, 32)
        if in_hours and last.get(mkt) and last[mkt] < now.date():
            text, color = "Closed today (holiday)", "gray"
        st.markdown(f":{color}-badge[{label}] {text}")
    st.caption("When a market is shut, pages show its last session.")


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
        st.caption(sample.describe() + " Turn on **Live data** in the sidebar for real-time prices.")
page.run()
