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

names = cache.name_map()
all_codes = list(dict.fromkeys(st.session_state.watchlist + list(names)))
fmt = lambda c: f"{c[2:]} · {names.get(c, INDEX_NAMES.get(c, c))}"  # noqa: E731

with st.expander(f"Watchlist ({len(st.session_state.watchlist)} symbols)", icon=":material/bookmark:"):
    wl = st.multiselect("Symbols", all_codes, default=st.session_state.watchlist, format_func=fmt,
                        key="wl_edit", help="Type a code or name. Saved to data/watchlist.json.")
    group = st.pills("Add a theme group", list(STARTER_GROUPS), key="wl_group")
    with st.container(horizontal=True):
        if st.button("Save watchlist", icon=":material/save:", type="primary"):
            st.session_state.watchlist = list(dict.fromkeys(wl + (STARTER_GROUPS[group] if group else [])))
            watchlist.save(st.session_state.watchlist)
            st.toast("Watchlist saved")
            st.rerun()
        st.caption("Pick a group and press Save to append it.")

with st.container(horizontal=True, vertical_alignment="bottom"):
    picks = st.multiselect("Compare (up to 8)", all_codes, default=st.session_state.watchlist[:6],
                           format_func=fmt, max_selections=8, key="cmp_codes", persist_state="session")
    period = st.segmented_control("Period", analytics.PERIODS, default="1Y", key="cmp_period",
                                  persist_state="session", required=True)

if not picks:
    st.info("Pick at least one stock.")
    st.stop()

rf = st.session_state.rf
benches = sorted({BENCHMARKS[market(c)] for c in picks})
labels = {c: names.get(c, INDEX_NAMES.get(c, c)) for c in picks + benches}
for b in benches:
    labels[b] = INDEX_NAMES.get(b, b)

with st.spinner("Loading daily history…"):
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

st.markdown("**Returns by period** · total return, dividends reinvested")
pcols = analytics.PERIODS + ["CAGR (Max)"]
st.dataframe(style_signed(per, pcols), hide_index=True,
             column_config={"code": st.column_config.TextColumn("Code", pinned=True),
                            "name": st.column_config.TextColumn("Name", pinned=True),
                            **{p: pct_col(p) for p in pcols}})

st.markdown(f"**Risk and return · {period}** · vs CSI 300 (A-shares) / Hang Seng (HK) · risk-free {rf:.2%}")
mcols = ["code", "name", "period_return", "cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "var_95",
         "cvar_95", "beta", "corr", "systematic_share", "alpha", "up_capture", "down_capture", "observations"]
st.dataframe(
    style_signed(metrics[[c for c in mcols if c in metrics]], ["period_return", "cagr", "alpha"]),
    hide_index=True,
    column_config={
        "code": st.column_config.TextColumn("Code", pinned=True),
        "name": st.column_config.TextColumn("Name", pinned=True),
        "period_return": pct_col("Return"), "cagr": pct_col("CAGR", "Annualised; periods ≥ 1 year"),
        "ann_vol": pct_col("Volatility"), "sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
        "sortino": st.column_config.NumberColumn("Sortino", format="%.2f"),
        "max_drawdown": pct_col("Max DD"), "var_95": pct_col("VaR 95% (1d)"), "cvar_95": pct_col("CVaR 95%"),
        "beta": st.column_config.NumberColumn("Beta", format="%.2f"),
        "corr": st.column_config.NumberColumn("Correlation", format="%.2f"),
        "systematic_share": st.column_config.ProgressColumn(
            "Market-driven", min_value=0, max_value=1, format="percent",
            help="R²: share of daily-return variance explained by the index. The rest is company-specific."),
        "alpha": pct_col("Alpha (ann.)"),
        "up_capture": st.column_config.NumberColumn("Up capture", format="%.2f"),
        "down_capture": st.column_config.NumberColumn("Down capture", format="%.2f"),
        "observations": st.column_config.NumberColumn("Days", format="%d"),
    })

c1, c2 = st.columns([1.6, 1])
with c1:
    with st.container(border=True):
        st.markdown(f"**Growth of 100 · {period}**")
        st.altair_chart(charts.multi_line(analytics.rebased(panel[picks + benches], period), labels, None,
                                          ",.0f", baseline=100))
with c2:
    with st.container(border=True):
        st.markdown("**Risk vs return**")
        if not metrics.empty:
            st.altair_chart(charts.risk_return(metrics, labels))

