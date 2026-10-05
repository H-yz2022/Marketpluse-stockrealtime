"""Live dashboard: one stock - real-time quote, KPIs, intraday and daily charts, sentiment."""

import pandas as pd
import streamlit as st

from stockrt import analytics, history, intraday, prompts, sentiment
from stockrt.calendar import now_bj, status
from stockrt.export import to_csv_bytes
from stockrt.symbols import board, market
from ui import cache, charts
from ui.fmt import QUOTE_COLUMNS, delta_color, money, num, pct, price

names = cache.name_map()
wl = st.session_state.watchlist
options = wl + [c for c in names if c not in set(wl)]

with st.container(horizontal=True, vertical_alignment="bottom"):
    code = st.selectbox(
        "Stock", options, key="live_code", persist_state="session",
        format_func=lambda c: f"{c[2:]} · {names.get(c, c)}", width=340,
        help="Watchlist first; type a code or name to search every A-share and HK stock.")
    period = st.segmented_control("Period", analytics.PERIODS, default="1Y", key="live_period",
                                  persist_state="session", required=True)
    auto = st.toggle("Auto-refresh (30 s)", value=True, key="live_auto", persist_state="session",
                     help="Refreshes the quote and intraday chart while the market is trading.")

mkt = market(code)
sess = status(mkt)


