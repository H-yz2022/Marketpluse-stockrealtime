"""Screener & query: rule-based whole-market screen (no AI) and SQL over the live snapshot."""

import pandas as pd
import streamlit as st

from stockrt import prompts, query, screener
from stockrt.export import stamp, to_csv_bytes
from stockrt.realtime import breadth
from ui import cache, charts
from ui.fmt import money, pct, quote_columns, style_signed
from ui.i18n import BOARDS_ZH, funnel_step, lang, mood, note, t

PRESET_NAMES = list(screener.PRESETS)
PRESETS_ZH = {"Late-session momentum (尾盘选股)": "尾盘选股", "Strong large caps": "强势大盘股",
              "Volume breakout": "放量突破", "Pullback in uptrend": "上升趋势中的回调"}
EXAMPLES_ZH = {"Late-session momentum (snapshot rules only)": "尾盘选股（只用快照条件）",
               "Biggest movers by board": "各板块涨跌幅最大", "Strongest active buying (外盘 > 内盘)": "主动买入最强（外盘 > 内盘）",
               "Near 52-week high with volume": "放量接近52周新高"}
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
    st.metric(t("市场情绪", "Market mood"), mood(b.get("mood", "—")), t(f"得分 {b.get('mood_score', 0):+.0f}",
                                                                      f"score {b.get('mood_score', 0):+.0f}"),
              delta_color="off", border=True,
              help=t("基于涨跌家数比、涨跌幅中位数、涨停与跌停数量的规则评分。",
                     "Rule-based breadth score: advance/decline ratio, median move, limit-up vs limit-down."))
    st.metric(t("上涨 / 下跌家数", "Advancers / decliners"), f"{b.get('advancers', 0):,} / {b.get('decliners', 0):,}",
              t(f"{b.get('advance_ratio', 0):.0%} 上涨", f"{b.get('advance_ratio', 0):.0%} advancing"),
              delta_color="off", border=True)
    st.metric(t("涨停 / 跌停", "Limit up / down"), f"{b.get('limit_up', 0)} / {b.get('limit_down', 0)}", border=True)
    st.metric(t("涨跌幅中位数", "Median change"), pct(b.get("median_change"), already_pct=True), border=True)
    st.metric(t("沪深300", "CSI 300"), pct(bench_pct, already_pct=True) if bench_pct is not None else "—", border=True)
    st.metric(t("成交额", "Value traded"), money(b.get("total_amount")), border=True)
st.caption(t(f"北京时间 {snap_time} 的 {len(snap):,} 只A股快照（每分钟刷新）。",
             f"Snapshot of {len(snap):,} A-shares taken {snap_time} Beijing (refreshes every minute)."))

tab_screen, tab_sql, tab_rules = st.tabs([t("选股器", "Screener"), t("SQL 查询", "SQL query"),
                                          t("条件说明", "How the rules work")])

