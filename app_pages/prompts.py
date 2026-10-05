"""AI prompt builder: paste-ready prompts filled with live data. No AI is called from the app."""

import pandas as pd
import streamlit as st

from stockrt import analytics, history, intraday, prompts, screener
from stockrt.config import BENCHMARKS, INDEX_NAMES
from stockrt.export import stamp, to_csv_bytes
from stockrt.realtime import breadth
from stockrt.symbols import market
from ui import cache

names = {**cache.name_map(), **INDEX_NAMES}
fmt = lambda c: f"{c[2:]} · {names.get(c, c)}"  # noqa: E731

st.markdown("Build a prompt, copy it with the button in the top-right corner of the box, and paste it into any AI "
            "assistant. Prompts carry the data itself (as CSV) plus exact column definitions, so the answer is "
            "grounded in what this app sees. Nothing is sent anywhere from here.")

tab_build, tab_lib = st.tabs(["Build from live data", "Prompt library"])

TEMPLATES = {
    "Review my latest screen results": "Ranks the stocks your last screen found and flags risks.",
    "Run my screen yourself (attach CSVs)": "Your 10-rule late-session screen in plain words, for an AI that can "
                                            "read attached files.",
    "Intraday review at a cut-off time": "Morning vs afternoon, VWAP, volume - for your watchlist, at 14:00 etc.",
    "Compare risk and return": "Multi-stock metrics, correlation and yearly returns.",
    "Single-stock brief": "KPIs, today's session, last 30 days and announcement titles for one stock.",
    "Market mood summary": "Breadth, limit-up / limit-down and the most-traded stocks right now.",
    "Ask anything about a dataset": "Your own question plus a dataset from the app.",
}

