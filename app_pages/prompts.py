"""AI prompt builder: paste-ready prompts filled with live data. No AI is called from the app."""

import pandas as pd
import streamlit as st

from stockrt import analytics, history, intraday, prompts, screener
from stockrt.config import BENCHMARKS, INDEX_NAMES
from stockrt.export import stamp, to_csv_bytes
from stockrt.realtime import breadth
from stockrt.symbols import market
from ui import cache
from ui.i18n import index_name, lang, t

names = {**cache.name_map(), **{c: index_name(n) for c, n in INDEX_NAMES.items()}}
fmt = lambda c: f"{c[2:]} · {names.get(c, c)}"  # noqa: E731

st.markdown(t("生成提示词，用框右上角的按钮复制，再粘贴到任何 AI 助手。提示词包含数据本身（CSV）和准确的字段定义，因此回答基于本程序看到的数据。"
              "这里不会向任何地方发送内容。",
              "Build a prompt, copy it with the button in the top-right corner of the box, and paste it into any AI "
              "assistant. Prompts carry the data itself (as CSV) plus exact column definitions, so the answer is "
              "grounded in what this app sees. Nothing is sent anywhere from here."))

tab_build, tab_lib = st.tabs([t("用实时数据生成", "Build from live data"), t("提示词库", "Prompt library")])

TEMPLATES = {
    "review": (("点评最近一次选股结果", "给上次选股找到的股票排序并提示风险。"),
               ("Review my latest screen results", "Ranks the stocks your last screen found and flags risks.")),
    "spec": (("让 AI 自己运行我的选股（附 CSV）", "用通俗语言写出 10 条尾盘选股规则，供能读取附件的 AI 使用。"),
             ("Run my screen yourself (attach CSVs)", "Your 10-rule late-session screen in plain words, for an AI "
                                                     "that can read attached files.")),
    "intraday": (("截至某个时间点的盘中点评", "上午与下午、均价、成交量——针对自选股，例如 14:00。"),
                 ("Intraday review at a cut-off time", "Morning vs afternoon, VWAP, volume - for your watchlist, at "
                                                       "14:00 etc.")),
    "compare": (("比较风险与收益", "多只股票的指标、相关系数和年度收益。"),
                ("Compare risk and return", "Multi-stock metrics, correlation and yearly returns.")),
    "brief": (("单只股票简报", "一只股票的关键指标、今日走势、近 30 天和公告标题。"),
              ("Single-stock brief", "KPIs, today's session, last 30 days and announcement titles for one stock.")),
    "mood": (("市场情绪总结", "涨跌家数、涨停 / 跌停和此刻成交最活跃的股票。"),
             ("Market mood summary", "Breadth, limit-up / limit-down and the most-traded stocks right now.")),
    "custom": (("就某个数据集提任何问题", "你自己的问题加上程序里的一个数据集。"),
               ("Ask anything about a dataset", "Your own question plus a dataset from the app.")),
}


def tpl(k: str, i: int) -> str:
    zh_pair, en_pair = TEMPLATES[k]
    return t(zh_pair[i], en_pair[i])