with tab_screen:
    st.selectbox(t("预设", "Preset"), PRESET_NAMES, key="scr_preset", on_change=apply_preset, width=380,
                 format_func=lambda n: t(PRESETS_ZH.get(n, n), n),
                 help=t("选择预设会填入下面的条件，运行前可以随意修改。",
                        "Choosing a preset fills the rules below; edit them freely before running."))
    with st.form("screen_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"**{t('今日走势', 'Today’s move')}**")
            a, b_ = st.columns(2)
            a.number_input(t("涨幅 % 从", "Change % from"), key="scr_pct_min", value=None, step=0.5,
                           persist_state="session")
            b_.number_input(t("到", "to"), key="scr_pct_max", value=None, step=0.5, persist_state="session")
            st.number_input(t("量比大于", "Volume ratio above"), key="scr_vr_min", value=None, step=0.1,
                            persist_state="session")
            st.toggle(t("强于大盘（沪深300）", "Beat the market (CSI 300)"), key="scr_outperform", persist_state="session")
        with c2:
            st.markdown(f"**{t('活跃度与规模', 'Activity & size')}**")
            a, b_ = st.columns(2)
            a.number_input(t("换手率 % 从", "Turnover % from"), key="scr_to_min", value=None, step=0.5,
                           persist_state="session")
            b_.number_input(t("到", "to"), key="scr_to_max", value=None, step=0.5, persist_state="session")
            a, b_ = st.columns(2)
            a.number_input(t("流通市值（十亿元）从", "Float cap ¥bn from"), key="scr_fc_min", value=None, step=1.0,
                           persist_state="session")
            b_.number_input(t("到", "to"), key="scr_fc_max", value=None, step=1.0, persist_state="session")
            a, b_ = st.columns(2)
            a.number_input(t("价格从", "Price from"), key="scr_px_min", value=None, step=1.0, persist_state="session")
            b_.number_input(t("到", "to"), key="scr_px_max", value=None, step=1.0, persist_state="session")
        with c3:
            st.markdown(f"**{t('趋势与资金', 'Trend & money')}**")
            st.toggle(t("5日线上穿10日线", "MA5 crosses above MA10"), key="scr_cross", persist_state="session")
            st.number_input(t("……在最近 N 个交易日内（1 = 今天）", "…within the last N sessions (1 = today)"),
                            key="scr_cross_within", min_value=1, max_value=10, step=1, persist_state="session")
            st.toggle(t("5日线与10日线同时向上", "MA5 and MA10 both rising"), key="scr_ma_rising", persist_state="session")
            st.toggle(t("量价齐升", "Volume and price rising together"), key="scr_vp_up", persist_state="session")
            st.toggle(t("主力资金净流入", "Main funds net inflow"), key="scr_inflow", persist_state="session")
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.toggle(t("排除 ST / *ST", "Exclude ST / *ST"), key="scr_exclude_st", persist_state="session")
            st.pills(t("板块", "Boards"), list(screener.BOARDS), selection_mode="multi", key="scr_boards",
                     persist_state="session", format_func=lambda x: t(BOARDS_ZH.get(x, x), x))
        submitted = st.form_submit_button(t("运行选股", "Run screen"), type="primary", icon=":material/filter_alt:")

    if submitted:
        params = screener.ScreenParams.from_dict({attr: st.session_state[k] for k, attr in KEYS.items()})
        with st.spinner(t("正在筛选全市场：先快照条件，再日线历史，最后资金流向…",
                          "Screening the whole market: snapshot rules, then daily history, then money flow…")):
            res = screener.run(snap, params, bench_pct)
        st.session_state.scr_result = {"res": res, "params": params, "as_of": snap_time}

    stored = st.session_state.get("scr_result")
    if not stored:
        st.info(t("设置条件（默认预设就是尾盘选股）后按 **运行选股**。",
                  "Set the rules (the default preset is your late-session momentum screen) and press **Run screen**."))
    else:
        res, params, as_of = stored["res"], stored["params"], stored["as_of"]
        rules = screener.describe(params)
        for n in res.notes:
            st.warning(note(n), icon=":material/warning:")
        left, right = st.columns([1, 1.6])
        with left:
            with st.container(border=True):
                st.markdown(t(f"**筛选漏斗** · 快照 {as_of}", f"**Funnel** · snapshot {as_of}"))
                st.altair_chart(charts.funnel([(funnel_step(n), k) for n, k in res.funnel]))
        with right:
            with st.container(border=True):
                st.markdown(t(f"**{len(res.passed)} 只股票满足全部条件**", f"**{len(res.passed)} stocks pass every rule**"))
                cols = ["code", "name", "price", "pct_change", "volume_ratio", "turnover_rate", "float_mcap",
                        "amount", "cross_days_ago", "ma5", "ma10", "main_net", "main_net_ratio", "board", "pe_ttm"]
                cfg = quote_columns()
                cfg.update({
                    "cross_days_ago": st.column_config.NumberColumn(t("金叉（几天前）", "Cross (days ago)"), format="%d"),
                    "ma5": st.column_config.NumberColumn("MA5", format="%.2f"),
                    "ma10": st.column_config.NumberColumn("MA10", format="%.2f"),
                    "missed_rule": st.column_config.TextColumn(t("未满足的条件", "Missed rule")),
                })
                passed = res.passed[[c for c in cols if c in res.passed.columns]]
                if passed.empty:
                    st.caption(t("目前没有股票满足全部条件——见下方差一条的股票。",
                                 "Nothing passes every rule right now - see the near misses below."))
                else:
                    ev = st.dataframe(style_signed(passed, ["pct_change", "main_net"]), hide_index=True,
                                      column_config=cfg, on_select="rerun", selection_mode="single-row",
                                      key="scr_pick")
                    sel = ev.selection.rows if ev else []
                    if sel:
                        code = passed.iloc[sel[0]]["code"]
                        with st.container(horizontal=True):
                            if st.button(t(f"在实时看板打开 {code}", f"Open {code} on Live dashboard"),
                                         icon=":material/monitoring:"):
                                st.session_state.live_code = code
                                st.switch_page("app_pages/live.py")
                            if st.button(t("加入自选股", "Add to watchlist"), icon=":material/playlist_add:"):
                                from stockrt import watchlist
                                if code not in st.session_state.watchlist:
                                    st.session_state.watchlist.append(code)
                                    watchlist.save(st.session_state.watchlist)
                                st.toast(t(f"{code} 已加入自选股", f"{code} added to the watchlist"))
        with st.container(border=True):
            st.markdown(t(f"**差一条** · {len(res.near_misses)} 只股票恰好只差一个趋势 / 资金条件",
                          f"**Near misses** · {len(res.near_misses)} stocks failed exactly one trend / money rule"))
            near = res.near_misses[[c for c in cols + ["missed_rule"] if c in res.near_misses.columns]].copy()
            if "missed_rule" in near:
                near["missed_rule"] = near["missed_rule"].map(funnel_step)
            st.dataframe(style_signed(near, ["pct_change", "main_net"]), hide_index=True, column_config=cfg)

        with st.container(horizontal=True):
            st.download_button(t("结果（CSV）", "Results (CSV)"), to_csv_bytes(res.passed),
                               file_name=f"screen_pass_{stamp()}.csv", mime="text/csv", on_click="ignore",
                               icon=":material/download:", type="primary")
            st.download_button(t("全部候选股及条件列（CSV）", "All evaluated + rule columns (CSV)"),
                               to_csv_bytes(res.candidates), file_name=f"screen_candidates_{stamp()}.csv",
                               mime="text/csv", on_click="ignore", icon=":material/download:")
            st.download_button(t("全市场快照（CSV）", "Full market snapshot (CSV)"), to_csv_bytes(snap),
                               file_name=f"cn_snapshot_{stamp()}.csv", mime="text/csv", on_click="ignore",
                               icon=":material/download:")
            with st.popover(t("AI 提示词", "AI prompt"), icon=":material/content_copy:"):
                kind = st.segmented_control(t("提示词", "Prompt"), ["review", "spec"], default="review",
                                            key="scr_prompt_kind_v2", required=True,
                                            format_func=lambda k: t("点评这些结果", "Review these results") if k == "review"
                                            else t("让 AI 自己运行这个选股", "Run this screen yourself"))
                if kind == "review":
                    text = prompts.screen_review(rules, res.funnel, res.passed, res.near_misses, as_of, lang())
                else:
                    text = prompts.screen_spec(lang())
                    st.caption(t("附上全市场快照 CSV 和日线 CSV（“数据下载”页）。",
                                 "Attach the market snapshot CSV and a daily-bars CSV (Data downloads page)."))
                st.code(text, language=None, wrap_lines=True, height=360)

with tab_sql:
    st.markdown(t("用 SQL（SQLite 语法）查询实时数据。数据表：**snapshot**——此刻每只A股的行情；**screen**——上一次选股评估过的股票，含条件列。",
                  "Query the live data with SQL (SQLite dialect). Tables: **snapshot** - every A-share quote right "
                  "now; **screen** - the stocks evaluated by your last screen, with rule columns."))
    ex = st.selectbox(t("从示例开始", "Start from an example"), list(query.EXAMPLES), key="sql_example",
                      format_func=lambda n: t(EXAMPLES_ZH.get(n, n), n))
    if st.session_state.get("sql_last_example") != ex:
        st.session_state.sql_text = query.EXAMPLES[ex]
        st.session_state.sql_last_example = ex
    sql = st.text_area("SQL", key="sql_text", height=200)
    tables = {"snapshot": snap}
    if st.session_state.get("scr_result"):
        tables["screen"] = st.session_state.scr_result["res"].candidates
    if st.button(t("运行查询", "Run query"), type="primary", icon=":material/play_arrow:"):
        try:
            st.session_state.sql_out = query.run(sql, tables)
            st.session_state.sql_err = None
        except Exception as e:  # noqa: BLE001 - show any SQL error to the user
            st.session_state.sql_out, st.session_state.sql_err = None, str(e)
    if st.session_state.get("sql_err"):
        st.error(st.session_state.sql_err)
    out = st.session_state.get("sql_out")
    if isinstance(out, pd.DataFrame):
        st.caption(t(f"{len(out):,} 行", f"{len(out):,} rows"))
        st.dataframe(out, hide_index=True, column_config=quote_columns())
        st.download_button(t("查询结果（CSV）", "Query result (CSV)"), to_csv_bytes(out), file_name=f"query_{stamp()}.csv",
                           mime="text/csv", on_click="ignore", icon=":material/download:")
    with st.expander(t("字段", "Columns")):
        st.json(query.schema(tables), expanded=False)

with tab_rules:
    st.markdown(t("""
**流程。** 一次实时扫描全部A股（腾讯，约 5,500 条行情，几秒钟）应用快照条件。只有通过的股票才读取约 40 根日线（用于均线），
只有仍然符合（或只差一条）的股票才读取资金流向。漏斗图显示每个条件筛掉了多少股票。

| 条件 | 定义 |
|---|---|
| 非ST | 名称不含“ST”（因财务困难被特别处理） |
| 涨幅 | 今日价格相对昨收 |
| 量比 | 今日至今每分钟成交量 ÷ 前5个交易日每分钟平均成交量 |
| 换手率 | 今日成交量 ÷ 流通股本 |
| 流通市值 | 价格 × 流通股本，单位十亿元 |
| 强于大盘 | 今日涨幅高于沪深300 |
| 5日线上穿10日线 | 现在 MA5 > MA10，且最近 N 个交易日内 MA5 ≤ MA10（含今日实时价格） |
| 均线向上 | 今日 MA5 和 MA10 都高于昨日 |
| 量价齐升 | 今日上涨，且今日成交量（交易时间内按全天折算）高于昨日 |
| 主力资金流入 | 大单 + 特大单买入减卖出 > 0（东方财富；被屏蔽时用新浪） |

均线使用前复权日收盘价；40 日窗口内加法复权与比例复权的结果在舍入误差以内一致。差一条的股票指通过快照条件、但恰好只有一个趋势 / 资金条件
未满足的股票——在没有股票满足全部条件时很有用。
""", """
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
"""))
