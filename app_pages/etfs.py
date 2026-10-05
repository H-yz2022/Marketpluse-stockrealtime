"""Index ETFs: the market reference for mainland China and Hong Kong."""

import pandas as pd
import streamlit as st

from stockrt import analytics
from stockrt.export import stamp, to_csv_bytes
from ui import cache, charts
from ui.fmt import pct_col, style_signed

period = st.segmented_control("Period", analytics.PERIODS, default="1Y", key="etf_period",
                              persist_state="session", required=True)
rf = st.session_state.rf

with st.spinner("Loading ETF prices, fees and fund sizes…"):
    df = cache.etf_scorecard(period, rf)

st.markdown(f"**Index ETF scorecard · {period}** · beta and correlation vs CSI 300 (mainland ETFs) or "
            f"Hang Seng Index (HK ETFs) · risk-free {rf:.2%}")
cols = ["code", "name", "tracks", "period_return", "ann_vol", "sharpe", "max_drawdown", "var_95", "beta", "corr",
        "expense_ratio", "fund_assets", "pe_ttm", "div_yield", "day_change", "currency"]
st.dataframe(
    style_signed(df[cols], ["period_return", "day_change"]),
    hide_index=True,
    column_config={
        "code": st.column_config.TextColumn("Code", pinned=True),
        "name": st.column_config.TextColumn("Index ETF", pinned=True),
        "tracks": st.column_config.TextColumn("Tracks"),
        "period_return": pct_col("Period return", "Total return incl. distributions"),
        "ann_vol": pct_col("Volatility (ann.)"),
        "sharpe": st.column_config.NumberColumn("Sharpe ratio", format="%.2f"),
        "max_drawdown": pct_col("Max drawdown"),
        "var_95": pct_col("VaR 95% (1 day)", "5th-percentile daily return over the period"),
        "beta": st.column_config.NumberColumn("Beta", format="%.2f"),
        "corr": st.column_config.NumberColumn("Correlation", format="%.2f"),
        "expense_ratio": st.column_config.NumberColumn(
            "Expense ratio", format="%.2f%%",
            help="Management + custody (+ sales service) fees a year. Mainland: East Money fund fee page. "
                 "HK: issuer figures maintained in stockrt/etfs.py (blank until filled in)."),
        "fund_assets": st.column_config.NumberColumn("Fund assets", format="compact",
                                                     help="Units outstanding × price (CNY or HKD), real time"),
        "pe_ttm": st.column_config.NumberColumn("P/E (trailing)", format="%.1f",
                                                help="Trailing P/E of the tracked index (mainland indices)"),
        "div_yield": st.column_config.NumberColumn("Dividend yield", format="%.2f%%",
                                                   help="Distributions per unit over the last 12 months ÷ price"),
        "day_change": st.column_config.NumberColumn("Today", format="%.2f%%"),
        "currency": st.column_config.TextColumn("Ccy", width="small"),
    },
)
st.caption("P/E is blank for Hong Kong ETFs: no free real-time source publishes Hang Seng index P/E. "
           "HK expense ratios are blank until added to HK_EXPENSE_RATIOS in stockrt/etfs.py.")

codes = df["code"].tolist()
labels = dict(zip(df["code"], df["tracks"]))
panel = cache.panel(tuple(codes))
pick = st.pills("Show on charts", codes, selection_mode="multi", default=codes[:6],
                format_func=lambda c: labels.get(c, c), key="etf_pick")
if pick:
    pick = pick[:8]
    c1, c2 = st.columns([1.5, 1])
    with c1:
        with st.container(border=True):
            st.markdown(f"**Growth of 100 · {period}**")
            st.altair_chart(charts.multi_line(analytics.rebased(panel[pick], period), labels, None, ",.0f",
                                              baseline=100))
    with c2:
        with st.container(border=True):
            st.markdown("**Correlation of daily returns**")
            st.altair_chart(charts.corr_heatmap(analytics.correlation_matrix(panel[pick], period), labels))
    yearly = {labels[c]: analytics.yearly_returns(panel[c].dropna()) for c in pick}
    with st.container(border=True):
        st.markdown("**Calendar-year returns** (latest year to date)")
        st.altair_chart(charts.returns_heatmap(pd.DataFrame(yearly).sort_index(ascending=False).head(10)))

st.download_button("ETF scorecard (CSV)", to_csv_bytes(df), file_name=f"etf_scorecard_{period}_{stamp()}.csv",
                   mime="text/csv", on_click="ignore", icon=":material/download:")
