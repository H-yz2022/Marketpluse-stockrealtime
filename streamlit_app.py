"""China A-share & Hong Kong real-time market platform (prototype).

Run:  streamlit run streamlit_app.py
"""

import streamlit as st

from stockrt import watchlist
from stockrt.calendar import now_bj, status
from stockrt.config import DEFAULT_RISK_FREE

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
        "Markets": [
            st.Page("app_pages/live.py", title="Live dashboard", icon=":material/monitoring:", default=True),
            st.Page("app_pages/intraday.py", title="Intraday at 14:00", icon=":material/schedule:"),
            st.Page("app_pages/screener.py", title="Screener & query", icon=":material/filter_alt:"),
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


@st.fragment(run_every="30s")
def market_clock() -> None:
    now = now_bj()
    st.markdown(f"**{now:%a %d %b %Y · %H:%M}** Beijing / HK time")
    for mkt, label in (("CN", "A-shares"), ("HK", "Hong Kong")):
        s = status(mkt, now)
        color = "green" if s.is_trading else ("orange" if s.phase in ("lunch", "pre-open", "closing auction") else "gray")
        st.markdown(f":{color}-badge[{label}] {s.label}")
    st.caption("Holidays aren't in the calendar: when a market is shut, pages show the last session.")


with st.sidebar:
    market_clock()
    with st.expander("Settings", icon=":material/tune:"):
        st.toggle("Red = up (mainland convention)", key="up_red",
                  help="Off: green = up, red = down (Western / many HK apps).")
        st.number_input("Risk-free rate (% a year)", min_value=0.0, max_value=10.0, step=0.25, key="rf_pct",
                        help="Used by Sharpe and Sortino ratios. Default ≈ 1-year China government bond yield.")

st.session_state.rf = st.session_state.rf_pct / 100
page.run()
