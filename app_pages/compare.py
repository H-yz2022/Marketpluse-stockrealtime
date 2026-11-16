"""Compare & watchlist: multi-period returns, risk metrics, correlation, yearly returns,
and a market-driven vs company-specific breakdown for one stock."""

import pandas as pd
import streamlit as st

from stockrt import analytics, prompts, watchlist
from stockrt.config import BENCHMARKS, INDEX_NAMES, STARTER_GROUPS
from stockrt.export import stamp, to_csv_bytes
from stockrt.symbols import market
from ui import cache, charts
from ui.fmt import pct, pct_col, style_signed
from ui.i18n import group_name, index_name, lang, t

names = cache.name_map()
all_codes = list(dict.fromkeys(st.session_state.watchlist + list(names)))
idx_names = {c: index_name(n) for c, n in INDEX_NAMES.items()}
fmt = lambda c: f"{c[2:]} · {names.get(c, idx_names.get(c, c))}"  # noqa: E731

with st.expander(t(f"自选股（{len(st.session_state.watchlist)} 只）", f"Watchlist ({len(st.session_state.watchlist)} symbols)"),
                 icon=":material/bookmark:"):
    wl = st.multiselect(t("股票", "Symbols"), all_codes, default=st.session_state.watchlist, format_func=fmt,
                        key="wl_edit", help=t("输入代码或名称。保存到 data/watchlist.json。",
                                              "Type a code or name. Saved to data/watchlist.json."))
    group = st.pills(t("添加主题组", "Add a theme group"), list(STARTER_GROUPS), key="wl_group", format_func=group_name)
    with st.container(horizontal=True):
        if st.button(t("保存自选股", "Save watchlist"), icon=":material/save:", type="primary"):
            st.session_state.watchlist = list(dict.fromkeys(wl + (STARTER_GROUPS[group] if group else [])))
            watchlist.save(st.session_state.watchlist)
            st.toast(t("自选股已保存", "Watchlist saved"))
            st.rerun()
        st.caption(t("选择一个组后按保存即可追加。", "Pick a group and press Save to append it."))

with st.container(horizontal=True, vertical_alignment="bottom"):
    picks = st.multiselect(t("对比（最多 8 只）", "Compare (up to 8)"), all_codes, default=st.session_state.watchlist[:6],
                           format_func=fmt, max_selections=8, key="cmp_codes", persist_state="session")
    period = st.segmented_control(t("区间", "Period"), analytics.PERIODS, default="1Y", key="cmp_period",
                                  persist_state="session", required=True)

if not picks:
    st.info(t("请至少选择一只股票。", "Pick at least one stock."))
    st.stop()

rf = st.session_state.rf
benches = sorted({BENCHMARKS[market(c)] for c in picks})
labels = {c: names.get(c, idx_names.get(c, c)) for c in picks + benches}
for b in benches:
    labels[b] = idx_names.get(b, b)

with st.spinner(t("正在载入日线历史…", "Loading daily history…")):
    panel = cache.panel(tuple(picks + benches))

rows = []
for c in picks:
    if c not in panel:
        continue
    sc = analytics.scorecard(panel[c].dropna(), panel[BENCHMARKS[market(c)]].dropna(), period, rf)
    rows.append({"code": c, "name": labels[c], "benchmark": labels[BENCHMARKS[market(c)]], **sc})
metrics = pd.DataFrame(rows)

per = pd.DataFrame([{"code": c, "name": labels[c], **analytics.period_returns(panel[c].dropna())}
                    for c in picks + benches if c in panel])

st.markdown(t("**各区间收益** · 总收益，分红再投资", "**Returns by period** · total return, dividends reinvested"))
pcols = analytics.PERIODS + ["CAGR (Max)"]
st.dataframe(style_signed(per, pcols), hide_index=True,
             column_config={"code": st.column_config.TextColumn(t("代码", "Code"), pinned=True),
                            "name": st.column_config.TextColumn(t("名称", "Name"), pinned=True),
                            **{p: pct_col(t("年化（全部）", p) if p == "CAGR (Max)" else p) for p in pcols}})