c3, c4 = st.columns(2)
corr = analytics.correlation_matrix(panel[picks + benches], period)
with c3:
    with st.container(border=True):
        st.markdown(f"**Correlation of daily returns · {period}**")
        st.altair_chart(charts.corr_heatmap(corr, labels))
yearly = pd.DataFrame({labels[c]: analytics.yearly_returns(panel[c].dropna()) for c in picks + benches
                       if c in panel}).sort_index(ascending=False).head(10)
with c4:
    with st.container(border=True):
        st.markdown("**Calendar-year returns** (latest year to date)")
        st.altair_chart(charts.returns_heatmap(yearly))

with st.container(border=True):
    st.markdown(f"**Drawdowns · {period}**")
    w = analytics.rebased(panel[picks], period)
    st.altair_chart(charts.multi_line(w / w.cummax() - 1, labels, None, ".0%", height=240, baseline=0))

st.divider()
st.subheader("Single company: market-driven or company-specific?", anchor=False)
one = st.selectbox("Company", picks, format_func=fmt, key="cmp_one")
bcode = BENCHMARKS[market(one)]
w1 = analytics.window(panel[one].dropna(), period, strict=period != "Max")
r1 = analytics.daily_returns(w1)
rb = analytics.daily_returns(panel[bcode].dropna())
reg = analytics.regression(r1, rb)
with st.container(horizontal=True):
    st.metric("Beta", f"{reg['beta']:.2f}", border=True, help="1% index move -> this many % for the stock, on average")
    st.metric("Market-driven share (R²)", pct(reg["systematic_share"], signed=False), border=True)
    st.metric("Company-specific share", pct(1 - reg["systematic_share"], signed=False), border=True)
    st.metric("Market risk (ann.)", pct(reg["systematic_vol"], signed=False), border=True)
    st.metric("Company risk (ann.)", pct(reg["specific_vol"], signed=False), border=True)
    st.metric("Alpha (ann.)", pct(reg["alpha"]), border=True)
share = reg["systematic_share"]
verdict = ("mostly **market-driven** - the index explains most of its daily moves" if share >= 0.5 else
           "mostly **company-specific** - its own news and flows explain most of its daily moves" if share < 0.25
           else "a **mix** of market and company-specific moves")
st.markdown(f"Over {period}, {labels[one]} was {verdict} (R² {share:.0%} vs {labels[bcode]}).")
c5, c6 = st.columns([1.4, 1])
with c5:
    with st.container(border=True):
        st.markdown(f"**Rolling 60-day beta vs {labels[bcode]}**")
        rbeta = analytics.rolling_beta(analytics.daily_returns(panel[one].dropna()), rb).to_frame(one)
        start = analytics.period_start(rbeta.index[-1], period) if not rbeta.empty else None
        rbeta = rbeta if start is None else rbeta[rbeta.index >= start]
        rbeta.index.name = "date"
        st.altair_chart(charts.multi_line(rbeta, labels, "Beta", ".2f", height=240, baseline=1))
with c6:
    with st.container(border=True):
        st.markdown("**Yearly return: company vs index**")
        yy = yearly[[labels[one], labels[bcode]]] if labels[one] in yearly else pd.DataFrame()
        if not yy.empty:
            yy = yy.assign(**{"Excess": yy[labels[one]] - yy[labels[bcode]]})
            st.altair_chart(charts.returns_heatmap(yy))

with st.container(horizontal=True):
    st.download_button("Metrics (CSV)", to_csv_bytes(metrics), file_name=f"compare_metrics_{period}_{stamp()}.csv",
                       mime="text/csv", on_click="ignore", icon=":material/download:")
    st.download_button("Adjusted closes, wide (CSV)", to_csv_bytes(panel.reset_index()),
                       file_name=f"closes_wide_{stamp()}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    with st.popover("AI prompt", icon=":material/content_copy:"):
        lang = st.segmented_control("Language", ["en", "zh"], default="en", key="cmp_lang", required=True,
                                    format_func=lambda x: {"en": "English", "zh": "中文"}[x])
        corr_l = corr.rename(index=labels, columns=labels)
        st.code(prompts.compare_review(metrics[[c for c in mcols if c in metrics]], period,
                                       " / ".join(labels[b] for b in benches), corr_l, yearly, lang),
                language=None, wrap_lines=True, height=320)