with tab_build:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        kind = st.selectbox("Template", list(TEMPLATES), key="pb_kind", persist_state="session", width=360)
        lang = st.segmented_control("Language", ["en", "zh"], default="en", key="pb_lang", required=True,
                                    format_func=lambda x: {"en": "English", "zh": "中文"}[x], persist_state="session")
    st.caption(TEMPLATES[kind])

    text, attach = None, {}
    if kind == "Review my latest screen results":
        res = st.session_state.get("scr_result")
        if not res:
            st.info("No screen has been run in this session. Run the default late-session screen now, or open the "
                    "Screener page to edit the rules.")
            if st.button("Run the default screen", icon=":material/filter_alt:"):
                snap, t = cache.snapshot("CN")
                b = cache.bench_quote("CN")
                with st.spinner("Screening the whole market…"):
                    r = screener.run(snap, screener.ScreenParams(), float(b["pct_change"]) if b else None)
                st.session_state.scr_result = {"res": r, "params": screener.ScreenParams(), "as_of": t}
                st.rerun()
        else:
            r = res["res"]
            text = prompts.screen_review(screener.describe(res["params"]), r.funnel, r.passed, r.near_misses,
                                         res["as_of"], lang)
            attach["screen_candidates.csv"] = r.candidates
    elif kind == "Run my screen yourself (attach CSVs)":
        text = prompts.screen_spec(lang)
        snap, t = cache.snapshot("CN")
        attach["cn_market_snapshot.csv"] = snap
        st.caption("Attach the market snapshot below. For the moving-average rules, also attach a daily dataset "
                   "(Data downloads → Daily datasets → All A-shares, 1M).")
    elif kind == "Intraday review at a cut-off time":
        codes = st.multiselect("Symbols", st.session_state.watchlist + [c for c in names if c not in
                               st.session_state.watchlist], default=st.session_state.watchlist[:12],
                               format_func=fmt, key="pb_intra_codes", max_selections=24)
        cut_label = st.segmented_control("Up to", ["11:30", "13:30", "14:00", "14:30", "Full day"], default="14:00",
                                         key="pb_cut", required=True)
        if codes:
            table, minutes = cache.intraday_table(tuple(codes), None if cut_label == "Full day" else cut_label)
            if not table.empty:
                dates = ", ".join(sorted({str(d) for d in table["trade_date"]}))
                text = prompts.intraday_review(table, f"{cut_label} on {dates}", lang)
                attach["intraday_1min.csv"] = minutes
    elif kind == "Compare risk and return":
        codes = st.multiselect("Symbols (up to 8)", st.session_state.watchlist + [c for c in names if c not in
                               st.session_state.watchlist], default=st.session_state.watchlist[:6],
                               format_func=fmt, key="pb_cmp_codes", max_selections=8)
        period = st.segmented_control("Period", analytics.PERIODS, default="1Y", key="pb_period", required=True)
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
            text = prompts.compare_review(m, period, " / ".join(labels[b] for b in benches), corr, yearly, lang)
            attach["closes_wide.csv"] = panel.reset_index()
    elif kind == "Single-stock brief":
        code = st.selectbox("Stock", st.session_state.watchlist + [c for c in names if c not in
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
                                   daily.tail(30), anns["title"].tolist() if not anns.empty else [], lang)
        attach[f"{code}_daily.csv"] = daily
        attach[f"{code}_1min.csv"] = bars
    elif kind == "Market mood summary":
        snap, t = cache.snapshot("CN")
        b = breadth(snap)
        movers = snap.sort_values("amount", ascending=False).head(40)
        text = prompts.market_summary(b, movers, t, lang)
        attach["cn_market_snapshot.csv"] = snap
    else:
        question = st.text_area("Your question", "Which of these stocks look strongest into the close, and why?",
                                key="pb_question", height=90)
        ds = st.segmented_control("Dataset", ["Watchlist quotes", "Last screen candidates", "A-share snapshot (top by value)",
                                              "Index ETF scorecard"], default="Watchlist quotes", key="pb_ds",
                                  required=True)
        n = st.slider("Rows to include", 10, 200, 60, key="pb_rows")
        if ds == "Watchlist quotes":
            data = cache.quotes(tuple(st.session_state.watchlist))
        elif ds == "Last screen candidates":
            res = st.session_state.get("scr_result")
            data = res["res"].candidates if res else pd.DataFrame()
            if data.empty:
                st.info("Run a screen first.")
        elif ds == "A-share snapshot (top by value)":
            data = cache.snapshot("CN")[0].sort_values("amount", ascending=False)
        else:
            data = cache.etf_scorecard("1Y", st.session_state.rf)
        if not data.empty:
            text = prompts.custom(question, data, ds, lang, n)
            attach["dataset.csv"] = data

    if text:
        n_chars = len(text)
        approx_tokens = int(n_chars / (1.6 if lang == "zh" else 3.6))
        st.caption(f"{n_chars:,} characters · roughly {approx_tokens:,} tokens · copy with the icon at the top right")
        st.code(text, language=None, wrap_lines=True, height=420)
        with st.container(horizontal=True):
            st.download_button("Prompt (.txt)", text.encode("utf-8"), file_name=f"prompt_{stamp()}.txt",
                               mime="text/plain", on_click="ignore", icon=":material/download:")
            for fname, df in attach.items():
                if df is not None and not df.empty:
                    st.download_button(f"Attach: {fname}", to_csv_bytes(df), file_name=fname, mime="text/csv",
                                       on_click="ignore", icon=":material/attach_file:", key=f"att_{fname}")
        with st.expander("Save to the prompt library", icon=":material/bookmark:"):
            name = st.text_input("Name", value=kind, key="pb_save_name")
            if st.button("Save", icon=":material/save:"):
                prompts.save_to_library(name, text)
                st.toast(f"Saved “{name}”")

with tab_lib:
    lib = prompts.load_library()
    st.caption("Built-in prompts plus anything you saved. Saved prompts live in data/prompt_library.json.")
    pick = st.selectbox("Prompt", [p["name"] for p in lib], key="lib_pick")
    item = next(p for p in lib if p["name"] == pick)
    st.code(item["text"], language=None, wrap_lines=True, height=380)
    with st.container(horizontal=True):
        st.download_button("Download (.txt)", item["text"].encode("utf-8"), file_name=f"{pick[:40]}.txt",
                           mime="text/plain", on_click="ignore", icon=":material/download:")
        if not item["builtin"] and st.button("Delete", icon=":material/delete:"):
            prompts.delete_from_library(pick)
            st.rerun()
    with st.expander("Write a new prompt", icon=":material/edit_note:"):
        new_name = st.text_input("Name", key="lib_new_name")
        new_text = st.text_area("Prompt text", key="lib_new_text", height=200)
        if st.button("Add to library", icon=":material/playlist_add:", disabled=not (new_name and new_text)):
            prompts.save_to_library(new_name, new_text)
            st.toast(f"Saved “{new_name}”")
            st.rerun()