st.markdown(t(f"**风险与收益 · {period}** · 相对沪深300（A股）/ 恒生指数（港股）· 无风险利率 {rf:.2%}",
              f"**Risk and return · {period}** · vs CSI 300 (A-shares) / Hang Seng (HK) · risk-free {rf:.2%}"))
mcols = ["code", "name", "period_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "var_95",
         "cvar_95", "beta", "corr", "systematic_share", "alpha", "up_capture", "down_capture", "observations"]
st.dataframe(
    style_signed(metrics[[c for c in mcols if c in metrics]], ["period_return", "cagr", "alpha"]),
    hide_index=True,
    column_config={
        "code": st.column_config.TextColumn(t("代码", "Code"), pinned=True),
        "name": st.column_config.TextColumn(t("名称", "Name"), pinned=True),
        "period_return": pct_col(t("收益", "Return")),
        "cagr": pct_col(t("年化收益", "CAGR"), t("年化；区间 ≥ 1 年", "Annualised; periods ≥ 1 year")),
        "ann_vol": pct_col(t("波动率", "Volatility")), "sharpe": st.column_config.NumberColumn(t("夏普", "Sharpe"), format="%.2f"),
        "sortino": st.column_config.NumberColumn(t("索提诺", "Sortino"), format="%.2f"),
        "max_drawdown": pct_col(t("最大回撤", "Max DD")), "var_95": pct_col(t("VaR 95%（1天）", "VaR 95% (1d)")),
        "cvar_95": pct_col("CVaR 95%"),
        "beta": st.column_config.NumberColumn(t("贝塔", "Beta"), format="%.2f"),
        "corr": st.column_config.NumberColumn(t("相关系数", "Correlation"), format="%.2f"),
        "systematic_share": st.column_config.ProgressColumn(
            t("市场驱动", "Market-driven"), min_value=0, max_value=1, format="percent",
            help=t("R²：日收益波动中能被指数解释的比例，其余为公司自身因素。",
                   "R²: share of daily-return variance explained by the index. The rest is company-specific.")),
        "alpha": pct_col(t("阿尔法（年化）", "Alpha (ann.)")),
        "up_capture": st.column_config.NumberColumn(t("上行捕获", "Up capture"), format="%.2f"),
        "down_capture": st.column_config.NumberColumn(t("下行捕获", "Down capture"), format="%.2f"),
        "observations": st.column_config.NumberColumn(t("天数", "Days"), format="%d"),
    })

c1, c2 = st.columns([1.6, 1])
with c1:
    with st.container(border=True):
        st.markdown(t(f"**100 元的增长 · {period}**", f"**Growth of 100 · {period}**"))
        st.altair_chart(charts.multi_line(analytics.rebased(panel[picks + benches], period), labels, None,
                                          ",.0f", baseline=100))
with c2:
    with st.container(border=True):
        st.markdown(t("**风险与收益**", "**Risk vs return**"))
        if not metrics.empty:
            st.altair_chart(charts.risk_return(metrics, labels))

c3, c4 = st.columns(2)
corr = analytics.correlation_matrix(panel[picks + benches], period)
with c3:
    with st.container(border=True):
        st.markdown(t(f"**日收益相关系数 · {period}**", f"**Correlation of daily returns · {period}**"))
        st.altair_chart(charts.corr_heatmap(corr, labels))
yearly = pd.DataFrame({labels[c]: analytics.yearly_returns(panel[c].dropna()) for c in picks + benches
                       if c in panel}).sort_index(ascending=False).head(10)
with c4:
    with st.container(border=True):
        st.markdown(t("**自然年收益**（最近一年为年初至今）", "**Calendar-year returns** (latest year to date)"))
        st.altair_chart(charts.returns_heatmap(yearly))

with st.container(border=True):
    st.markdown(t(f"**回撤 · {period}**", f"**Drawdowns · {period}**"))
    w = analytics.rebased(panel[picks], period)
    st.altair_chart(charts.multi_line(w / w.cummax() - 1, labels, None, ".0%", height=240, baseline=0))