with tab_build:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        kind = st.selectbox(t("模板", "Template"), list(TEMPLATES), key="pb_kind_v2", persist_state="session",
                            width=360, format_func=lambda k: tpl(k, 0))
        plang = st.segmented_control(t("提示词语言", "Prompt language"), ["zh", "en"], default=lang(),
                                     key="pb_lang_v2", required=True,
                                     format_func=lambda x: {"en": "English", "zh": "中文"}[x])
    st.caption(tpl(kind, 1))

    text, attach = None, {}
    if kind == "review":
        res = st.session_state.get("scr_result")
        if not res:
            st.info(t("本次还没有运行过选股。现在运行默认的尾盘选股，或打开“选股器”页面修改条件。",
                      "No screen has been run in this session. Run the default late-session screen now, or open the "
                      "Screener page to edit the rules."))
            if st.button(t("运行默认选股", "Run the default screen"), icon=":material/filter_alt:"):
                snap, ts = cache.snapshot("CN")
                b = cache.bench_quote("CN")
                with st.spinner(t("正在筛选全市场…", "Screening the whole market…")):
                    r = screener.run(snap, screener.ScreenParams(), float(b["pct_change"]) if b else None)
                st.session_state.scr_result = {"res": r, "params": screener.ScreenParams(), "as_of": ts}
                st.rerun()
        else:
            r = res["res"]
            text = prompts.screen_review(screener.describe(res["params"]), r.funnel, r.passed, r.near_misses,
                                         res["as_of"], plang)
            attach["screen_candidates.csv"] = r.candidates
    elif kind == "spec":
        text = prompts.screen_spec(plang)
        snap, ts = cache.snapshot("CN")
        attach["cn_market_snapshot.csv"] = snap
        st.caption(t("附上下面的全市场快照。均线条件还需要附上日线数据集（数据下载 → 日线数据集 → 全部A股，1M）。",
                     "Attach the market snapshot below. For the moving-average rules, also attach a daily dataset "
                     "(Data downloads → Daily datasets → All A-shares, 1M)."))
    elif kind == "intraday":
        codes = st.multiselect(t("股票", "Symbols"), st.session_state.watchlist + [c for c in names if c not in
                               st.session_state.watchlist], default=st.session_state.watchlist[:12],
                               format_func=fmt, key="pb_intra_codes", max_selections=24)
        cut = st.segmented_control(t("截至", "Up to"), ["11:30", "13:30", "14:00", "14:30", "full"], default="14:00",
                                   key="pb_cut_v2", required=True,
                                   format_func=lambda c: t("全天", "Full day") if c == "full" else c)
        if codes:
            table, minutes = cache.intraday_table(tuple(codes), None if cut == "full" else cut)
            if not table.empty:
                dates = ", ".join(sorted({str(d) for d in table["trade_date"]}))
                text = prompts.intraday_review(table, f"{'Full day' if cut == 'full' else cut} on {dates}", plang)
                attach["intraday_1min.csv"] = minutes
    elif kind == "compare":
        codes = st.multiselect(t("股票（最多 8 只）", "Symbols (up to 8)"), st.session_state.watchlist +
                               [c for c in names if c not in st.session_state.watchlist],
                               default=st.session_state.watchlist[:6], format_func=fmt, key="pb_cmp_codes",
                               max_selections=8)
        period = st.segmented_control(t("区间", "Period"), analytics.PERIODS, default="1Y", key="pb_period",
                                      required=True)
        if codes:
            benches = sorted({BENCHMARKS[market(c)] for c in codes})
            panel = cache.panel(tuple(codes + benches))
            labels = {c: names.get(c, c) for c in codes + benches}
            rows = [{"code": c, "name": labels[c], **analytics.scorecard(panel[c].dropna(),
                     panel[BENCHMARKS[market(c)]].dropna(), period, st.session_state.rf)} for c in codes if c in panel]
            m = pd.DataFrame(rows).drop(columns=["start"], errors="ignore")
            corr = analytics.correlation_matrix(panel[codes], period).rename(index=labels, columns=labels)
            yearly = pd.DataFrame({labels[c]: analytics.yearly_returns(panel[c].dropna()) for c in codes + benches
                                   if c in panel}).sort_index(ascending=False).head(10)
            text = prompts.compare_review(m, period, " / ".join(labels[b] for b in benches), corr, yearly, plang)
            attach["closes_wide.csv"] = panel.reset_index()
    elif kind == "brief":
        code = st.selectbox(t("股票", "Stock"), st.session_state.watchlist + [c for c in names if c not in
                            st.session_state.watchlist], format_func=fmt, key="pb_one")
        q = cache.quotes((code,)).iloc[0].to_dict()
        daily, _ = cache.daily(code)
        daily = history.append_live(daily, q)
        close = daily.set_index("date")["close"]
        day, pc, bars = cache.minute_today(code)
        s = intraday.stats(bars, pc, market(code)) if not bars.empty else {}
        anns = cache.announcements(code)
        kpis = {"price": q["price"], "change_pct_today": q["pct_change"],
                **{f"{p}_return": round(analytics.total_return(analytics.window(close, p)), 4)
                   for p in ("1M", "3M", "1Y")},
                "volatility_1Y": round(analytics.ann_vol(analytics.daily_returns(analytics.window(close, "1Y"))), 4),
                "max_drawdown_1Y": round(analytics.max_drawdown(analytics.window(close, "1Y")), 4),
                "pe_ttm": q.get("pe_ttm"), "pb": q.get("pb"), "div_yield_pct": q.get("div_yield"),
                "turnover_rate_pct": q.get("turnover_rate"), "volume_ratio": q.get("volume_ratio")}
        text = prompts.stock_brief(q["name"], code, kpis, {k: v for k, v in s.items() if k != "as_of"},
                                   daily.tail(30), anns["title"].tolist() if not anns.empty else [], plang)
        attach[f"{code}_daily.csv"] = daily
        attach[f"{code}_1min.csv"] = bars
    elif kind == "mood":
        snap, ts = cache.snapshot("CN")
        b = breadth(snap)
        movers = snap.sort_values("amount", ascending=False).head(40)
        text = prompts.market_summary(b, movers, ts, plang)
        attach["cn_market_snapshot.csv"] = snap
    else:
        question = st.text_area(t("你的问题", "Your question"),
                                t("这些股票中哪些尾盘看起来最强，为什么？",
                                  "Which of these stocks look strongest into the close, and why?"),
                                key="pb_question", height=90)
        datasets = {"watchlist": t("自选股行情", "Watchlist quotes"),
                    "screen": t("上次选股的候选股", "Last screen candidates"),
                    "snapshot": t("A股快照（按成交额排序）", "A-share snapshot (top by value)"),
                    "etfs": t("指数ETF评分表", "Index ETF scorecard")}
        ds = st.segmented_control(t("数据集", "Dataset"), list(datasets), default="watchlist", key="pb_ds_v2",
                                  required=True, format_func=datasets.get)
        n = st.slider(t("包含的行数", "Rows to include"), 10, 200, 60, key="pb_rows")
        if ds == "watchlist":
            data = cache.quotes(tuple(st.session_state.watchlist))
        elif ds == "screen":
            res = st.session_state.get("scr_result")
            data = res["res"].candidates if res else pd.DataFrame()
            if data.empty:
                st.info(t("请先运行一次选股。", "Run a screen first."))
        elif ds == "snapshot":
            data = cache.snapshot("CN")[0].sort_values("amount", ascending=False)
        else:
            data = cache.etf_scorecard("1Y", st.session_state.rf)
        if not data.empty:
            text = prompts.custom(question, data, datasets[ds], plang, n)
            attach["dataset.csv"] = data

    if text:
        n_chars = len(text)
        approx_tokens = int(n_chars / (1.6 if plang == "zh" else 3.6))
        st.caption(t(f"{n_chars:,} 个字符 · 约 {approx_tokens:,} 个 token · 用右上角的图标复制",
                     f"{n_chars:,} characters · roughly {approx_tokens:,} tokens · copy with the icon at the top right"))
        st.code(text, language=None, wrap_lines=True, height=420)
        with st.container(horizontal=True):
            st.download_button(t("提示词（.txt）", "Prompt (.txt)"), text.encode("utf-8"), file_name=f"prompt_{stamp()}.txt",
                               mime="text/plain", on_click="ignore", icon=":material/download:")
            for fname, df in attach.items():
                if df is not None and not df.empty:
                    st.download_button(t(f"附件：{fname}", f"Attach: {fname}"), to_csv_bytes(df), file_name=fname,
                                       mime="text/csv", on_click="ignore", icon=":material/attach_file:",
                                       key=f"att_{fname}")
        with st.expander(t("保存到提示词库", "Save to the prompt library"), icon=":material/bookmark:"):
            name = st.text_input(t("名称", "Name"), value=tpl(kind, 0), key="pb_save_name")
            if st.button(t("保存", "Save"), icon=":material/save:"):
                prompts.save_to_library(name, text)
                st.toast(t(f"已保存“{name}”", f"Saved “{name}”"))

