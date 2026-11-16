"""Live dashboard: one stock - real-time quote, KPIs, intraday and daily charts, sentiment."""

import pandas as pd
import streamlit as st

from stockrt import analytics, history, intraday, prompts, sentiment
from stockrt.calendar import now_bj, status
from stockrt.export import to_csv_bytes
from stockrt.symbols import board, market
from ui import cache, charts
from ui.fmt import delta_color, money, num, pct, price, quote_columns
from ui.i18n import BOARDS_ZH, lang, t, zh

SENTIMENT_ZH = {"Bullish": "看多", "Mildly bullish": "略偏多", "Neutral": "中性", "Mildly bearish": "略偏空",
                "Bearish": "看空", "n/a": "无"}
COMPONENTS_ZH = {"Order flow": "主动买卖", "Main funds": "主力资金", "Relative strength": "相对强度",
                 "Close location": "收盘位置", "Announcements": "公告"}
ADJ_ZH = [("price index (no adjustment)", "价格指数（未复权）"), ("ratio-adjusted (dividend text)", "比例复权（分红文本）"),
          ("unadjusted (no corporate actions found)", "未复权（未找到除权除息）"), ("ratio-adjusted", "比例复权"),
          (" factors)", " 复权因子）")]


def adj_text(label: str) -> str:
    if not zh():
        return label
    for en, cn in ADJ_ZH:
        label = label.replace(en, cn)
    return label.replace("(", "（")


names = cache.name_map()
wl = st.session_state.watchlist
options = wl + [c for c in names if c not in set(wl)]

with st.container(horizontal=True, vertical_alignment="bottom"):
    code = st.selectbox(
        t("股票", "Stock"), options, key="live_code", persist_state="session",
        format_func=lambda c: f"{c[2:]} · {names.get(c, c)}", width=340,
        help=t("自选股在前；输入代码或名称可搜索全部A股和港股。",
               "Watchlist first; type a code or name to search every A-share and HK stock."))
    period = st.segmented_control(t("区间", "Period"), analytics.PERIODS, default="1Y", key="live_period",
                                  persist_state="session", required=True)
    auto = st.toggle(t("自动刷新（30 秒）", "Auto-refresh (30 s)"), value=True, key="live_auto", persist_state="session",
                     help=t("交易时间内刷新行情和分时图。", "Refreshes the quote and intraday chart while the market is trading."))

mkt = market(code)
sess = status(mkt)