st.divider()
st.subheader(t("单个公司：受市场驱动还是公司自身因素？", "Single company: market-driven or company-specific?"), anchor=False)
one = st.selectbox(t("公司", "Company"), picks, format_func=fmt, key="cmp_one")
bcode = BENCHMARKS[market(one)]
w1 = analytics.window(panel[one].dropna(), period, strict=period != "Max")
r1 = analytics.daily_returns(w1)
rb = analytics.daily_returns(panel[bcode].dropna())
reg = analytics.regression(r1, rb)
with st.container(horizontal=True):
    st.metric(t("贝塔", "Beta"), f"{reg['beta']:.2f}", border=True,
              help=t("指数涨跌 1%，这只股票平均涨跌多少 %", "1% index move -> this many % for the stock, on average"))
    st.metric(t("市场驱动占比（R²）", "Market-driven share (R²)"), pct(reg["systematic_share"], signed=False), border=True)
    st.metric(t("公司自身占比", "Company-specific share"), pct(1 - reg["systematic_share"], signed=False), border=True)
    st.metric(t("市场风险（年化）", "Market risk (ann.)"), pct(reg["systematic_vol"], signed=False), border=True)
    st.metric(t("公司风险（年化）", "Company risk (ann.)"), pct(reg["specific_vol"], signed=False), border=True)
    st.metric(t("阿尔法（年化）", "Alpha (ann.)"), pct(reg["alpha"]), border=True)
share = reg["systematic_share"]
if share >= 0.5:
    verdict = t("主要**受市场驱动**——指数解释了它大部分的日内波动",
                "mostly **market-driven** - the index explains most of its daily moves")
elif share < 0.25:
    verdict = t("主要由**公司自身因素**决定——自身消息和资金解释了大部分的日内波动",
                "mostly **company-specific** - its own news and flows explain most of its daily moves")
else:
    verdict = t("市场与公司自身因素**兼而有之**", "a **mix** of market and company-specific moves")
st.markdown(t(f"在 {period} 内，{labels[one]} {verdict}（相对{labels[bcode]}的 R² 为 {share:.0%}）。",
              f"Over {period}, {labels[one]} was {verdict} (R² {share:.0%} vs {labels[bcode]})."))
c5, c6 = st.columns([1.4, 1])
with c5:
    with st.container(border=True):
        st.markdown(t(f"**相对{labels[bcode]}的滚动 60 日贝塔**", f"**Rolling 60-day beta vs {labels[bcode]}**"))
        rbeta = analytics.rolling_beta(analytics.daily_returns(panel[one].dropna()), rb).to_frame(one)
        start = analytics.period_start(rbeta.index[-1], period) if not rbeta.empty else None
        rbeta = rbeta if start is None else rbeta[rbeta.index >= start]
        rbeta.index.name = "date"
        st.altair_chart(charts.multi_line(rbeta, labels, t("贝塔", "Beta"), ".2f", height=240, baseline=1))
with c6:
    with st.container(border=True):
        st.markdown(t("**年度收益：公司与指数**", "**Yearly return: company vs index**"))
        yy = yearly[[labels[one], labels[bcode]]] if labels[one] in yearly else pd.DataFrame()
        if not yy.empty:
            yy = yy.assign(**{t("超额", "Excess"): yy[labels[one]] - yy[labels[bcode]]})
            st.altair_chart(charts.returns_heatmap(yy))

with st.container(horizontal=True):
    st.download_button(t("指标（CSV）", "Metrics (CSV)"), to_csv_bytes(metrics),
                       file_name=f"compare_metrics_{period}_{stamp()}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    st.download_button(t("复权收盘价，宽表（CSV）", "Adjusted closes, wide (CSV)"), to_csv_bytes(panel.reset_index()),
                       file_name=f"closes_wide_{stamp()}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    with st.popover(t("AI 提示词", "AI prompt"), icon=":material/content_copy:"):
        corr_l = corr.rename(index=labels, columns=labels)
        st.code(prompts.compare_review(metrics[[c for c in mcols if c in metrics]], period,
                                       " / ".join(labels[b] for b in benches), corr_l, yearly, lang()),
                language=None, wrap_lines=True, height=320)
