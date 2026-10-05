"""Screener & query: rule-based whole-market screen (no AI) and SQL over the live snapshot."""

import pandas as pd
import streamlit as st

from stockrt import prompts, query, screener
from stockrt.export import stamp, to_csv_bytes
from stockrt.realtime import breadth
from ui import cache, charts
from ui.fmt import QUOTE_COLUMNS, money, pct, style_signed

PRESET_NAMES = list(screener.PRESETS)
KEYS = {  # widget key -> ScreenParams field
    "scr_exclude_st": "exclude_st", "scr_boards": "boards", "scr_pct_min": "pct_min", "scr_pct_max": "pct_max",
    "scr_vr_min": "volume_ratio_min", "scr_to_min": "turnover_min", "scr_to_max": "turnover_max",
    "scr_fc_min": "float_mcap_min_bn", "scr_fc_max": "float_mcap_max_bn", "scr_px_min": "price_min",
    "scr_px_max": "price_max", "scr_outperform": "outperform_market", "scr_cross": "golden_cross",
    "scr_cross_within": "cross_within", "scr_ma_rising": "ma_rising", "scr_vp_up": "volume_price_up",
    "scr_inflow": "main_inflow",
}


def apply_preset() -> None:
    p = screener.PRESETS[st.session_state.scr_preset]
    for key, attr in KEYS.items():
        v = getattr(p, attr)
        st.session_state[key] = list(v) if attr == "boards" else v


if "scr_exclude_st" not in st.session_state:
    st.session_state.scr_preset = PRESET_NAMES[0]
    apply_preset()

snap, snap_time = cache.snapshot("CN")
bench = cache.bench_quote("CN")
bench_pct = float(bench["pct_change"]) if bench else None

# Market mood (breadth) - real-time sentiment for the whole A-share market.
b = breadth(snap)
with st.container(horizontal=True):
    st.metric("Market mood", b.get("mood", "—"), f"score {b.get('mood_score', 0):+.0f}", delta_color="off",
              border=True, help="Rule-based breadth score: advance/decline ratio, median move, limit-up vs limit-down.")
    st.metric("Advancers / decliners", f"{b.get('advancers', 0):,} / {b.get('decliners', 0):,}",
              f"{b.get('advance_ratio', 0):.0%} advancing", delta_color="off", border=True)
    st.metric("Limit up / down", f"{b.get('limit_up', 0)} / {b.get('limit_down', 0)}", border=True)
    st.metric("Median change", pct(b.get("median_change"), already_pct=True), border=True)
    st.metric("CSI 300", pct(bench_pct, already_pct=True) if bench_pct is not None else "—", border=True)
    st.metric("Value traded", money(b.get("total_amount")), border=True)
st.caption(f"Snapshot of {len(snap):,} A-shares taken {snap_time} Beijing (refreshes every minute).")

tab_screen, tab_sql, tab_rules = st.tabs(["Screener", "SQL query", "How the rules work"])