def render() -> None:
    q_df = cache.quotes((code,))
    if q_df.empty:
        st.error(t("没有返回这只股票的行情。", "No quote returned for this symbol."))
        return
    q = q_df.iloc[0].to_dict()
    cur = q.get("currency") or ("HKD" if mkt == "HK" else "CNY")
    qt = pd.Timestamp(q["time"]) if q.get("time") is not None else None
    is_live = bool(qt is not None and qt.date() == now_bj().date() and sess.is_trading)

    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(f"{q['name']} · {code[2:]}")
        st.badge(t("实时", "Live") if is_live else t("上一交易日", "Last session"),
                 icon=":material/bolt:" if is_live else ":material/schedule:", color="green" if is_live else "gray")
        st.badge(t(BOARDS_ZH.get(board(code), board(code)), board(code)), color="blue")
        st.caption(t(f"行情时间 {qt:%Y-%m-%d %H:%M:%S} 北京 · {cur}", f"Quote time {qt:%Y-%m-%d %H:%M:%S} Beijing · {cur}")
                   if qt is not None else "")

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
                st.metric(t("最新价", "Latest price"), price(q["price"], cur),
                          f"{q['change']:+.2f} ({q['pct_change']:+.2f}%)", delta_color=delta_color(), border=True)
                st.metric(t("当日涨跌", "Daily return"), pct(q["pct_change"], already_pct=True),
                          t(f"指数 {pct(bench['pct_change'], already_pct=True)}",
                            f"index {pct(bench['pct_change'], already_pct=True)}") if bench else None,
                          delta_color="off", border=True)
                st.metric(t("近 1 月收益", "1-month return"), pct(one_m), border=True,
                          chart_data=close.tail(22).round(3).tolist(), chart_type="line")
                st.metric(t(f"{period} 收益", f"{period} return"), pct(analytics.total_return(w)), border=True,
                          help=t("含分红的总收益（比例复权价格）。", "Total return including dividends (ratio-adjusted prices)."))
            else:
                st.metric(t(f"波动率（{period}，年化）", f"Volatility ({period}, ann.)"), vol_txt, border=True)
                st.metric(t(f"最大回撤（{period}）", f"Max drawdown ({period})"), pct(analytics.max_drawdown(w)),
                          border=True)
                st.metric(t("情绪", "Sentiment"), f"{senti['score']:+.0f}" if senti["score"] is not None else "—",
                          t(SENTIMENT_ZH.get(senti["label"], senti["label"]), senti["label"]), delta_color="off",
                          border=True,
                          help=t("基于规则：主动买卖、主力资金、相对强度、收盘位置、公告关键词。−100 到 +100，明细见下方。",
                                 "Rule-based: order flow, main funds, relative strength, close location, "
                                 "announcement keywords. -100 to +100. Breakdown below."))
                yld = pct(q.get("div_yield"), signed=False, already_pct=True)
                st.metric(t("市盈率（TTM）", "P/E (TTM)"), num(q.get("pe_ttm"), 1),
                          t(f"市净率 {num(q.get('pb'))} · 股息率 {yld}", f"P/B {num(q.get('pb'))} · yield {yld}"),
                          delta_color="off", border=True)

    day, prev_close, bars = cache.minute_today(code)
    left, right = st.columns([1.9, 1])
    with left:
        with st.container(border=True):
            title = t("分时", "Intraday") + ("" if day == now_bj().date() else
                                             t(f" · 上一交易日 {day:%Y-%m-%d}", f" · last session {day:%Y-%m-%d}"))
            st.markdown(t(f"**{title}** · 昨收 {num(prev_close)}（灰线）", f"**{title}** · prev close {num(prev_close)} (grey line)"))
            if bars.empty:
                st.info(t("本交易日还没有分时数据。", "No minute data yet for this session."))
            else:
                st.altair_chart(charts.intraday_price(bars, prev_close, mkt))
                st.altair_chart(charts.intraday_volume(bars))
    with right:
        with st.container(border=True):
            st.markdown(t("**交易统计**", "**Session statistics**"))
            s = intraday.stats(bars, prev_close, mkt) if not bars.empty else {}
            morning_end = "11:30" if mkt == "CN" else "12:00"
            buy_share = (f"{q['buy_volume'] / max(q['buy_volume'] + q['sell_volume'], 1):.0%}" if q.get("buy_volume")
                         else None)
            st.table({
                t(f"上午（至 {morning_end}）", f"Morning (to {morning_end})"): pct(s.get("am_return")),
                t("下午至今", "Afternoon so far"): pct(s.get("pm_return")),
                t("最近 30 分钟", "Last 30 min"): pct(s.get("last_30m_return")),
                t("均价", "VWAP"): num(s.get("vwap")),
                t("相对均价", "Price vs VWAP"): pct(s.get("vs_vwap")),
                t("最高 / 最低", "High / low"): f"{num(s.get('high'))} @ {s.get('high_time', '—')} · "
                                             f"{num(s.get('low'))} @ {s.get('low_time', '—')}",
                t("量比", "Volume ratio (量比)"): num(q.get("volume_ratio")),
                t("换手率", "Turnover (换手率)"): pct(q.get("turnover_rate"), signed=False, already_pct=True),
                t("成交额", "Value traded"): money(q.get("amount"), cur),
                t("主力净流入", "Main funds net"): money(flow.get("main_net"), cur) if flow else t("无", "n/a"),
                t("主动买 / 卖量", "Active buy / sell vol."): (t(f"买入 {buy_share}", f"{buy_share} buy") if buy_share
                                                           else t("无（港股）", "n/a (HK)")),
            }, border="horizontal")

    st.markdown(f"**{t('日线', 'Daily price')} · {period}** :gray[· {adj_text(adj_label)}]")
    view = daily_df[daily_df["date"] >= w.index[0]] if len(w) else daily_df
    st.altair_chart(charts.daily_price(view))
    st.altair_chart(charts.daily_volume(view))

    c1, c2 = st.columns([1, 1.4])
    with c1:
        with st.container(border=True):
            st.markdown(t("**情绪明细**（每项 −1 到 +1）", "**Sentiment breakdown** (each component −1 to +1)"))
            comp = pd.DataFrame([{"signal": t(COMPONENTS_ZH.get(k, k), k), "score": v}
                                 for k, v in senti["components"].items()])
            if comp.empty:
                st.caption(t("数据不足。", "Not enough data."))
            else:
                st.dataframe(comp, hide_index=True, column_config={
                    "signal": st.column_config.TextColumn(t("信号", "Signal")),
                    "score": st.column_config.ProgressColumn(t("分数", "Score"), min_value=-1, max_value=1,
                                                             format="%.2f")})
    with c2:
        with st.container(border=True):
            st.markdown(t("**最新公告** · 点击标题打开，或点 PDF 查看原文",
                          "**Recent announcements** · click a title to open it, or the PDF for the original filing"))
            if anns.empty:
                st.caption(t("没有返回公告。", "No announcements returned."))
            else:
                hits = {h["title"]: h for h in senti["headline_hits"]}
                prefix = f"{q['name']}:"

                def md_link(text: str, url: str | None) -> str:
                    safe = text.replace("[", "(").replace("]", ")")
                    return f"[{safe}]({url})" if url else safe

                cols = (t("日期", "Date"), t("标题", "Title"), t("类型", "Type"), t("语气", "Tone"))
                table = {c: [] for c in cols}
                for _, a in anns.head(12).iterrows():
                    title = a["title"] or ""
                    short = title[len(prefix):] if title.startswith(prefix) else title
                    tone = hits[title]["score"] if title in hits else None
                    table[cols[0]].append(a["date"])
                    table[cols[1]].append(md_link(short, a.get("url")) +
                                          (f" · [PDF]({a['pdf']})" if a.get("pdf") else ""))
                    table[cols[2]].append(a["category"] or "")
                    table[cols[3]].append("" if tone is None else
                                          (f":red[{tone:+.1f}]" if (tone > 0) == st.session_state.up_red and tone
                                           else f":green[{tone:+.1f}]" if tone else "0"))
                st.table(table, hide_index=True, border="horizontal")
                st.caption(t("语气 = 标题关键词得分（−1 到 +1），计入上面的情绪。",
                             "Tone = keyword score of the title (−1 to +1), used in the sentiment above."))

    with st.container(horizontal=True):
        if not bars.empty:
            st.download_button(t("分时 1 分钟 CSV", "Intraday 1-min CSV"), to_csv_bytes(bars.assign(code=code)),
                               on_click="ignore", file_name=f"{code}_{day:%Y%m%d}_1min.csv", mime="text/csv",
                               icon=":material/download:")
        st.download_button(t("日线历史 CSV", "Daily history CSV"), to_csv_bytes(daily_df.assign(code=code)),
                           on_click="ignore", file_name=f"{code}_daily.csv", mime="text/csv",
                           icon=":material/download:")
        with st.popover(t("这只股票的 AI 提示词", "AI prompt for this stock"), icon=":material/content_copy:"):
            kpis = {"price": q["price"], "change_pct_today": q["pct_change"], "1M_return": round(one_m, 4),
                    f"{period}_return": round(analytics.total_return(w), 4),
                    "volatility_ann": round(analytics.ann_vol(rets), 4),
                    "max_drawdown": round(analytics.max_drawdown(w), 4), "pe_ttm": q.get("pe_ttm"),
                    "pb": q.get("pb"), "turnover_rate_pct": q.get("turnover_rate"),
                    "volume_ratio": q.get("volume_ratio"), "sentiment_score": senti["score"]}
            text = prompts.stock_brief(q["name"], code, kpis, {k: v for k, v in s.items() if k != "as_of"},
                                       daily_df.tail(30), anns["title"].tolist() if not anns.empty else [], lang())
            st.code(text, language=None, wrap_lines=True, height=320)