def render() -> None:
    q_df = cache.quotes((code,))
    if q_df.empty:
        st.error("No quote returned for this symbol.")
        return
    q = q_df.iloc[0].to_dict()
    cur = q.get("currency") or ("HKD" if mkt == "HK" else "CNY")
    qt = pd.Timestamp(q["time"]) if q.get("time") is not None else None
    is_live = bool(qt is not None and qt.date() == now_bj().date() and sess.is_trading)

    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(f"{q['name']} · {code[2:]}")
        st.badge("Live" if is_live else "Last session", icon=":material/bolt:" if is_live else ":material/schedule:",
                 color="green" if is_live else "gray")
        st.badge(board(code), color="blue")
        st.caption(f"Quote time {qt:%Y-%m-%d %H:%M:%S} Beijing · {cur}" if qt is not None else "")

    daily_df, adj_label = cache.daily(code)
    daily_df = history.append_live(daily_df, q)
    close = daily_df.set_index("date")["close"]
    bench = cache.bench_quote(mkt)
    flow_df = cache.main_flow((code,))
    flow = flow_df.iloc[0].to_dict() if not flow_df.empty else None
    anns = cache.announcements(code)
    senti = sentiment.stock_sentiment(q, flow, bench.get("pct_change") if bench else None,
                                      anns["title"].tolist() if not anns.empty else [])

    w = analytics.window(close, period, strict=period != "Max")
    rets = analytics.daily_returns(w)
    one_m = analytics.total_return(analytics.window(close, "1M"))
    vol_txt = pct(analytics.ann_vol(rets), signed=False)
    for row in (0, 1):
        with st.container(horizontal=True):
            if row == 0:
                st.metric("Latest price", price(q["price"], cur), f"{q['change']:+.2f} ({q['pct_change']:+.2f}%)",
                          delta_color=delta_color(), border=True)
                st.metric("Daily return", pct(q["pct_change"], already_pct=True),
                          f"index {pct(bench['pct_change'], already_pct=True)}" if bench else None,
                          delta_color="off", border=True)
                st.metric("1-month return", pct(one_m), border=True,
                          chart_data=close.tail(22).round(3).tolist(), chart_type="line")
                st.metric(f"{period} return", pct(analytics.total_return(w)), border=True,
                          help="Total return including dividends (ratio-adjusted prices).")
            else:
                st.metric(f"Volatility ({period}, ann.)", vol_txt, border=True)
                st.metric(f"Max drawdown ({period})", pct(analytics.max_drawdown(w)), border=True)
                st.metric("Sentiment", f"{senti['score']:+.0f}" if senti["score"] is not None else "—",
                          senti["label"], delta_color="off", border=True,
                          help="Rule-based: order flow, main funds, relative strength, close location, "
                               "announcement keywords. -100 to +100. Breakdown below.")
                st.metric("P/E (TTM)", num(q.get("pe_ttm"), 1),
                          f"P/B {num(q.get('pb'))} · yield {pct(q.get('div_yield'), signed=False, already_pct=True)}",
                          delta_color="off", border=True)

    day, prev_close, bars = cache.minute_today(code)
    left, right = st.columns([1.9, 1])
    with left:
        with st.container(border=True):
            title = "Intraday" + ("" if day == now_bj().date() else f" · last session {day:%Y-%m-%d}")
            st.markdown(f"**{title}** · prev close {num(prev_close)} (grey line)")
            if bars.empty:
                st.info("No minute data yet for this session.")
            else:
                st.altair_chart(charts.intraday_price(bars, prev_close, mkt))
                st.altair_chart(charts.intraday_volume(bars))
    with right:
        with st.container(border=True):
            st.markdown("**Session statistics**")
            s = intraday.stats(bars, prev_close, mkt) if not bars.empty else {}
            st.table({
                "Morning (to 11:30)" if mkt == "CN" else "Morning (to 12:00)": pct(s.get("am_return")),
                "Afternoon so far": pct(s.get("pm_return")),
                "Last 30 min": pct(s.get("last_30m_return")),
                "VWAP": num(s.get("vwap")),
                "Price vs VWAP": pct(s.get("vs_vwap")),
                "High / low": f"{num(s.get('high'))} @ {s.get('high_time', '—')} · "
                              f"{num(s.get('low'))} @ {s.get('low_time', '—')}",
                "Volume ratio (量比)": num(q.get("volume_ratio")),
                "Turnover (换手率)": pct(q.get("turnover_rate"), signed=False, already_pct=True),
                "Value traded": money(q.get("amount"), cur),
                "Main funds net": money(flow.get("main_net"), cur) if flow else "n/a",
                "Active buy / sell vol.": (f"{q['buy_volume'] / max(q['buy_volume'] + q['sell_volume'], 1):.0%} buy"
                                           if q.get("buy_volume") else "n/a (HK)"),
            }, border="horizontal")

    st.markdown(f"**Daily price · {period}** :gray[· {adj_label}]")
    view = daily_df[daily_df["date"] >= w.index[0]] if len(w) else daily_df
    st.altair_chart(charts.daily_price(view))
    st.altair_chart(charts.daily_volume(view))

    c1, c2 = st.columns([1, 1.4])
    with c1:
        with st.container(border=True):
            st.markdown("**Sentiment breakdown** (each component −1 to +1)")
            comp = pd.DataFrame([{"Signal": k, "Score": v} for k, v in senti["components"].items()])
            if comp.empty:
                st.caption("Not enough data.")
            else:
                st.dataframe(comp, hide_index=True, column_config={
                    "Score": st.column_config.ProgressColumn("Score", min_value=-1, max_value=1, format="%.2f")})
    with c2:
        with st.container(border=True):
            st.markdown("**Recent announcements** · click a title to open it, or the PDF for the original filing")
            if anns.empty:
                st.caption("No announcements returned.")
            else:
                hits = {h["title"]: h for h in senti["headline_hits"]}
                prefix = f"{q['name']}:"

                def md_link(text: str, url: str | None) -> str:
                    safe = text.replace("[", "(").replace("]", ")")
                    return f"[{safe}]({url})" if url else safe

                table = {"Date": [], "Title": [], "Type": [], "Tone": []}
                for _, a in anns.head(12).iterrows():
                    title = a["title"] or ""
                    short = title[len(prefix):] if title.startswith(prefix) else title
                    tone = hits[title]["score"] if title in hits else None
                    table["Date"].append(a["date"])
                    table["Title"].append(md_link(short, a.get("url")) +
                                          (f" · [PDF]({a['pdf']})" if a.get("pdf") else ""))
                    table["Type"].append(a["category"] or "")
                    table["Tone"].append("" if tone is None else
                                         (f":red[{tone:+.1f}]" if (tone > 0) == st.session_state.up_red and tone
                                          else f":green[{tone:+.1f}]" if tone else "0"))
                st.table(table, hide_index=True, border="horizontal")
                st.caption("Tone = keyword score of the title (−1 to +1), used in the sentiment above.")

    with st.container(horizontal=True):
        if not bars.empty:
            st.download_button("Intraday 1-min CSV", to_csv_bytes(bars.assign(code=code)), on_click="ignore",
                               file_name=f"{code}_{day:%Y%m%d}_1min.csv", mime="text/csv",
                               icon=":material/download:")
        st.download_button("Daily history CSV", to_csv_bytes(daily_df.assign(code=code)), on_click="ignore",
                           file_name=f"{code}_daily.csv", mime="text/csv", icon=":material/download:")
        with st.popover("AI prompt for this stock", icon=":material/content_copy:"):
            lang = st.segmented_control("Language", ["en", "zh"], default="en", key="live_prompt_lang",
                                        format_func=lambda x: {"en": "English", "zh": "中文"}[x], required=True)
            kpis = {"price": q["price"], "change_pct_today": q["pct_change"], "1M_return": round(one_m, 4),
                    f"{period}_return": round(analytics.total_return(w), 4),
                    "volatility_ann": round(analytics.ann_vol(rets), 4),
                    "max_drawdown": round(analytics.max_drawdown(w), 4), "pe_ttm": q.get("pe_ttm"),
                    "pb": q.get("pb"), "turnover_rate_pct": q.get("turnover_rate"),
                    "volume_ratio": q.get("volume_ratio"), "sentiment_score": senti["score"]}
            text = prompts.stock_brief(q["name"], code, kpis, {k: v for k, v in s.items() if k != "as_of"},
                                       daily_df.tail(30), anns["title"].tolist() if not anns.empty else [], lang)
            st.code(text, language=None, wrap_lines=True, height=320)


if auto and sess.is_trading:
    st.fragment(run_every="30s")(render)()
else:
    render()

with st.expander("What do these numbers mean?", icon=":material/info:"):
    st.markdown(
        "- **Returns** use ratio-adjusted prices (dividends and splits reinvested), so long periods are total returns.\n"
        "- **Volatility** is the annualised standard deviation of daily returns (252 trading days).\n"
        "- **Max drawdown** is the worst peak-to-trough fall inside the period.\n"
        "- **Volume ratio (量比)** compares volume per minute so far today with the 5-day average.\n"
        "- **Main funds** = large + extra-large order net buying (East Money, else Sina).\n"
        "- **Sentiment** is rule-based (no AI); every component is shown in the breakdown.")
    st.dataframe(pd.DataFrame([cache.quotes((code,)).iloc[0]]), hide_index=True, column_config=QUOTE_COLUMNS)