with tab_screen:
    st.selectbox("Preset", PRESET_NAMES, key="scr_preset", on_change=apply_preset, width=380,
                 help="Choosing a preset fills the rules below; edit them freely before running.")
    with st.form("screen_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Today's move**")
            a, b_ = st.columns(2)
            a.number_input("Change % from", key="scr_pct_min", value=None, step=0.5, persist_state="session")
            b_.number_input("to", key="scr_pct_max", value=None, step=0.5, persist_state="session")
            st.number_input("Volume ratio above", key="scr_vr_min", value=None, step=0.1, persist_state="session")
            st.toggle("Beat the market (CSI 300)", key="scr_outperform", persist_state="session")
        with c2:
            st.markdown("**Activity & size**")
            a, b_ = st.columns(2)
            a.number_input("Turnover % from", key="scr_to_min", value=None, step=0.5, persist_state="session")
            b_.number_input("to", key="scr_to_max", value=None, step=0.5, persist_state="session")
            a, b_ = st.columns(2)
            a.number_input("Float cap ¥bn from", key="scr_fc_min", value=None, step=1.0, persist_state="session")
            b_.number_input("to", key="scr_fc_max", value=None, step=1.0, persist_state="session")
            a, b_ = st.columns(2)
            a.number_input("Price from", key="scr_px_min", value=None, step=1.0, persist_state="session")
            b_.number_input("to", key="scr_px_max", value=None, step=1.0, persist_state="session")
        with c3:
            st.markdown("**Trend & money**")
            st.toggle("MA5 crosses above MA10", key="scr_cross", persist_state="session")
            st.number_input("…within the last N sessions (1 = today)", key="scr_cross_within", min_value=1,
                            max_value=10, step=1, persist_state="session")
            st.toggle("MA5 and MA10 both rising", key="scr_ma_rising", persist_state="session")
            st.toggle("Volume and price rising together", key="scr_vp_up", persist_state="session")
            st.toggle("Main funds net inflow", key="scr_inflow", persist_state="session")
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.toggle("Exclude ST / *ST", key="scr_exclude_st", persist_state="session")
            st.pills("Boards", list(screener.BOARDS), selection_mode="multi", key="scr_boards",
                     persist_state="session")
        submitted = st.form_submit_button("Run screen", type="primary", icon=":material/filter_alt:")

    if submitted:
        params = screener.ScreenParams.from_dict({attr: st.session_state[k] for k, attr in KEYS.items()})
        with st.spinner("Screening the whole market: snapshot rules, then daily history, then money flow…"):
            res = screener.run(snap, params, bench_pct)
        st.session_state.scr_result = {"res": res, "params": params, "as_of": snap_time}

    stored = st.session_state.get("scr_result")
    if not stored:
        st.info("Set the rules (the default preset is your late-session momentum screen) and press **Run screen**.")
    else:
        res, params, as_of = stored["res"], stored["params"], stored["as_of"]
        rules = screener.describe(params)
        for n in res.notes:
            st.warning(n, icon=":material/warning:")
        left, right = st.columns([1, 1.6])
        with left:
            with st.container(border=True):
                st.markdown(f"**Funnel** · snapshot {as_of}")
                st.altair_chart(charts.funnel(res.funnel))
        with right:
            with st.container(border=True):
                st.markdown(f"**{len(res.passed)} stocks pass every rule**")
                cols = ["code", "name", "price", "pct_change", "volume_ratio", "turnover_rate", "float_mcap",
                        "amount", "cross_days_ago", "ma5", "ma10", "main_net", "main_net_ratio", "board", "pe_ttm"]
                cfg = dict(QUOTE_COLUMNS)
                cfg.update({
                    "cross_days_ago": st.column_config.NumberColumn("Cross (days ago)", format="%d"),
                    "ma5": st.column_config.NumberColumn("MA5", format="%.2f"),
                    "ma10": st.column_config.NumberColumn("MA10", format="%.2f"),
                    "missed_rule": st.column_config.TextColumn("Missed rule"),
                })
                passed = res.passed[[c for c in cols if c in res.passed.columns]]
                if passed.empty:
                    st.caption("Nothing passes every rule right now - see the near misses below.")
                else:
                    ev = st.dataframe(style_signed(passed, ["pct_change", "main_net"]), hide_index=True,
                                      column_config=cfg, on_select="rerun", selection_mode="single-row",
                                      key="scr_pick")
                    sel = ev.selection.rows if ev else []
                    if sel:
                        code = passed.iloc[sel[0]]["code"]
                        with st.container(horizontal=True):
                            if st.button(f"Open {code} on Live dashboard", icon=":material/monitoring:"):
                                st.session_state.live_code = code
                                st.switch_page("app_pages/live.py")
                            if st.button("Add to watchlist", icon=":material/playlist_add:"):
                                from stockrt import watchlist
                                if code not in st.session_state.watchlist:
                                    st.session_state.watchlist.append(code)
                                    watchlist.save(st.session_state.watchlist)
                                st.toast(f"{code} added to the watchlist")
        with st.container(border=True):
            st.markdown(f"**Near misses** · {len(res.near_misses)} stocks failed exactly one trend / money rule")
            near = res.near_misses[[c for c in cols + ["missed_rule"] if c in res.near_misses.columns]]
            st.dataframe(style_signed(near, ["pct_change", "main_net"]), hide_index=True, column_config=cfg)

        with st.container(horizontal=True):
            st.download_button("Results (CSV)", to_csv_bytes(res.passed), file_name=f"screen_pass_{stamp()}.csv",
                               mime="text/csv", on_click="ignore", icon=":material/download:", type="primary")
            st.download_button("All evaluated + rule columns (CSV)", to_csv_bytes(res.candidates),
                               file_name=f"screen_candidates_{stamp()}.csv", mime="text/csv", on_click="ignore",
                               icon=":material/download:")
            st.download_button("Full market snapshot (CSV)", to_csv_bytes(snap), file_name=f"cn_snapshot_{stamp()}.csv",
                               mime="text/csv", on_click="ignore", icon=":material/download:")
            with st.popover("AI prompt", icon=":material/content_copy:"):
                kind = st.segmented_control("Prompt", ["Review these results", "Run this screen yourself"],
                                            default="Review these results", key="scr_prompt_kind", required=True)
                lang = st.segmented_control("Language", ["en", "zh"], default="en", key="scr_prompt_lang",
                                            required=True, format_func=lambda x: {"en": "English", "zh": "中文"}[x])
                if kind == "Review these results":
                    text = prompts.screen_review(rules, res.funnel, res.passed, res.near_misses, as_of, lang)
                else:
                    text = prompts.screen_spec(lang)
                    st.caption("Attach the market snapshot CSV and a daily-bars CSV (Data downloads page).")
                st.code(text, language=None, wrap_lines=True, height=360)