if auto and sess.is_trading:
    st.fragment(run_every="30s")(render)()
else:
    render()

with st.expander(t("这些数字是什么意思？", "What do these numbers mean?"), icon=":material/info:"):
    st.markdown(t(
        "- **收益** 使用比例复权价格（分红和拆股再投资），因此长区间为总收益。\n"
        "- **波动率** 是日收益的年化标准差（按 252 个交易日）。\n"
        "- **最大回撤** 是区间内从最高点到最低点的最大跌幅。\n"
        "- **量比** 是今日至今每分钟成交量与前5日平均的比较。\n"
        "- **主力资金** = 大单 + 特大单净买入（东方财富，否则新浪）。\n"
        "- **情绪** 基于规则（不是 AI）；每一项都在明细中列出。",
        "- **Returns** use ratio-adjusted prices (dividends and splits reinvested), so long periods are total returns.\n"
        "- **Volatility** is the annualised standard deviation of daily returns (252 trading days).\n"
        "- **Max drawdown** is the worst peak-to-trough fall inside the period.\n"
        "- **Volume ratio (量比)** compares volume per minute so far today with the 5-day average.\n"
        "- **Main funds** = large + extra-large order net buying (East Money, else Sina).\n"
        "- **Sentiment** is rule-based (no AI); every component is shown in the breakdown."))
    st.dataframe(pd.DataFrame([cache.quotes((code,)).iloc[0]]), hide_index=True, column_config=quote_columns())
