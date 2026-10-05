"""Intraday at 14:00: morning session + early afternoon for the watchlist, ready before the close."""

import streamlit as st

from stockrt import capture, prompts
from stockrt.calendar import now_bj, status
from stockrt.export import stamp, to_csv_bytes
from stockrt.symbols import market
from ui import cache, charts
from ui.fmt import pct_col, style_signed

names = cache.name_map()
CUTS = ["11:30", "13:30", "14:00", "14:30", "Full day"]

with st.container(horizontal=True, vertical_alignment="bottom"):
    codes = st.multiselect("Symbols", list(dict.fromkeys(st.session_state.watchlist + list(names))),
                           default=st.session_state.watchlist[:12], key="intra_codes", persist_state="session",
                           format_func=lambda c: f"{c[2:]} · {names.get(c, c)}", max_selections=24,
                           help="Defaults to your watchlist. Type a code or name to add any stock.")
    cut_label = st.segmented_control("Data up to (Beijing time)", CUTS, default="14:00", key="intra_cut",
                                     persist_state="session", required=True)
cut = None if cut_label == "Full day" else cut_label
cn = status("CN")
st.caption(
    f"Morning session plus the afternoon up to **{cut_label}**. A-shares close at 15:00 and HK at 16:00 Beijing time. "
    f"A-shares now: {cn.label.lower()}. When a market is shut, the latest session is used and its date is shown.")

if not codes:
    st.info("Pick at least one symbol.")
    st.stop()

with st.spinner("Loading minute data…"):
    table, minutes = cache.intraday_table(tuple(codes), cut)

if table.empty:
    st.warning("No intraday data returned.")
    st.stop()

dates = sorted({str(d) for d in table["trade_date"]})
st.markdown(f"**Session statistics at {cut_label}** · trade date {', '.join(dates)}")
cols = ["code", "name", "trade_date", "last", "day_return", "am_return", "pm_return", "last_30m_return",
        "vs_vwap", "range_position", "high_time", "low_time", "am_volume_share", "intraday_vol",
        "max_intraday_drawdown", "amount", "minutes"]
view = table[[c for c in cols if c in table.columns]].sort_values("day_return", ascending=False)
st.dataframe(
    style_signed(view, ["day_return", "am_return", "pm_return", "last_30m_return", "vs_vwap"]),
    hide_index=True,
    column_config={
        "code": st.column_config.TextColumn("Code", pinned=True),
        "name": st.column_config.TextColumn("Name", pinned=True),
        "trade_date": st.column_config.DateColumn("Date"),
        "last": st.column_config.NumberColumn("Last", format="%.2f"),
        "day_return": pct_col("Day", "Latest vs previous close"),
        "am_return": pct_col("Morning", "Morning close vs previous close"),
        "pm_return": pct_col("Afternoon", "Latest vs morning close"),
        "last_30m_return": pct_col("Last 30 min"),
        "vs_vwap": pct_col("vs VWAP", "Latest price vs session average price"),
        "range_position": st.column_config.ProgressColumn(
            "Range position", min_value=0, max_value=1, format="%.2f",
            help="0 = at the day's low, 1 = at the day's high"),
        "high_time": st.column_config.TextColumn("High at"),
        "low_time": st.column_config.TextColumn("Low at"),
        "am_volume_share": pct_col("Morning vol. share"),
        "intraday_vol": pct_col("Intraday vol.", "1-minute return volatility scaled to one session"),
        "max_intraday_drawdown": pct_col("Max intraday DD"),
        "amount": st.column_config.NumberColumn("Value traded", format="compact"),
        "minutes": st.column_config.NumberColumn("Minutes", format="%d"),
    },
)

labels = {c: names.get(c, c) for c in codes}
for mkt, title in (("CN", "A-shares"), ("HK", "Hong Kong")):
    m = minutes[minutes["code"].map(market) == mkt] if not minutes.empty else minutes
    if m.empty:
        continue
    with st.container(border=True):
        st.markdown(f"**{title} · % change from previous close, to {cut_label}**")
        shown = list(dict.fromkeys(m["code"]))[:8]
        if len(set(m["code"])) > 8:
            st.caption("Showing the first 8 symbols - the table and downloads include all of them.")
        st.altair_chart(charts.intraday_multi(m[m["code"].isin(shown)], labels, mkt, cut))

with st.container(horizontal=True):
    st.download_button("1-minute dataset (CSV)", to_csv_bytes(minutes), on_click="ignore",
                       file_name=f"intraday_1min_to_{(cut or 'close').replace(':', '')}_{stamp()}.csv",
                       mime="text/csv", icon=":material/download:", type="primary",
                       help="Long format: one row per symbol per minute, with price, volume, value, VWAP, % change.")
    st.download_button("Session statistics (CSV)", to_csv_bytes(view), on_click="ignore",
                       file_name=f"intraday_stats_{(cut or 'close').replace(':', '')}_{stamp()}.csv",
                       mime="text/csv", icon=":material/download:")
    with st.popover("AI prompt for this table", icon=":material/content_copy:"):
        lang = st.segmented_control("Language", ["en", "zh"], default="en", key="intra_lang", required=True,
                                    format_func=lambda x: {"en": "English", "zh": "中文"}[x])
        st.code(prompts.intraday_review(view, f"{cut_label} on {', '.join(dates)}", lang),
                language=None, wrap_lines=True, height=320)

st.divider()
st.subheader("Capture the whole market at this moment", anchor=False)
st.caption("Turnover, volume ratio and order flow at 14:00 can't be rebuilt after the close, so capture them when "
           "they happen. Each capture saves every A-share quote (with main-fund flow), every HK quote, and the "
           "watchlist's 1-minute bars to data/snapshots/<date>/. Find them on the Data downloads page.")
auto = cache.auto_capture()
c1, c2 = st.columns([1, 2])
with c1:
    if st.button("Capture now", icon=":material/photo_camera:", type="primary"):
        with st.spinner("Capturing ~8,000 quotes and money flow (about 30-60 s)…"):
            files = capture.capture()
        st.success("Saved " + ", ".join(f.name for f in files))
with c2:
    auto.enabled = st.toggle("Auto-capture on weekdays while the app is running", value=auto.enabled,
                             help="Runs in the background at the times below (Beijing). For unattended capture "
                                  "use scripts/capture_snapshot.py with Windows Task Scheduler - see README.")
    picked = st.pills("Capture times", ["10:30", "11:30", "13:30", "14:00", "14:30", "14:50", "15:00"],
                      selection_mode="multi", default=sorted(auto.times), key="auto_times")
    auto.times = set(picked or [])
    if auto.last_runs:
        st.caption("Last runs: " + ", ".join(sorted(auto.last_runs.values())))
    if auto.last_error:
        st.caption(f":red[Last error: {auto.last_error}]")
st.caption(f"Now {now_bj():%H:%M} Beijing. Auto-capture is {'on' if auto.enabled else 'off'}"
           f"{' for ' + ', '.join(sorted(auto.times)) if auto.enabled and auto.times else ''}.")