with tab_sql:
    st.markdown("Query the live data with SQL (SQLite dialect). Tables: **snapshot** - every A-share quote right "
                "now; **screen** - the stocks evaluated by your last screen, with rule columns.")
    ex = st.selectbox("Start from an example", list(query.EXAMPLES), key="sql_example")
    if st.session_state.get("sql_last_example") != ex:
        st.session_state.sql_text = query.EXAMPLES[ex]
        st.session_state.sql_last_example = ex
    sql = st.text_area("SQL", key="sql_text", height=200)
    tables = {"snapshot": snap}
    if st.session_state.get("scr_result"):
        tables["screen"] = st.session_state.scr_result["res"].candidates
    if st.button("Run query", type="primary", icon=":material/play_arrow:"):
        try:
            st.session_state.sql_out = query.run(sql, tables)
            st.session_state.sql_err = None
        except Exception as e:  # noqa: BLE001 - show any SQL error to the user
            st.session_state.sql_out, st.session_state.sql_err = None, str(e)
    if st.session_state.get("sql_err"):
        st.error(st.session_state.sql_err)
    out = st.session_state.get("sql_out")
    if isinstance(out, pd.DataFrame):
        st.caption(f"{len(out):,} rows")
        st.dataframe(out, hide_index=True, column_config=QUOTE_COLUMNS)
        st.download_button("Query result (CSV)", to_csv_bytes(out), file_name=f"query_{stamp()}.csv",
                           mime="text/csv", on_click="ignore", icon=":material/download:")
    with st.expander("Columns"):
        st.json(query.schema(tables), expanded=False)

with tab_rules:
    st.markdown("""
**Pipeline.** One real-time sweep of every A-share (Tencent, ~5,500 quotes in a few seconds) applies the
snapshot rules. Only the survivors then fetch ~40 daily bars (for moving averages), and only stocks still
alive (or one rule short) fetch money flow. The funnel shows how many stocks each rule removes.

| Rule | Definition |
|---|---|
| Not ST | name does not contain "ST" (special treatment for financial distress) |
| Change % | today's price vs the previous close |
| Volume ratio (量比) | volume per minute so far today ÷ average volume per minute over the previous 5 sessions |
| Turnover (换手率) | today's volume ÷ float shares |
| Float cap (流通市值) | price × circulating shares, CNY billions |
| Beat the market | today's change % above the CSI 300's |
| MA5 crosses above MA10 | MA5 > MA10 now, and MA5 was ≤ MA10 within the last N sessions (today's live price included) |
| MAs rising | today's MA5 and MA10 both above yesterday's |
| Volume & price up | price up today and today's volume - scaled to a full session while trading - above yesterday's |
| Main funds inflow | large + extra-large orders bought minus sold > 0 (East Money; Sina when blocked) |

Moving averages use forward-adjusted daily closes; within a 40-day window the additive and ratio
methods agree to within rounding. Near misses are stocks that pass the snapshot rules but fail exactly
one trend / money rule - useful when nothing passes everything.
""")
