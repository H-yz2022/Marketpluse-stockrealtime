"""Index ETFs: the market reference for mainland China and Hong Kong."""

import pandas as pd
import streamlit as st

from stockrt import analytics
from stockrt.export import stamp, to_csv_bytes
from ui import cache, charts
from ui.fmt import pct_col, style_signed
from ui.i18n import t

period = st.segmented_control(t("区间", "Period"), analytics.PERIODS, default="1Y", key="etf_period",
                              persist_state="session", required=True)
rf = st.session_state.rf

with st.spinner(t("正在载入ETF价格、费率和规模…", "Loading ETF prices, fees and fund sizes…")):
    df = cache.etf_scorecard(period, rf)

st.markdown(t(f"**指数ETF评分表 · {period}** · 贝塔和相关系数相对沪深300（内地ETF）或恒生指数（港股ETF）· 无风险利率 {rf:.2%}",
              f"**Index ETF scorecard · {period}** · beta and correlation vs CSI 300 (mainland ETFs) or "
              f"Hang Seng Index (HK ETFs) · risk-free {rf:.2%}"))
cols = ["code", "name", "tracks", "period_return", "ann_vol", "sharpe", "max_drawdown", "var_95", "beta", "corr",
        "expense_ratio", "fund_assets", "pe_ttm", "div_yield", "day_change", "currency"]
st.dataframe(
    style_signed(df[cols], ["period_return", "day_change"]),
    hide_index=True,
    column_config={
        "code": st.column_config.TextColumn(t("代码", "Code"), pinned=True),
        "name": st.column_config.TextColumn(t("指数ETF", "Index ETF"), pinned=True),
        "tracks": st.column_config.TextColumn(t("跟踪指数", "Tracks")),
        "period_return": pct_col(t("区间收益", "Period return"), t("含分红的总收益", "Total return incl. distributions")),
        "ann_vol": pct_col(t("波动率（年化）", "Volatility (ann.)")),
        "sharpe": st.column_config.NumberColumn(t("夏普比率", "Sharpe ratio"), format="%.2f"),
        "max_drawdown": pct_col(t("最大回撤", "Max drawdown")),
        "var_95": pct_col(t("VaR 95%（1天）", "VaR 95% (1 day)"),
                          t("区间内日收益的第 5 百分位", "5th-percentile daily return over the period")),
        "beta": st.column_config.NumberColumn(t("贝塔", "Beta"), format="%.2f"),
        "corr": st.column_config.NumberColumn(t("相关系数", "Correlation"), format="%.2f"),
        "expense_ratio": st.column_config.NumberColumn(
            t("费率", "Expense ratio"), format="%.2f%%",
            help=t("每年的管理费 + 托管费（+ 销售服务费）。内地：东方财富基金费率页。港股：stockrt/etfs.py 中维护的发行人数据"
                   "（未填写时为空）。",
                   "Management + custody (+ sales service) fees a year. Mainland: East Money fund fee page. "
                   "HK: issuer figures maintained in stockrt/etfs.py (blank until filled in).")),
        "fund_assets": st.column_config.NumberColumn(t("基金规模", "Fund assets"), format="compact",
                                                     help=t("份额 × 价格（人民币或港元），实时",
                                                            "Units outstanding × price (CNY or HKD), real time")),
        "pe_ttm": st.column_config.NumberColumn(t("市盈率（TTM）", "P/E (trailing)"), format="%.1f",
                                                help=t("所跟踪指数的滚动市盈率（内地指数）",
                                                       "Trailing P/E of the tracked index (mainland indices)")),
        "div_yield": st.column_config.NumberColumn(t("股息率", "Dividend yield"), format="%.2f%%",
                                                   help=t("近 12 个月每份分红 ÷ 价格",
                                                          "Distributions per unit over the last 12 months ÷ price")),
        "day_change": st.column_config.NumberColumn(t("今日", "Today"), format="%.2f%%"),
        "currency": st.column_config.TextColumn(t("币种", "Ccy"), width="small"),
    },
)
st.caption(t("港股ETF的市盈率为空：没有免费的实时来源发布恒生指数市盈率。港股费率在 stockrt/etfs.py 的 HK_EXPENSE_RATIOS 中填写前为空。",
             "P/E is blank for Hong Kong ETFs: no free real-time source publishes Hang Seng index P/E. "
             "HK expense ratios are blank until added to HK_EXPENSE_RATIOS in stockrt/etfs.py."))

codes = df["code"].tolist()
labels = dict(zip(df["code"], df["tracks"]))
panel = cache.panel(tuple(codes))
pick = st.pills(t("显示在图表中", "Show on charts"), codes, selection_mode="multi", default=codes[:6],
                format_func=lambda c: labels.get(c, c), key="etf_pick")
if pick:
    pick = pick[:8]
    c1, c2 = st.columns([1.5, 1])
    with c1:
        with st.container(border=True):
            st.markdown(t(f"**100 元的增长 · {period}**", f"**Growth of 100 · {period}**"))
            st.altair_chart(charts.multi_line(analytics.rebased(panel[pick], period), labels, None, ",.0f",
                                              baseline=100))
    with c2:
        with st.container(border=True):
            st.markdown(t("**日收益相关系数**", "**Correlation of daily returns**"))
            st.altair_chart(charts.corr_heatmap(analytics.correlation_matrix(panel[pick], period), labels))
    yearly = {labels[c]: analytics.yearly_returns(panel[c].dropna()) for c in pick}
    with st.container(border=True):
        st.markdown(t("**自然年收益**（最近一年为年初至今）", "**Calendar-year returns** (latest year to date)"))
        st.altair_chart(charts.returns_heatmap(pd.DataFrame(yearly).sort_index(ascending=False).head(10)))

st.download_button(t("ETF评分表（CSV）", "ETF scorecard (CSV)"), to_csv_bytes(df),
                   file_name=f"etf_scorecard_{period}_{stamp()}.csv", mime="text/csv", on_click="ignore",
                   icon=":material/download:")
