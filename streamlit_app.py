"""China A-share & Hong Kong real-time market platform (prototype).

Run:  streamlit run streamlit_app.py

The interface is in one language at a time: 中文 by default, English from the sidebar switch (ui/i18n.py). The
desktop app opens the site with ?app=desktop, which leaves out website-only parts.
"""

import os

import streamlit as st

from stockrt import published, sample, watchlist
from stockrt.calendar import now_bj, status
from stockrt.config import DEFAULT_RISK_FREE
from ui import i18n
from ui.i18n import t

st.set_page_config(
    page_title="尾盘选股 · China & HK markets",
    page_icon=":material/candlestick_chart:",
    layout="wide",
)
i18n.init()

# The results folder in use (a small file read). Downloading newer results, like every heavy task, happens
# in the worker process - the web server itself only reads files, so it always answers its health check.
published.use_current()
clock = None
if not sample.is_sample():
    from ui import scheduler

    clock = scheduler.start()   # queues the day's fixed results at their times, with deadlines and retries

# Settings shared by every page.
st.session_state.setdefault("up_red", True)
st.session_state.setdefault("rf_pct", DEFAULT_RISK_FREE * 100)
if "watchlist" not in st.session_state:
    st.session_state.watchlist = watchlist.load()

page = st.navigation(
    {
        "": [
            st.Page("app_pages/picks.py", title=t("尾盘选股 · A股", "Late-session picks · A-shares"),
                    icon=":material/leaderboard:", default=True),
            st.Page("app_pages/picks_hk.py", title=t("尾盘选股 · 港股（测试）", "Late-session picks · Hong Kong (test)"),
                    icon=":material/science:"),
        ],
        t("行情", "Markets"): [
            st.Page("app_pages/screener.py", title=t("选股器", "Screener & query"), icon=":material/filter_alt:"),
            st.Page("app_pages/intraday.py", title=t("14:00 盘中", "Intraday at 14:00"), icon=":material/schedule:"),
            st.Page("app_pages/live.py", title=t("实时看板", "Live dashboard"), icon=":material/monitoring:"),
            st.Page("app_pages/compare.py", title=t("对比与自选", "Compare & watchlist"),
                    icon=":material/compare_arrows:"),
            st.Page("app_pages/etfs.py", title=t("指数ETF", "Index ETFs"), icon=":material/account_balance:"),
        ],
        t("数据", "Data"): [
            st.Page("app_pages/downloads.py", title=t("数据下载", "Data downloads"), icon=":material/download:"),
            st.Page("app_pages/prompts.py", title=t("AI 提示词", "AI prompt builder"), icon=":material/content_copy:"),
            st.Page("app_pages/sources.py", title=t("数据来源", "Data sources"), icon=":material/dns:"),
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
    st.markdown(t(f"**{i18n.day(now, long=True)} · {now:%H:%M}** 北京 / 香港时间",
                  f"**{now:%a %d %b %Y · %H:%M}** Beijing / HK time"))
    delays = None
    for mkt, label in (("CN", t("A股", "A-shares")), ("HK", t("港股", "Hong Kong"))):
        s = status(mkt, now)
        if s.is_trading:
            color = "green"
        elif s.phase in ("lunch", "pre-open", "before open", "closing auction"):
            color = "orange"
        else:
            color = "gray"
        st.markdown(f":{color}-badge[{label}] {i18n.status_label(s)}")
        if s.is_trading:
            delays = quote_delays() if delays is None else delays
            lag = delays.get(mkt, 0)
            if lag >= 5:
                st.caption(t(f":material/schedule: 行情约延迟 {lag} 分钟（免费{label}数据）",
                             f":material/schedule: Quotes about {lag} min behind (free {label} feed)"))
    st.caption(t("开市 / 休市依据深交所官方交易日历和香港政府公众假期。",
                 "Open / closed from the official SZSE trading calendar and HKEX (HK government) holidays."))
    if clock is not None:
        icons = {"done": "✓", "running": "⟳", "waiting": "·", "due": "…", "late": "⚠", "failed": "✗"}
        items = clock.status()
        if items:
            st.caption(t("今日自动更新：", "Automatic updates today: ") + " · ".join(
                f"{x['at']} {i18n.bi(x['label']) if i18n.zh() else CLOCK_EN.get(x['label'], x['label'])} "
                f"{icons[x['state']]}" for x in items))
            for x in items:
                if x["state"] == "failed":
                    st.caption(f":red[✗ {x['label']}: {x['error']}] " + t("（会自动重试；日志见“数据来源”页）",
                                                                         "(retries by itself; log on **Data sources**)"))


CLOCK_EN = {"主力暗中吸筹": "Morning accumulation", "尾盘选股 15:00": "Late-session 15:00", "低位吸筹": "Buy the dip",
            "长线": "Long-term", "收盘选股": "After-close", "港股 16:00": "Hong Kong 16:00"}


def switch_data_mode() -> None:
    sample.set_mode("live" if st.session_state.live_data else "sample")
    st.cache_data.clear()


def switch_language() -> None:
    st.query_params["lang"] = st.session_state.lang


with st.sidebar:
    st.segmented_control("Language", list(i18n.LANGS), format_func=i18n.LANGS.get, key="lang", required=True,
                         on_change=switch_language, label_visibility="collapsed")
    market_clock()
    if not i18n.desktop():           # the sample dataset is a website demo; the desktop app always shows live data
        st.toggle(t("实时数据", "Live data"), value=not sample.is_sample(), key="live_data",
                  on_change=switch_data_mode,
                  disabled=not sample.available() or os.environ.get("STOCKRT_LOCK_MODE") == "1",
                  help=t("关闭：使用内置示例数据——即时、离线、不联网。打开：腾讯、新浪和东方财富的实时数据。",
                         "Off: the built-in sample dataset - instant, offline, nothing fetched. "
                         "On: real-time data from Tencent, Sina and East Money."))
    with st.expander(t("设置", "Settings"), icon=":material/tune:"):
        st.segmented_control(t("字号", "Text size"), list(i18n.FONT_SIZES), key="font_size", required=True,
                             format_func=lambda k: t(*i18n.FONT_SIZES[k][:2]))
        if i18n.desktop():
            st.segmented_control(t("外观", "Appearance"), list(i18n.THEMES), key="theme_pref", required=True,
                                 format_func=lambda k: t(*i18n.THEMES[k]),
                                 help=t("切换后窗口会在几秒内重新载入。", "The window reloads in a few seconds to switch."))
        else:
            st.caption(t("浅色 / 深色：右上角 ⋮ → Settings。", "Light / dark: ⋮ at the top right → Settings."))
        st.toggle(t("红涨绿跌（内地习惯）", "Red = up (mainland convention)"), key="up_red",
                  help=t("关闭：绿涨红跌（西方及部分港股软件）。", "Off: green = up, red = down (Western / many HK apps)."))
        st.number_input(t("无风险利率（年化 %）", "Risk-free rate (% a year)"), min_value=0.0, max_value=10.0, step=0.25,
                        key="rf_pct", help=t("用于夏普和索提诺比率。默认约等于 1 年期中国国债收益率。",
                                             "Used by Sharpe and Sortino ratios. Default ≈ 1-year China government "
                                             "bond yield."))

st.session_state.rf = st.session_state.rf_pct / 100
i18n.appearance()
if sample.is_sample():
    with st.container(horizontal=True, vertical_alignment="center"):
        st.badge(t("示例数据", "Sample data"), icon=":material/inventory_2:", color="orange")
        as_of = sample.meta().get("as_of", "")
        st.caption(t(f"{as_of} 的保存数据——不联网。在侧边栏打开 **实时数据** 查看实时行情。",
                     f"Saved session of {as_of} - nothing is fetched. Turn on **Live data** in the sidebar for "
                     "real-time prices."))
if clock is not None and clock.syncing:
    # A restarted server's disk is empty: nothing is shown or computed until the published results are back.
    @st.fragment(run_every="2s")
    def _loading_results() -> None:
        if not clock.syncing:
            st.rerun()
        st.info(t("正在载入最新结果，通常只需几秒。", "Loading the latest published results - usually a few seconds."),
                icon=":material/cloud_download:")

    _loading_results()
    st.stop()
page.run()