with tab_lib:
    lib = prompts.load_library()
    st.caption(t("内置提示词加上你保存的提示词。保存的提示词在 data/prompt_library.json。",
                 "Built-in prompts plus anything you saved. Saved prompts live in data/prompt_library.json."))
    pick = st.selectbox(t("提示词", "Prompt"), [p["name"] for p in lib], key="lib_pick")
    item = next(p for p in lib if p["name"] == pick)
    st.code(item["text"], language=None, wrap_lines=True, height=380)
    with st.container(horizontal=True):
        st.download_button(t("下载（.txt）", "Download (.txt)"), item["text"].encode("utf-8"),
                           file_name=f"{pick[:40]}.txt", mime="text/plain", on_click="ignore",
                           icon=":material/download:")
        if not item["builtin"] and st.button(t("删除", "Delete"), icon=":material/delete:"):
            prompts.delete_from_library(pick)
            st.rerun()
    with st.expander(t("写一个新提示词", "Write a new prompt"), icon=":material/edit_note:"):
        new_name = st.text_input(t("名称", "Name"), key="lib_new_name")
        new_text = st.text_area(t("提示词内容", "Prompt text"), key="lib_new_text", height=200)
        if st.button(t("加入提示词库", "Add to library"), icon=":material/playlist_add:", disabled=not (new_name and new_text)):
            prompts.save_to_library(new_name, new_text)
            st.toast(t(f"已保存“{new_name}”", f"Saved “{new_name}”"))
            st.rerun()
