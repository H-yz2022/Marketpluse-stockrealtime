"""Intraday at 14:00: morning session + early afternoon for the watchlist, ready before the close."""

import streamlit as st

from stockrt import capture, prompts
from stockrt.calendar import now_bj, status
from stockrt.export import stamp, to_csv_bytes
from stockrt.symbols import market
from ui import cache, charts
from ui.fmt import pct_col, style_signed
from ui.i18n import lang, status_label, t

names = cache.name_map()
CUTS = ["11:30", "13:30", "14:00", "14:30", "full"]

with st.container(horizontal=True, vertical_alignment="bottom"):
    codes = st.multiselect(t("股票", "Symbols"), list(dict.fromkeys(st.session_state.watchlist + list(names))),
                           default=st.session_state.watchlist[:12], key="intra_codes", persist_state="session",
                           format_func=lambda c: f"{c[2:]} · {names.get(c, c)}", max_selections=24,
                           help=t("默认为自选股。输入代码或名称可添加任何股票。",
                                  "Defaults to your watchlist. Type a code or name to add any stock."))
    cut_key = st.segmented_control(t("数据截至（北京时间）", "Data up to (Beijing time)"), CUTS, default="14:00",
                                   key="intra_cut_v2", persist_state="session", required=True,
                                   format_func=lambda c: t("全天", "Full day") if c == "full" else c)
cut = None if cut_key == "full" else cut_key
cut_label = t("全天", "Full day") if cut is None else cut
cn = status("CN")
st.caption(t(f"上午盘加下午截至 **{cut_label}** 的数据。A股北京时间 15:00 收盘，港股 16:00 收盘。A股现在：{status_label(cn)}。"
             "休市时使用最近一个交易日并显示其日期。",
             f"Morning session plus the afternoon up to **{cut_label}**. A-shares close at 15:00 and HK at 16:00 "
             f"Beijing time. A-shares now: {cn.label.lower()}. When a market is shut, the latest session is used and "
             "its date is shown."))

if not codes:
    st.info(t("请至少选择一只股票。", "Pick at least one symbol."))
    st.stop()

with st.spinner(t("正在载入分时数据…", "Loading minute data…")):
    table, minutes = cache.intraday_table(tuple(codes), cut)

if table.empty:
    st.warning(t("没有返回分时数据。", "No intraday data returned."))
    st.stop()

dates = sorted({str(d) for d in table["trade_date"]})
st.markdown(t(f"**截至 {cut_label} 的交易统计** · 交易日 {', '.join(dates)}",
              f"**Session statistics at {cut_label}** · trade date {', '.join(dates)}"))
cols = ["code", "name", "trade_date", "last", "day_return", "am_return", "pm_return", "last_30m_return",
        "vs_vwap", "range_position", "high_time", "low_time", "am_volume_share", "intraday_vol",
        "max_intraday_drawdown", "amount", "minutes"]
view = table[[c for c in cols if c in table.columns]].sort_values("day_return", ascending=False)
st.dataframe(
    style_signed(view, ["day_return", "am_return", "pm_return", "last_30m_return", "vs_vwap"]),
    hide_index=True,
    column_config={
        "code": st.column_config.TextColumn(t("代码", "Code"), pinned=True),
        "name": st.column_config.TextColumn(t("名称", "Name"), pinned=True),
        "trade_date": st.column_config.DateColumn(t("日期", "Date")),
        "last": st.column_config.NumberColumn(t("最新价", "Last"), format="%.2f"),
        "day_return": pct_col(t("当日", "Day"), t("最新价相对昨收", "Latest vs previous close")),
        "am_return": pct_col(t("上午", "Morning"), t("上午收盘相对昨收", "Morning close vs previous close")),
        "pm_return": pct_col(t("下午", "Afternoon"), t("最新价相对上午收盘", "Latest vs morning close")),
        "last_30m_return": pct_col(t("最近 30 分钟", "Last 30 min")),
        "vs_vwap": pct_col(t("相对均价", "vs VWAP"), t("最新价相对当日均价", "Latest price vs session average price")),
        "range_position": st.column_config.ProgressColumn(
            t("区间位置", "Range position"), min_value=0, max_value=1, format="%.2f",
            help=t("0 = 当日最低，1 = 当日最高", "0 = at the day's low, 1 = at the day's high")),
        "high_time": st.column_config.TextColumn(t("最高时间", "High at")),
        "low_time": st.column_config.TextColumn(t("最低时间", "Low at")),
        "am_volume_share": pct_col(t("上午成交占比", "Morning vol. share")),
        "intraday_vol": pct_col(t("日内波动", "Intraday vol."),
                                t("1 分钟收益波动率，折算为一个交易日", "1-minute return volatility scaled to one session")),
        "max_intraday_drawdown": pct_col(t("日内最大回撤", "Max intraday DD")),
        "amount": st.column_config.NumberColumn(t("成交额", "Value traded"), format="compact"),
        "minutes": st.column_config.NumberColumn(t("分钟数", "Minutes"), format="%d"),
    },
)

labels = {c: names.get(c, c) for c in codes}
for mkt, title in (("CN", t("A股", "A-shares")), ("HK", t("港股", "Hong Kong"))):
    m = minutes[minutes["code"].map(market) == mkt] if not minutes.empty else minutes
    if m.empty:
        continue
    with st.container(border=True):
        st.markdown(t(f"**{title} · 相对昨收的涨跌幅，截至 {cut_label}**",
                      f"**{title} · % change from previous close, to {cut_label}**"))
        shown = list(dict.fromkeys(m["code"]))[:8]
        if len(set(m["code"])) > 8:
            st.caption(t("只显示前 8 只——表格和下载包含全部。",
                         "Showing the first 8 symbols - the table and downloads include all of them."))
        st.altair_chart(charts.intraday_multi(m[m["code"].isin(shown)], labels, mkt, cut))

with st.container(horizontal=True):
    st.download_button(t("1 分钟数据（CSV）", "1-minute dataset (CSV)"), to_csv_bytes(minutes), on_click="ignore",
                       file_name=f"intraday_1min_to_{(cut or 'close').replace(':', '')}_{stamp()}.csv",
                       mime="text/csv", icon=":material/download:", type="primary",
                       help=t("长格式：每只股票每分钟一行，含价格、成交量、成交额、均价和涨跌幅。",
                              "Long format: one row per symbol per minute, with price, volume, value, VWAP, % change."))
    st.download_button(t("交易统计（CSV）", "Session statistics (CSV)"), to_csv_bytes(view), on_click="ignore",
                       file_name=f"intraday_stats_{(cut or 'close').replace(':', '')}_{stamp()}.csv",
                       mime="text/csv", icon=":material/download:")
    with st.popover(t("本表的 AI 提示词", "AI prompt for this table"), icon=":material/content_copy:"):
        st.code(prompts.intraday_review(view, f"{cut_label} on {', '.join(dates)}", lang()),
                language=None, wrap_lines=True, height=320)

st.divider()
st.subheader(t("此刻抓取全市场数据", "Capture the whole market at this moment"), anchor=False)
st.caption(t("14:00 的换手率、量比和资金流向收盘后无法还原，需要当时抓取。每次抓取保存全部A股行情（含主力资金）、全部港股行情和自选股的 "
             "1 分钟K线到 data/snapshots/<日期>/，可在“数据下载”页找到。",
             "Turnover, volume ratio and order flow at 14:00 can't be rebuilt after the close, so capture them when "
             "they happen. Each capture saves every A-share quote (with main-fund flow), every HK quote, and the "
             "watchlist's 1-minute bars to data/snapshots/<date>/. Find them on the Data downloads page."))
auto = cache.auto_capture()
c1, c2 = st.columns([1, 2])
with c1:
    if st.button(t("立即抓取", "Capture now"), icon=":material/photo_camera:", type="primary"):
        with st.spinner(t("正在抓取约 8,000 条行情和资金流向（约 30–60 秒）…",
                          "Capturing ~8,000 quotes and money flow (about 30-60 s)…")):
            files = capture.capture()
        st.success(t("已保存 ", "Saved ") + ", ".join(f.name for f in files))
with c2:
    auto.enabled = st.toggle(t("程序运行时，工作日自动抓取", "Auto-capture on weekdays while the app is running"),
                             value=auto.enabled,
                             help=t("在下面的时间（北京时间）于后台运行。无人值守的抓取请用 scripts/capture_snapshot.py 配合 Windows "
                                    "任务计划——见 README。",
                                    "Runs in the background at the times below (Beijing). For unattended capture "
                                    "use scripts/capture_snapshot.py with Windows Task Scheduler - see README."))
    picked = st.pills(t("抓取时间", "Capture times"), ["10:30", "11:30", "13:30", "14:00", "14:30", "14:50", "15:00"],
                      selection_mode="multi", default=sorted(auto.times), key="auto_times")
    auto.times = set(picked or [])
    if auto.last_runs:
        st.caption(t("最近运行：", "Last runs: ") + ", ".join(sorted(auto.last_runs.values())))
    if auto.last_error:
        st.caption(f":red[{t('最近错误', 'Last error')}: {auto.last_error}]")
times = ", ".join(sorted(auto.times))
st.caption(t(f"现在北京时间 {now_bj():%H:%M}。自动抓取{'已开启' if auto.enabled else '已关闭'}"
             f"{'（' + times + '）' if auto.enabled and auto.times else ''}。",
             f"Now {now_bj():%H:%M} Beijing. Auto-capture is {'on' if auto.enabled else 'off'}"
             f"{' for ' + times if auto.enabled and auto.times else ''}."))
