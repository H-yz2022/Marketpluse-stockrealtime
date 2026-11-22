"""尾盘选股 view shared by the A-share front page and the Hong Kong page.

Built for a free hosting plan, where every request to the mainland sources is slow:

* the newest saved result shows instantly (disk, the repository archive, or the
  bundled sample) - the page is never empty;
* a checkpoint that hasn't been computed yet is rebuilt by a background job while
  that saved result stays on screen, and is swapped in when ready;
* past sessions sit in a collapsed section and load only when opened;
* before the open, the pre-open auction (集合竞价) shows indicative prices.

Every text is in one language at a time (ui.i18n: 中文 by default, English on request).
"""

import dataclasses
from datetime import date, time, timedelta

import pandas as pd
import streamlit as st

from stockrt import (
    archive,
    auction,
    checkpoint,
    jobs,
    opening,
    prompts,
    published,
    sample,
    screener,
    watchlist,
)
from stockrt.calendar import now_bj, status
from stockrt.export import stamp, to_csv_bytes
from stockrt.symbols import market
from ui.fmt import pct, style_signed
from ui.i18n import day as fday
from ui.i18n import desktop, t
from ui.i18n import wd as fwd

QUERY = {
    "CN": {
        "en": "Non-ST stocks up **3%–5%**, volume ratio **> 1**, turnover **3%–8%**, the **5-day MA crossing above "
              "the 10-day MA**, moving averages **rising**, **volume and price both increasing**, **main funds "
              "flowing in**, **outperforming the market**, and float market value **CNY 5–20 billion**.",
        "zh": "非ST，涨幅 **3%–5%**，量比 **大于1**，换手率 **3%–8%**，**5日均线上穿10日均线**，均线**向上**，"
              "**量价齐升**，**主力资金流入**，**强于大盘**，流通市值 **50亿–200亿**。",
    },
    "HK": {
        "en": "The same screen on Hong Kong stocks: up **3%–5%**, volume ratio **> 1**, turnover **{turnover}**, "
              "the **5-day MA crossing above the 10-day MA**, moving averages **rising**, **volume and price both "
              "increasing**, **main funds flowing in**, **beating the Hang Seng Index**, and market value "
              "**HKD 5–20 billion**.",
        "zh": "同一套条件用于港股：涨幅 **3%–5%**，量比 **大于1**，换手率 **{turnover}**，**5日均线上穿10日均线**，"
              "均线**向上**，**量价齐升**，**主力资金流入**，**强于恒生指数**，市值 **50亿–200亿港元**。",
    },
}
TIMES = {
    "CN": ("北京时间 **14:00**、**14:30** 和 **15:00 收盘**各检查一次。",
           "Checked at **14:00**, **14:30** and the **15:00 close** (Beijing time; 02:00 / 02:30 / 03:00 in New York "
           "during daylight saving)."),
    "HK": ("**15:00**、**15:30** 和 **16:00 收盘**各检查一次（香港与北京同一时区）——港股晚一小时收盘，"
           "因此同样是收盘前最后一小时。",
           "Checked at **15:00**, **15:30** and the **16:00 close** (Hong Kong = Beijing time; 03:00 / 03:30 / 04:00 "
           "in New York during daylight saving) - the same last-hour pattern, since Hong Kong closes an hour later."),
}
HK_ADAPTED = dataclasses.replace(screener.ScreenParams(), turnover_min=0.5)
TIERS = {
    "A": ("A · 每个检查点都满足全部条件", "A · all rules at every checkpoint"),
    "B": ("B · 最新检查点满足全部条件", "B · all rules at the latest checkpoint"),
    "C": ("C · 之前满足，最新未满足", "C · earlier, not at the latest"),
    "D": ("D · 差一条且只差一点", "D · one rule narrowly missed"),
}
AUCTION = {
    "CN": ("A股集合竞价时间为 **09:15–09:25**（09:20 前可撤单）。09:25 撮合出的价格即开盘价，09:30 开始连续竞价。"
           "在此之前下面的价格都是**参考价**：假如竞价此刻结束会成交的价格，实际尚未成交。",
           "A-shares hold a call auction from **09:15 to 09:25** (orders can be cancelled until 09:20). The matched "
           "price becomes the opening price at 09:25 and continuous trading starts at 09:30. Until then the prices "
           "below are **indicative**: what would trade if the auction ended now. Nothing has traded yet."),
    "HK": ("港股开市前时段为 **09:00–09:20**（约 09:20 撮合），09:30 开始持续交易。此前价格均为**参考价**。"
           "免费港股行情约延迟 15 分钟。",
           "Hong Kong's pre-opening session runs **09:00-09:20** (matching about 09:20), and continuous trading starts "
           "at 09:30. The prices below are **indicative** until then. Free HK quotes run about 15 minutes behind."),
}
APP_DOWNLOAD = "https://github.com/H-yz2022/Marketpluse-stockrealtime/releases/download/desktop/ChinaHKMarkets.exe"


def tier_text(k: str) -> str:
    return t(*TIERS[k]) if k in TIERS else "-"


@st.cache_resource
def job_runner() -> jobs.ProcessRunner:
    """One worker process at a time for the whole server: heavy work never runs inside the web server's own
    process, so it keeps answering (and passing Render's 5-second health check)."""
    return jobs.ProcessRunner()


FRONT_VIEWS = {
    "tail": ("尾盘选股", "Late-session"),
    "acc": ("主力暗中吸筹", "Morning accumulation"),
    "dip": ("低位吸筹", "Buy the dip"),
    "close": ("收盘选股", "After-close"),
    "long": ("长线", "Long-term"),
    "lab": ("策略实验室", "Strategy lab"),
}


def _other_view(view: str) -> None:
    """One screen per visit: only the chosen one loads its data."""
    from ui import screens_view

    if view == "acc":
        screens_view.accumulation_section()
    elif view == "dip":
        screens_view.dipbuy_section()
    elif view == "close":
        _close_screen()
    elif view == "long":
        screens_view.longterm_section()
    else:
        st.header(t("策略实验室", "Strategy lab"), anchor=False)
        from ui import lab_view

        lab_view.render()


def _app_link() -> None:
    """The website offers the Windows app (left out inside the app itself)."""
    if desktop():
        return
    st.markdown(f":material/desktop_windows: [{t('下载 Windows 桌面版', 'Download the Windows app')}]({APP_DOWNLOAD})"
                f" <small>{t('最新版本，自动更新', 'latest version, updates itself')}</small>",
                unsafe_allow_html=True)


def render(mkt: str) -> None:
    spec = checkpoint.SPECS[mkt]
    cp_name = {c: (f"{c} {t('收盘', 'close')}" if c == spec.close else c) for c in spec.checkpoints}

    if mkt == "CN":
        view = st.segmented_control(t("页面", "Screen"), list(FRONT_VIEWS), format_func=lambda k: t(*FRONT_VIEWS[k]),
                                    default="tail", key="front_view", required=True, label_visibility="collapsed")
        if view != "tail":
            _other_view(view)
            return
        st.header(t("尾盘选股", "Late-session picks"), anchor=False)
        P = screener.ScreenParams()
    else:
        st.header(t("尾盘选股 · 港股（实盘测试）", "Late-session picks · Hong Kong (live test)"), anchor=False)
        st.caption(t("把A股的选股流程（分时数据、检查点、条件、排名）原样用于港股，可作为A股检查点之前的预演。",
                     "A live run of the A-share screen on Hong Kong stocks - the same pipeline (minute data, "
                     "checkpoints, rules, ranking), useful as a pre-test before the A-share checkpoints."))
        variant = st.segmented_control(
            t("条件", "Rules"), ["same", "adapted"], default="same", key="hk_variant", required=True,
            format_func=lambda v: t("与A股相同", "Same rules as A-shares") if v == "same"
            else t("港股换手率（0.5–8%）", "HK-adapted turnover (0.5–8%)"),
            help=t("港股换手率远低于内地（腾讯每日约 0.3%），3% 的下限几乎排除所有股票。调整版只降低这一下限。",
                   "Hong Kong turnover rates are far lower than mainland ones (Tencent trades ~0.3% a day), so a "
                   "3% minimum removes almost every stock. The adapted version lowers only that floor."))
        P = screener.ScreenParams() if variant == "same" else HK_ADAPTED
    from ui.i18n import rule_labels

    rules = rule_labels(P, spec)
    _query_card(mkt, P)
    if mkt == "CN":
        _app_link()

    if sample.is_sample() and mkt != "CN":
        st.info(t("港股测试只使用实时数据。请在侧边栏打开 **实时数据**。",
                  "The Hong Kong test runs on live data only. Turn on **Live data** in the sidebar."),
                icon=":material/inventory_2:")
        return

    if not sample.is_sample():
        _auction_panel(mkt, spec)

    # ------------------------------------------------------------------ which session, which checkpoints
    from ui.i18n import plan_reason, session_note

    if sample.is_sample():
        saved = sample.picks()
        day = saved["day"] if saved else checkpoint.latest_session_day()
        note = t(f"**示例数据：{fday(day, long=True)}**——当天各检查点满足条件的股票。在侧边栏打开 **实时数据** 查看最新交易日。",
                 f"**Saved example: {pd.Timestamp(day):%A %d %B %Y}** - the stocks that matched as the market stood "
                 "at each checkpoint that day. Turn on **Live data** in the sidebar for the latest session.")
    else:
        day = checkpoint.latest_session_day(mkt)
        note = session_note(day, spec)
    plan = checkpoint.plan(day, spec)
    reached = tuple(x["checkpoint"] for x in plan if x["ready"])
    if note:
        st.info(note, icon=":material/event:")
    with st.container(horizontal=True):
        for x in plan:
            with st.container(border=True):
                badge = f":green-badge[{t('已出', 'Shown')}]" if x["ready"] else f":gray-badge[{t('未到', 'Not yet')}]"
                st.markdown(f"**{cp_name[x['checkpoint']]}** {badge}")
                st.caption(fday(day) if x["ready"] else plan_reason(x, spec))

    # ------------------------------------------------------------------ result: saved now, live in the background
    if sample.is_sample():
        res, job = sample.picks(), None
    else:
        res = checkpoint.load_result(day, P, spec) if reached else None
        recompute = st.button(t("重新计算", "Recompute"), icon=":material/refresh:", disabled=not reached,
                              help=t("忽略已保存的结果，重新用行情数据计算（在后台运行）。",
                                     "Rebuild from the market data again, ignoring saved results (runs in the "
                                     "background)."))
        job = None
        # After today's close the daily job computes the session once and publishes it: wait for that
        # (fixed figures, no recomputing per visit) unless it is overdue or the rules are customised.
        wait = res is None and not recompute and reached and P == screener.ScreenParams() and \
            published.await_close(mkt, day)
        if wait:
            _await_publish(mkt, day)
        elif reached and (res is None or recompute):
            job = job_runner().submit(frontpage_key(mkt, day, reached, P), "frontpage",
                                      {"mkt": mkt, "day": str(day), "params": P.to_dict(),
                                       "use_cache": not recompute}, force=recompute, front=True)
        if job is not None and (job.active or job.state == "failed"):
            _job_progress(job)
    shown = res or checkpoint.latest_saved(spec, P, on_or_before=day)
    if shown is None:
        if reached:
            st.info(t("还没有保存过任何交易日——第一次结果正在上方计算。",
                      "No session has been saved yet - the first one is being computed above."),
                    icon=":material/hourglass_top:")
        else:
            st.info(t(f"还没有到任何检查点，也没有之前保存的结果。名单将在北京时间 {spec.checkpoints[0]} 之后出现。",
                      f"No checkpoint has passed yet and no earlier session is saved. The list appears after "
                      f"{spec.checkpoints[0]} Beijing."), icon=":material/schedule:")
    else:
        if shown["day"] != day or tuple(shown["frames"]) != reached:
            cps_txt = ", ".join(shown["frames"])
            st.caption(t(f":material/history: 显示最近保存的结果：**{fday(shown['day'])}**，{cps_txt}。",
                         f":material/history: Showing the last saved result: **{fday(shown['day'])}** at {cps_txt}."))
        _result_block(shown, spec, P, rules, cp_name, mkt, key=f"{mkt}_current")

    _past_sessions(mkt, spec, P, rules, cp_name, exclude=shown["day"] if shown else None)


# ---------------------------------------------------------------------------------------------- pieces

def _query_card(mkt: str, P: screener.ScreenParams) -> None:  # noqa: N803
    from ui.i18n import lang

    turnover = f"{P.turnover_min:g}%–{P.turnover_max:g}%"
    with st.container(border=True):
        st.markdown(f"**{t('选股条件', 'The query')}**")
        st.markdown(QUERY[mkt][lang()].replace("{turnover}", turnover))
        st.caption(t(*TIMES[mkt]) + (t(" 阈值可在“选股器”页面修改。", " Edit the thresholds on the Screener page.")
                                     if mkt == "CN" else ""))


def frontpage_key(mkt: str, day: date, reached: tuple, p: screener.ScreenParams) -> tuple:
    """The job key shared by a visitor and the site's clock (same session, checkpoints and rules = one run)."""
    return ("frontpage", mkt, str(day), tuple(reached), repr(sorted(p.to_dict().items(), key=str)))


def _await_publish(mkt: str, day: date) -> None:
    when = "15:10" if mkt == "CN" else "16:20"
    st.info(t(f"已收盘。今日最终名单由每日更新计算一次（北京时间约 {when}），之后在此显示为固定结果，本页会自动刷新。"
              "在此之前显示最近保存的结果：",
              f"The market has closed. Today's final list is computed once by the daily update (about {when} "
              "Beijing) and then shown here as fixed figures; this page picks it up by itself. Meanwhile, the latest "
              "saved result:"), icon=":material/lock_clock:")

    @st.fragment(run_every="60s")
    def poll() -> None:
        published.sync()
        if day in archive.sessions(mkt):
            st.rerun()

    poll()


SCREEN_AT = time(16, 0)        # the after-close screen runs from 16:00 Beijing


def _close_screen() -> None:
    """收盘选股: the 16:00 after-close screen - fixed figures, computed once a day by the daily update."""
    from stockrt import closescreen

    st.subheader(t("收盘选股（16:00）", "After-close screen (16:00)"), anchor=False)
    with st.container(border=True):
        st.markdown(f"**{closescreen.QUERY_ZH if t('zh', 'en') == 'zh' else closescreen.QUERY_EN}**")
        st.caption(t("每个交易日北京时间 16:00 起用当日收盘数据计算一次，之后显示为固定结果。",
                     "Computed once a day from 16:00 Beijing (04:00 in New York during daylight saving) on the "
                     "session's closing data, then shown as fixed figures."))
    if sample.is_sample():
        st.caption(t("只使用实时数据——请在侧边栏打开 **实时数据**。", "Runs on live data only - turn on **Live data** in the sidebar."))
        return
    now = now_bj()
    session = checkpoint.latest_session_day("CN")
    days = archive.screen_days()
    if session not in days:
        if session == now.date() and now.time() < SCREEN_AT:
            mins = (SCREEN_AT.hour * 60 - now.hour * 60 - now.minute)
            st.caption(t(f":material/schedule: 今日名单将在北京时间 16:00 出现（还有{mins // 60}小时{mins % 60}分钟）。"
                         + ("先显示最近保存的交易日。" if days else ""),
                         f":material/schedule: Today's list comes at 16:00 Beijing (in {mins // 60} h {mins % 60} "
                         "min)." + (" Showing the latest saved session." if days else "")))
        else:  # computed right away (the GitHub job starts hours late; its later copy is identical)
            job = job_runner().submit(("close_screen", str(session)), "close_screen",
                                      {"day": str(session), "workers": checkpoint.WORKERS}, front=True)
            if job.active or job.state == "failed":
                _job_progress(job, t("收盘选股 · 正在计算", "After-close screen · computing"),
                              t("成交量、压力位与均线", "volume, resistance and moving averages"), typical=40)
            days = archive.screen_days()
    if not days:
        st.info(t("还没有收盘选股结果。第一份将在交易日北京时间 16:00 之后出现。",
                  "No after-close list saved yet. The first one appears after 16:00 Beijing on a trading day."),
                icon=":material/schedule:")
        return
    with st.container(horizontal=True, vertical_alignment="bottom"):
        pick = st.selectbox(t("交易日", "Session"), days, format_func=lambda d: fday(d, long=True),
                            key="close_screen_day")
        res_n = st.segmented_control(t("压力位", "Resistance"), list(closescreen.RESISTANCE), default=20,
                                     format_func=lambda n: t(f"{n}日最高", f"{n}-session high"), key="close_screen_res",
                                     required=True)
        ma_n = st.segmented_control(t("牛熊线", "Bull-bear line"), list(closescreen.BULL_BEAR), default=250,
                                    format_func=lambda n: f"MA{n}", key="close_screen_ma", required=True)
    got = archive.load_screen(pick)
    if got is None or got[0].empty:
        st.caption(t("当日没有股票通过收盘初筛。", "Nothing passed the end-of-day shortlist that session."))
        return
    frame, meta = got
    d = closescreen.apply(frame, res_n, ma_n)
    hits = d[d["passes"]]
    n_short = meta.get("shortlisted", len(frame))
    st.markdown(t(f"{fday(pick)}：**{len(hits)} 只股票**满足全部 5 个条件（按成交量和换手率初筛 {n_short} 只）。",
                  f"**{len(hits)} stocks** met all 5 conditions on {fday(pick)} ({n_short} shortlisted on volume and "
                  "turnover)."))
    show = hits if not hits.empty else d[d["rules_met"] == 4].head(15)
    if hits.empty:
        st.caption(t("没有股票满足全部条件——显示只差一个条件的股票。",
                     "Nothing met every condition - showing stocks one condition short."))
    cols = ["code", "name", "close", "pct_change", "vol_x", "turnover_rate", "above_resistance_pct", "above_ma_pct",
            "float_mcap", "missed"]
    st.dataframe(style_signed(show[cols], ["pct_change"]), hide_index=True, column_config={
        "code": st.column_config.TextColumn(t("代码", "Code")), "name": st.column_config.TextColumn(t("名称", "Name")),
        "close": st.column_config.NumberColumn(t("收盘价", "Close"), format="%.2f"),
        "pct_change": st.column_config.NumberColumn(t("涨幅", "Change"), format="%+.2f%%"),
        "vol_x": st.column_config.NumberColumn(t("成交量倍数", "Volume × prev"), format="%.1f×"),
        "turnover_rate": st.column_config.NumberColumn(t("换手率", "Turnover"), format="%.2f%%"),
        "above_resistance_pct": st.column_config.NumberColumn(t("高于压力位", "Above resistance"), format="%+.2f%%"),
        "above_ma_pct": st.column_config.NumberColumn(t(f"高于MA{ma_n}", f"Above MA{ma_n}"), format="%+.2f%%"),
        "float_mcap": st.column_config.NumberColumn(t("流通市值", "Float cap"), format="compact"),
        "missed": st.column_config.TextColumn(t("未满足", "Missed"))})
    st.download_button(t("收盘选股（CSV）", "After-close screen (CSV)"), to_csv_bytes(show[cols]),
                       file_name=f"close_screen_{pick}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    with st.expander(t("每个条件如何判断", "How each condition is read"), icon=":material/help:"):
        from ui.i18n import close_rules

        st.table(pd.DataFrame([{t("条件", "Condition"): lbl, t("判断方法", "Reading"): desc}
                               for lbl, desc in close_rules(res_n, ma_n)]), hide_index=True)
        st.caption(t("价格为前复权，分红不会造成假突破。昨日一字板的股票，昨收即最高价，因此今日站上即算突破，且其昨日成交量极小，"
                     "今日成交量倍数会显得很大。仅供研究，不构成投资建议。",
                     "Prices are forward-adjusted, so dividends never fake a breakout. A stock locked at the limit all "
                     "day yesterday (一字板) has yesterday's close as its high, so today's move above it counts as a "
                     "breakout, and its tiny volume makes today's look many times larger. Research tooling, not "
                     "investment advice."))


def _job_progress(job: jobs.Job, what_zh: str = "", what_en: str = "", typical: float = 90) -> None:
    from ui.progress import job_card

    what_zh = what_zh or t("尾盘选股 · 正在重建检查点", "Late-session picks · rebuilding checkpoints")
    if not what_en and job.key[0] == "frontpage":
        what_en = t(f"用分时数据重建 {job.key[2]} 的 {'、'.join(job.key[3])}",
                    f"rebuilding {job.key[2]} at {', '.join(job.key[3])} from minute data")
    job_card(job, what_zh, what_en, typical=typical)


def _auction_panel(mkt: str, spec: checkpoint.MarketSpec) -> None:
    """Pre-open: indicative auction prices, live. After the open: the auction result and research queries,
    loaded only when opened."""
    s = status(mkt)
    if s.phase == "pre-open":
        @st.fragment(run_every="20s")
        def live() -> None:
            _auction_view(mkt, spec, pre_open=True)

        with st.container(border=True):
            st.subheader(t("集合竞价", "Pre-open auction"), anchor=False)
            st.caption(t(*AUCTION[mkt]))
            live()
            if mkt == "CN":
                _auction_research(spec)
    elif mkt == "CN":
        day = checkpoint.latest_session_day(mkt)
        when = t("今日", "today") if day == now_bj().date() else fday(day)
        exp = st.expander(t(f"集合竞价与成交量查询 · {when}", f"Opening auction & volume queries · {when}"),
                          icon=":material/gavel:", on_change="rerun")
        if exp.open:
            with exp:
                view = st.segmented_control(
                    t("查看", "View"), ["overview", "volume", "screen"], default="overview", key="auction_view",
                    required=True, format_func=_auction_view_name)
                if view == "overview":
                    _auction_view(mkt, spec, pre_open=False)
                else:
                    _auction_research(spec, view, day)
    elif s.phase in ("morning", "lunch", "afternoon", "closing auction", "closed") and \
            checkpoint.latest_session_day(mkt) == now_bj().date():
        exp = st.expander(t("今日集合竞价", "Today's opening auction"), icon=":material/gavel:", on_change="rerun")
        if exp.open:
            with exp:
                _auction_view(mkt, spec, pre_open=False)


def _auction_view_name(v: str) -> str:
    return {"overview": t("概览", "Overview"), "volume": t("成交量 ≥ 昨日 N 倍", "Volume ≥ N× yesterday"),
            "screen": t("竞价选股", "Auction screen")}[v]


@st.cache_resource
def auction_volume_memo() -> dict:
    """Each day's auction volumes per stock (fixed once the auction has matched), shared by every visitor."""
    return {}


def _auction_research(spec: checkpoint.MarketSpec, view: str | None = None, day: date | None = None) -> None:
    """The volume queries and the 竞价选股 screen (A-shares) for one session (default: today)."""
    from ui import cache  # loaded only when these queries are opened

    today = day or now_bj().date()
    got = archive.load_volumes("CN", before=today)
    if got is None:
        st.info(t("还没有上一交易日的成交量数据。每次收盘后会自动保存，也可用 "
                  "`python scripts/build_volume_book.py --date YYYY-MM-DD` 补建。",
                  "No volume book for the previous session yet. It is saved automatically after each close, or "
                  "backfill one with `python scripts/build_volume_book.py --date YYYY-MM-DD`."),
                icon=":material/info:")
        return
    yday, book = got
    from stockrt import tradingdays

    expected = tradingdays.previous_trading_day("CN", today)
    when = fday(yday)
    if expected and yday != expected:
        st.warning(t(f"最新保存的成交量数据是 {when}，不是上一交易日（{fday(expected)}）。比较基于 {when}。",
                     f"The newest saved volume book is for {when}, not the previous session ({fday(expected)}). "
                     f"Comparisons use {when}."), icon=":material/warning:")
    if view is None:  # pre-open: offer both queries in a compact switch
        view = st.segmented_control(t("查询", "Query"), ["volume", "screen"], default="screen",
                                    key="auction_query_pre", required=True, format_func=_auction_view_name)
    snap, ts = (cache.snapshot_live if status("CN").phase == "pre-open" else cache.snapshot)("CN")
    snap = snap[pd.to_datetime(snap["time"]).dt.date == today]
    if snap.empty:
        st.caption(t("今日还没有行情。", "No quotes for today yet."))
        return

    if view == "volume":
        with st.container(horizontal=True, vertical_alignment="bottom"):
            mult = st.number_input(t("今日成交量至少为昨日的 × 倍", "Volume today at least × yesterday's"), min_value=1.0,
                                   max_value=100.0, value=5.0, step=0.5, key="surge_mult")
            min_amt = st.number_input(t("今日成交额 ≥（百万元）", "Value traded today ≥ ¥ million"), min_value=0.0,
                                      value=20.0, step=10.0, key="surge_amt",
                                      help=t("排除成交量小、波动大的冷门股。",
                                             "Skips illiquid names whose small volumes swing wildly."))
            ex_st = st.toggle(t("排除ST", "Exclude ST"), value=True, key="surge_st")
        d = opening.volume_surge(snap, book, mult, min_amt * 1e6, ex_st)
        st.markdown(t(f"**{len(d)} 只股票**今日成交量已达到 {when} 全天成交量的 {mult:g} 倍以上（快照：北京时间 {ts}）。"
                      "开盘初期满足的很少，名单会随交易时间增加。",
                      f"**{len(d)} stocks** have traded at least {mult:g}× their whole-day volume of {when} "
                      f"(snapshot {ts} Beijing). Early in the session few qualify; the list grows through the day."))
        cols = ["code", "name", "price", "pct_change", "vol_multiple", "volume", "yday_volume", "amount",
                "turnover_rate", "float_mcap", "yday_pct"]
        st.dataframe(style_signed(d[cols], ["pct_change", "yday_pct"]), hide_index=True, column_config={
            "code": st.column_config.TextColumn(t("代码", "Code")),
            "name": st.column_config.TextColumn(t("名称", "Name")),
            "price": st.column_config.NumberColumn(t("现价", "Price"), format="%.2f"),
            "pct_change": st.column_config.NumberColumn(t("涨幅", "Change"), format="%+.2f%%"),
            "vol_multiple": st.column_config.NumberColumn(f"× {when}", format="%.1f×"),
            "volume": st.column_config.NumberColumn(t("今日成交量", "Volume today"), format="compact"),
            "yday_volume": st.column_config.NumberColumn(t(f"{when} 成交量", f"Volume {when}"), format="compact"),
            "amount": st.column_config.NumberColumn(t("今日成交额", "Value today"), format="compact"),
            "turnover_rate": st.column_config.NumberColumn(t("换手率", "Turnover"), format="%.2f%%"),
            "float_mcap": st.column_config.NumberColumn(t("流通市值", "Float cap"), format="compact"),
            "yday_pct": st.column_config.NumberColumn(t(f"{when} 涨幅", f"Change {when}"), format="%+.2f%%")})
        st.download_button(t("放量名单（CSV）", "Volume surge list (CSV)"), to_csv_bytes(d[cols]),
                           file_name=f"volume_surge_{today}.csv", mime="text/csv", on_click="ignore",
                           icon=":material/download:")
        return

    # ---- 竞价选股 auction screen
    now_t = now_bj().time()
    phase = "indicative" if now_t < time(9, 25) else ("result" if now_t < time(9, 30) else "after_open")
    if today != now_bj().date():
        phase = "after_open"
    with st.form("auction_form", border=True):
        st.markdown(t("**竞价选股**——每个条件都可以关闭（留空即可）",
                      "**Auction screen** - every condition can be switched off (leave a box empty)"))
        c1, c2, c3 = st.columns(3)
        with c1:
            g_lo = st.number_input(t("高开幅度下限 %", "Gap from %"), value=2.0, step=0.5, key="a_gap_lo")
            g_hi = st.number_input(t("高开幅度上限 %", "Gap to %"), value=7.0, step=0.5, key="a_gap_hi")
            amt = st.number_input(t("竞价成交额 ≥（百万元）", "Auction value ≥ ¥ million"), value=10.0, step=5.0,
                                  key="a_amt")
        with c2:
            vs = st.number_input(t("竞价量 ≥ 昨日成交量的 %", "Auction volume ≥ % of yesterday"), value=5.0, step=1.0,
                                 key="a_vs")
            to = st.number_input(t("竞价换手率 ≥ %", "Auction turnover ≥ %"), value=0.3, step=0.1, key="a_to")
            cap_lo = st.number_input(t("流通市值下限（十亿元）", "Float cap from ¥ bn"), value=3.0, step=1.0,
                                     key="a_cap_lo")
            cap_hi = st.number_input(t("流通市值上限（十亿元）", "Float cap to ¥ bn"), value=50.0, step=5.0,
                                     key="a_cap_hi")
        with c3:
            ex_st = st.toggle(t("排除ST", "Exclude ST"), value=True, key="a_st")
            no_lim = st.toggle(t("排除一字涨停开盘", "Skip limit-up opens (一字板)"), value=True, key="a_lim")
            y_up = st.toggle(t("昨日收涨", "Yesterday closed up"), value=False, key="a_yup")
            y_lim = st.toggle(t("昨日涨停（接力）", "Yesterday limit-up (接力)"), value=False, key="a_ylim")
            hold = st.toggle(t("09:30 后仍维持高开", "Gap still holding (after 09:30)"), value=False, key="a_hold")
        st.form_submit_button(t("运行竞价选股", "Run auction screen"), icon=":material/filter_alt:", type="primary")
    p = opening.AuctionParams(exclude_st=ex_st, gap_min=g_lo, gap_max=g_hi, skip_limit_up_open=no_lim,
                              amount_min_m=amt, vs_yday_min_pct=vs, turnover_min=to, cap_min_bn=cap_lo,
                              cap_max_bn=cap_hi, yday_up=y_up, yday_limit_up=y_lim, holding=hold)
    d = opening.auction_frame(snap, book, phase)
    after_open = phase == "after_open"
    if after_open:
        short = d[opening.cheap_mask(d, p)]
        memo = auction_volume_memo().setdefault(str(today), {})
        missing = [c for c in short["code"] if c not in memo]
        if missing:
            bar = st.progress(0.0, text=t("正在读取竞价成交量…", "Reading auction volumes…"))
            filled = opening.fill_auction_volumes(short[short["code"].isin(missing)], missing,
                                                  progress=lambda f, m: bar.progress(f, text=m))
            bar.empty()
            for r in filled.itertuples():
                memo[r.code] = (r.auction_volume, r.auction_amount)
        d = short.copy()
        d["auction_volume"] = d["code"].map(lambda c: memo.get(c, (None, None))[0])
        d["auction_amount"] = d["code"].map(lambda c: memo.get(c, (None, None))[1])
        d = d.astype({"auction_volume": float, "auction_amount": float})
    res = opening.screen(d, p, after_open)
    active = opening.active_rules(p, after_open)
    passed = res[res["passes"]]
    label = {"indicative": t("参考价（竞价进行中）", "indicative (auction still running)"),
             "result": t("09:25 竞价撮合结果", "matched auction (09:25)"),
             "after_open": t("取每只股票 09:30 的第一根分时", "auction from each stock's 09:30 bar")}[phase]
    st.markdown(t(f"**{len(passed)} 只股票满足全部 {len(active)} 个条件** · {label} · 对比 {when} · 快照 {ts}",
                  f"**{len(passed)} stocks pass all {len(active)} conditions** · {label} · compared with {when} · "
                  f"snapshot {ts} Beijing"))
    show = passed if not passed.empty else res[res["rules_met"] >= len(active) - 1].head(20)
    if passed.empty:
        st.caption(t("没有股票满足全部条件——显示只差一个条件的股票。",
                     "Nothing passes every condition - showing stocks one condition short."))
    cols = ["code", "name", "auction_price", "gap_pct", "auction_amount", "auction_vs_yday", "auction_turnover",
            "float_mcap", "yday_pct", "price", "pct_change", "rules_met", "missed"]
    st.dataframe(style_signed(show[cols], ["gap_pct", "yday_pct", "pct_change"]), hide_index=True, column_config={
        "code": st.column_config.TextColumn(t("代码", "Code")), "name": st.column_config.TextColumn(t("名称", "Name")),
        "auction_price": st.column_config.NumberColumn(t("竞价价格", "Auction price"), format="%.2f"),
        "gap_pct": st.column_config.NumberColumn(t("高开幅度", "Gap"), format="%+.2f%%"),
        "auction_amount": st.column_config.NumberColumn(t("竞价成交额", "Auction value"), format="compact"),
        "auction_vs_yday": st.column_config.NumberColumn(t(f"竞价量占{when}%", f"Auction vol. % of {when}"),
                                                         format="%.1f%%"),
        "auction_turnover": st.column_config.NumberColumn(t("竞价换手率", "Auction turnover"), format="%.2f%%"),
        "float_mcap": st.column_config.NumberColumn(t("流通市值", "Float cap"), format="compact"),
        "yday_pct": st.column_config.NumberColumn(t(f"{when} 涨幅", f"Change {when}"), format="%+.2f%%"),
        "price": st.column_config.NumberColumn(t("现价", "Price now"), format="%.2f"),
        "pct_change": st.column_config.NumberColumn(t("当前涨幅", "Change now"), format="%+.2f%%"),
        "rules_met": st.column_config.NumberColumn(t("满足条件", "Conditions met"), format=f"%d/{len(active)}"),
        "missed": st.column_config.TextColumn(t("未满足", "Missed"))})
    st.download_button(t("竞价选股（CSV）", "Auction screen (CSV)"), to_csv_bytes(show[cols]),
                       file_name=f"auction_screen_{today}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    with st.expander(t("为什么是这些条件", "Why these conditions"), icon=":material/help:"):
        from ui.i18n import auction_rules

        st.table(pd.DataFrame([{t("条件", "Condition"): lbl, t("意义", "Why it matters"): why}
                               for lbl, why in auction_rules(active, p)]), hide_index=True)
        st.caption(t("仅供研究，不构成投资建议。竞价信号持续时间很短：操作前先看 09:30 后几分钟（高开能否维持、成交量是否跟上）。",
                     "Research tooling, not investment advice. Auction signals are short-lived: check the first "
                     "minutes after 09:30 (does the gap hold, does volume follow through) before acting on any of "
                     "them."))


def _auction_view(mkt: str, spec: checkpoint.MarketSpec, pre_open: bool) -> None:
    from ui import cache  # loaded only when the auction panel is shown
    from ui.i18n import INDEX_ZH

    snap, ts = (cache.snapshot_live if pre_open else cache.snapshot)(mkt)
    today = now_bj().date()
    fresh = snap[pd.to_datetime(snap["time"]).dt.date == today] if "time" in snap else snap
    if fresh.empty:
        start = "09:15" if mkt == "CN" else "09:00"
        st.caption(t(f"今日还没有竞价价格——竞价开始后（北京时间 {start}）出现。",
                     f"No auction prices yet for today - they appear once the auction starts ({start} Beijing)."))
        return
    d = auction.indicative(fresh) if pre_open else auction.opening(fresh)
    b = auction.breadth(d)
    idx = cache.quotes((spec.index,))
    idx_gap = None
    if not idx.empty:
        i = auction.indicative(idx) if pre_open else auction.opening(idx)
        idx_gap = i["gap_pct"].iloc[0] if not i.empty else None
        if pre_open and (idx["bid1"].fillna(0).iloc[0] == 0):
            idx_gap = None  # indices don't print during the auction - their price stays at the previous close
    index_name = t(INDEX_ZH.get(spec.index_name, spec.index_name), spec.index_name)
    with st.container(horizontal=True):
        st.metric(t(f"{index_name} · {'参考' if pre_open else '开盘'}高开幅度",
                    f"{index_name} · {'indicative' if pre_open else 'opening'} gap"),
                  pct(idx_gap, already_pct=True), border=True,
                  help=t("指数在竞价撮合后才有价格（A股为 09:25）。", "Indices only print once the auction matches "
                         "(09:25 for A-shares).") if pre_open else None)
        st.metric(t("高开 / 低开", "Gapping up / down"), f"{b.get('up', 0):,} / {b.get('down', 0):,}", border=True)
        st.metric(t("高开幅度中位数", "Median gap"), pct(b.get("median"), already_pct=True), border=True)
        st.metric(t("高开或低开 3% 以上", "Gaps of 3% or more"), f"↑ {b.get('up_3', 0)} · ↓ {b.get('down_3', 0)}",
                  border=True)
    cols = ["code", "name", "prev_close", "indicative" if pre_open else "open", "gap_pct", "volume", "time"]
    cfg = {"code": st.column_config.TextColumn(t("代码", "Code")), "name": st.column_config.TextColumn(t("名称", "Name")),
           "prev_close": st.column_config.NumberColumn(t("昨收", "Prev close"), format="%.2f"),
           "indicative": st.column_config.NumberColumn(t("参考价", "Indicative"), format="%.2f"),
           "open": st.column_config.NumberColumn(t("开盘价", "Open"), format="%.2f"),
           "gap_pct": st.column_config.NumberColumn(t("高开幅度", "Gap"), format="%+.2f%%"),
           "volume": st.column_config.NumberColumn(t("成交量", "Volume"), format="compact"),
           "time": st.column_config.DatetimeColumn(t("行情时间", "Quote time"), format="HH:mm:ss")}
    wl = [c for c in st.session_state.get("watchlist", []) if market(c) == mkt]
    left, right = st.columns(2)
    with left:
        st.markdown(f"**{t('自选股', 'Your watchlist')}**")
        w = d[d["code"].isin(wl)]
        if w.empty:
            st.caption(t("自选股中没有该市场的股票。", "No watchlist stocks in this market."))
        else:
            st.dataframe(style_signed(w[cols], ["gap_pct"]), hide_index=True, column_config=cfg)
    with right:
        up, down = auction.movers(d, 8, min_value=2e9)
        st.markdown(t("**高开 / 低开最多**（流通市值 20 亿以上）", "**Biggest gaps** (float cap over 2 bn)"))
        st.dataframe(style_signed(pd.concat([up, down])[cols], ["gap_pct"]), hide_index=True, column_config=cfg)
    note = t("港股开市前报价稀少，且约延迟 15 分钟。", " Hong Kong pre-open quotes are sparse and about 15 minutes stale.") \
        if mkt == "HK" and pre_open else ""
    st.caption(t(f"快照时间：北京时间 {ts}" + ("；开盘前每 20 秒刷新。" if pre_open else "。"),
                 f"Snapshot taken {ts} Beijing" + ("; refreshes every 20 s before the open." if pre_open else "."))
               + note)


def _past_sessions(mkt: str, spec: checkpoint.MarketSpec, P, rules, cp_name, exclude: date | None) -> None:  # noqa: N803
    exp = st.expander(t("历史交易日", "Past sessions"), icon=":material/history:", on_change="rerun")
    if not exp.open:
        return
    with exp:
        days = archive.sessions(mkt) if P == screener.ScreenParams() else []
        if not days:
            st.caption(t("还没有保存过往交易日。每个交易日结束后自动添加。",
                         "No earlier sessions saved yet. Each finished session is added automatically."))
            return
        weeks: dict[date, list[date]] = {}
        for d in days:
            weeks.setdefault(d - timedelta(days=d.weekday()), []).append(d)

        def week_label(monday: date) -> str:
            friday = monday + timedelta(days=4)
            n = len(weeks[monday])
            return t(f"{monday.month}月{monday.day}日 – {friday.month}月{friday.day}日 {friday.year} · {n} 个交易日",
                     f"{monday:%d %b} – {friday:%d %b %Y} · {n} sessions")

        with st.container(horizontal=True, vertical_alignment="bottom"):
            week = st.selectbox(t("周", "Week"), list(weeks), format_func=week_label, key=f"past_week_{mkt}")
            pick = st.segmented_control(t("日", "Day"), sorted(weeks[week]), default=max(weeks[week]), required=True,
                                        format_func=fwd, key=f"past_day_{mkt}_{week}")
        missing = [week + timedelta(days=i) for i in range(5) if week + timedelta(days=i) not in weeks[week]
                   and week + timedelta(days=i) < now_bj().date()]
        if missing:
            gone = "、".join(fwd(d) for d in missing) if t("zh", "en") == "zh" else ", ".join(fwd(d) for d in missing)
            st.caption(t(f"已保存 {len(days)} 个交易日。本周缺少：{gone}（休市，或当天没有保存结果）。",
                         f"{len(days)} sessions saved. Not in this week: {gone} (market closed, or no result was "
                         "saved)."))
        else:
            st.caption(t(f"已保存 {len(days)} 个交易日，每次收盘后新增一个。",
                         f"{len(days)} sessions saved. A new one is added after each close."))
        res = archive.load(mkt, pick)
        if res:
            if res.get("compact"):
                st.caption(t(f":material/compress: 较早的交易日只保留排名和最接近的股票；最近 {archive.KEEP_SESSIONS} "
                             "个交易日保留全部候选股。",
                             ":material/compress: Older session: the ranked list and the closest stocks are kept; the "
                             f"full candidate list is kept for the newest {archive.KEEP_SESSIONS} sessions."))
            _result_block(res, spec, P, rules, cp_name, mkt, key=f"{mkt}_past")


def _result_block(res: dict, spec, P, rules, cp_name, mkt: str, key: str) -> None:  # noqa: N803
    from ui.i18n import missed as missed_text
    from ui.i18n import note as note_text

    frames = res["frames"]
    if not frames:
        st.warning(t("该交易日没有能够重建的股票。", "No stock could be rebuilt for this session. ")
                   + " ".join(note_text(n) for n in res.get("notes", [])), icon=":material/warning:")
        return
    ranked = checkpoint.ranked(res, P, spec=spec)
    cps = [c for c in spec.checkpoints if c in frames]
    index_short = t("沪深300", "CSI 300") if mkt == "CN" else t("恒指", "HSI")
    with st.container(horizontal=True):
        st.metric(t("交易日", "Session"), fday(res["day"], long=True), border=True)
        for c in cps:
            idx = res["index_pct"].get(c)
            st.metric(t(f"{cp_name[c]} 满足全部 {len(rules)} 条", f"Met all {len(rules)} at {cp_name[c]}"),
                      f"{int(frames[c]['passes'].sum())}",
                      f"{index_short} {pct(idx, already_pct=True)}" if idx is not None else None,
                      delta_color="off", border=True,
                      help=t(f"{c} 共重建 {len(frames[c])} 只股票。", f"Out of {len(frames[c])} stocks rebuilt at {c}."))
    for n in res.get("notes", []):
        st.caption(f":material/info: {note_text(n)}")

    latest = cps[-1]
    if frames[latest].empty:
        st.info(t("当日没有股票通过第一轮筛选，因此没有名单。", "No stock passed the first screen this session, so there is "
                  "no list."), icon=":material/search_off:")
        return
    st.subheader(t(f"排名 · {fday(res['day'])}", f"Ranked list · {fday(res['day'])}"), anchor=False)
    if ranked.empty:
        st.warning(t(f"{'、'.join(cps)} 没有股票满足全部条件，也没有只差一点的股票。{latest} 最接近的：",
                     f"No stock met every rule - or came within one narrowly-missed rule - at {', '.join(cps)}. "
                     f"Closest at {latest}:"), icon=":material/search_off:")
        show = frames[latest].head(15).copy()
        show.insert(0, "rank", range(1, len(show) + 1))
        show["tier"] = "-"
        show["confidence"] = show["flow_at"].map({"end of day": "Medium"}).fillna("High")
        if not res.get("flow_checked", True):
            show["confidence"] = "Low"
    else:
        show = ranked.copy()

    show["tier_label"] = show["tier"].map(tier_text).fillna("-")
    show["rules"] = show["rules_met"].astype(int).astype(str) + f"/{len(rules)}"
    show["confidence"] = show["confidence"].map(
        lambda c: {"High": t("高", "High"), "Medium": t("中", "Medium"), "Low": t("低", "Low")}.get(c, c))
    if "missed" in show:
        show["missed"] = show["missed"].map(lambda s: missed_text(s, P, spec))
    first_label = t("收盘", "close") if "after_first_label" in show and \
        (show["after_first_label"] == "to close").any() else t("现在", "now")
    cp_cols = [f"at_{c}" for c in cps if f"at_{c}" in show]
    cols = ["rank", "tier_label", "code", "name", "score", "rules", *cp_cols, "missed", "pct_change", "volume_ratio",
            "turnover_rate", "float_mcap", "cross_days_ago", "main_net", "confidence", "after_first"]
    view = show[[c for c in cols if c in show.columns]]
    tiers = " · ".join(tier_text(k) for k in TIERS)
    event = st.dataframe(
        style_signed(view, ["pct_change", "main_net", "after_first"]),
        hide_index=True, on_select="rerun", selection_mode="single-row", key=f"picks_table_{key}",
        column_config={
            "rank": st.column_config.NumberColumn("#", width="small"),
            "tier_label": st.column_config.TextColumn(
                t("等级", "Tier"), help=t("A 最可靠：每个检查点都满足全部条件。", "A is most reliable: every rule held at "
                                         "every checkpoint. ") + tiers),
            "code": st.column_config.TextColumn(t("代码", "Code")),
            "name": st.column_config.TextColumn(t("名称", "Name")),
            "score": st.column_config.ProgressColumn(
                t("信号分", "Signal score"), min_value=0, max_value=100, format="%.0f",
                help=t("最新检查点的 0-100 分：趋势 25%，成交量 20%，主力资金 20%，分时形态 20%，相对强度 10%，流动性 5%。",
                       "0-100 at the latest checkpoint: trend 25%, volume 20%, main funds 20%, intraday pattern 20%, "
                       "relative strength 10%, liquidity 5%.")),
            "rules": st.column_config.TextColumn(t("满足条件", "Rules met"), width="small",
                                                 help=t(f"{latest} 时", f"At {latest}")),
            **{f"at_{c}": st.column_config.TextColumn(c, width="small",
                                                      help=t(f"✓ = {c} 满足全部条件", f"✓ = every rule met at {c}"))
               for c in cps},
            "missed": st.column_config.TextColumn(t("未满足", "Missed")),
            "pct_change": st.column_config.NumberColumn(t(f"{latest} 涨幅", f"Change at {latest}"), format="%.2f%%"),
            "volume_ratio": st.column_config.NumberColumn(t("量比", "Vol ratio"), format="%.2f"),
            "turnover_rate": st.column_config.NumberColumn(t("换手率", "Turnover"), format="%.2f%%"),
            "float_mcap": st.column_config.NumberColumn(t("流通市值", "Float cap"), format="compact"),
            "cross_days_ago": st.column_config.NumberColumn(t("均线金叉（几天前）", "MA cross (days ago)"), format="%d"),
            "main_net": st.column_config.NumberColumn(t("主力净流入", "Main net inflow"), format="compact"),
            "confidence": st.column_config.TextColumn(
                t("可信度", "Confidence"),
                help=t("高：所有数据都在检查点当时测得。中：主力资金用的是当日收盘数据。低：无法获取资金数据，未检查该条件。",
                       "High: every input measured at the checkpoint. Medium: main-fund flow is the session's "
                       "end-of-day figure. Low: no money-flow source was reachable, so that rule was not checked.")),
            "after_first": st.column_config.NumberColumn(
                f"{cps[0]} → {first_label}", format="percent",
                help=t("第一个检查点之后的走势（事后数据），不参与排名。",
                       "What happened after the first checkpoint (hindsight). Not used for ranking.")),
        })
    sel = event.selection.rows if event else []
    if sel:
        code = view.iloc[sel[0]]["code"]
        with st.container(horizontal=True):
            if st.button(t(f"在实时看板打开 {code}", f"Open {code} on the Live dashboard"), icon=":material/monitoring:",
                         key=f"open_{key}"):
                st.session_state.live_code = code
                st.switch_page("app_pages/live.py")
            if st.button(t("加入自选股", "Add to watchlist"), icon=":material/playlist_add:", key=f"wl_{key}"):
                if code not in st.session_state.watchlist:
                    st.session_state.watchlist.append(code)
                    watchlist.save(st.session_state.watchlist)
                st.toast(t(f"{code} 已加入自选股", f"{code} added to the watchlist"))
    st.caption(t("排名：先按等级（多个检查点都出现的信号比只出现一次的可靠），再按信号分。点选一行可打开该股票。",
                 "Ranking: tier first (a signal that held across checkpoints is more reliable than one seen once), "
                 "then signal score. Select a row to open it."))

    from ui.i18n import lang

    with st.container(horizontal=True):
        st.download_button(t("排名名单（CSV）", "Ranked list (CSV)"),
                           to_csv_bytes(show.drop(columns=["tier_label"], errors="ignore")),
                           file_name=f"picks_{mkt.lower()}_{res['day']}_{stamp()}.csv", mime="text/csv",
                           on_click="ignore", icon=":material/download:", type="primary", key=f"dl_{key}")
        with st.popover(t("AI 提示词", "AI prompt"), icon=":material/content_copy:"):
            st.code(prompts.checkpoint_review(checkpoint_rules(P, spec), str(res["day"]), res["index_pct"], show,
                                              lang(), market=mkt, checkpoints=spec.checkpoints),
                    language=None, wrap_lines=True, height=340)

    exp = st.expander(t("每个检查点的每条条件，以及排名方法", "Every rule at every checkpoint, and how the ranking works"),
                      icon=":material/rule:", on_change="rerun", key=f"rules_exp_{key}")
    if exp.open:
        with exp:
            labels = dict(rules)
            pick = st.segmented_control(t("检查点", "Checkpoint"), list(reversed(cps)), default=latest,
                                        key=f"picks_detail_cp_{key}", required=True,
                                        format_func=lambda c: cp_name[c])
            f = frames[pick]
            near = f[f["rules_met"] >= len(rules) - 2]
            st.caption(t(f"{pick} 时有 {len(near)} 只股票至少满足 {len(rules)} 条中的 {len(rules) - 2} 条"
                         f"（共重建 {len(f)} 只）。✓ = 满足。",
                         f"{len(near)} stocks met at least {len(rules) - 2} of {len(rules)} rules at {pick} "
                         f"(of {len(f)} rebuilt). ✓ = rule met."))
            grid = near[["code", "name", "score"] + list(labels)].rename(columns=labels)
            st.dataframe(grid, hide_index=True, column_config={
                "code": st.column_config.TextColumn(t("代码", "Code")),
                "name": st.column_config.TextColumn(t("名称", "Name")),
                "score": st.column_config.ProgressColumn(t("分数", "Score"), min_value=0, max_value=100, format="%.0f"),
                **{lbl: st.column_config.CheckboxColumn(lbl, width="small") for lbl in labels.values()}})
            st.download_button(t("每个检查点重建的全部股票（CSV）", "Every stock rebuilt at each checkpoint (CSV)"),
                               to_csv_bytes(pd.concat([fr.assign(checkpoint=c) for c, fr in frames.items()],
                                                      ignore_index=True)),
                               file_name=f"checkpoints_{mkt.lower()}_{res['day']}_{stamp()}.csv", mime="text/csv",
                               on_click="ignore", icon=":material/download:", key=f"dl_all_{key}")
            st.markdown(explainer(spec))


def checkpoint_rules(p: screener.ScreenParams, spec: checkpoint.MarketSpec) -> list[str]:
    """The rules for prompts, in the visitor's language, with the market's own index and currency."""
    from ui.i18n import rule_labels

    return [lbl for _, lbl in rule_labels(p, spec)]


def explainer(spec: checkpoint.MarketSpec) -> str:
    first, mid, close = spec.checkpoints
    from ui.i18n import INDEX_ZH, zh

    if zh():
        auction_txt = ("A股 14:57–15:00 收盘集合竞价于 15:00 出价" if spec.mkt == "CN"
                       else "港股 16:00–16:10 收市竞价约 16:08 产生官方收市价")
        hk = ("" if spec.mkt == "CN" else """
**港股的不同之处。** 港股没有ST，也没有涨跌停。换手率远低于内地，因此有港股换手率选项。主力资金只来自东方财富（新浪只覆盖内地）：
无法获取时不检查该条件，可信度显示为**低**。免费港股*报价*约延迟 15 分钟，但这里用的分时数据是即时的。
""")
        index = INDEX_ZH.get(spec.index_name, spec.index_name)
        return f"""
**为什么是 {first}、{mid} 和 {close}？** 这个选股是为 {close} 收盘前最后一小时设计的。收盘后行情只给出收盘数据，
因此每只股票都用分时数据还原到各检查点当时的状态。与真实收盘行情核对，还原的涨幅误差在 0.005 个百分点以内，量比和换手率在约 1% 以内。
检查点在其时间后一分钟出现，以确保该分钟数据已经生成。收盘使用官方收盘价（{auction_txt}）。检查点到来之前，卡片会显示还需多久。
{hk}
**排名顺序**

1. **等级**，代表可靠性。
   - A：到目前为止每个检查点都满足全部条件。
   - B：最新检查点满足全部条件。
   - C：之前满足全部条件，但最新检查点未满足。
   - D：最新检查点只差一条，且只差一点（例如涨幅离 3–5% 区间不到 1 个百分点），远离区间的不算。
2. **信号分**（0–100）：

| 组成 | 权重 | 分数高的情况 |
|---|---|---|
| 趋势 | 25% | 今日 5 日线上穿 10 日线（越新越高），两条均线都向上 |
| 成交量 | 20% | 量比在 2–4 的理想区间（低于 1 偏弱，高于 6 过热） |
| 主力资金 | 20% | 大单净流入占成交额比例高 |
| 分时形态 | 20% | 在均价线之上、接近当日最高、没有从高点回落 |
| 相对强度 | 10% | 明显强于{index} |
| 流动性 | 5% | 成交额越大越难被操纵 |

**可信度。** 所有数据都在检查点当时测得时为高（包括收盘，此时收盘资金数据*就是*检查点数据）；
较早检查点只能用收盘资金数据时为中；无法获取任何资金数据时为低。
"""
    auction_txt = ("A-shares' 14:57-15:00 closing auction prints at 15:00" if spec.mkt == "CN"
                   else "Hong Kong's 16:00-16:10 closing auction prints the official close at about 16:08")
    hk = ("" if spec.mkt == "CN" else """
**Hong Kong differences.** There are no ST stocks and no daily price limit. Turnover rates are far lower than on the
mainland, hence the HK-adapted option. Main-fund flow comes only from East Money (Sina covers the mainland only):
when it is unreachable the rule is not checked and confidence shows **Low**. Free HK *quotes* run about 15 minutes
behind, but the minute series used here is current.
""")
    return f"""
**Why {first}, {mid} and {close}?** The screen is meant for the last hour before the {close} close. After the
close, quote feeds only show end-of-day values, so each stock is rebuilt from its minute-by-minute data as it stood at
each checkpoint. Checked against real end-of-day quotes, the rebuild matches change % to 0.005 points, and volume
ratio and turnover to within about 1%. A checkpoint appears one minute after its time, so its minute has printed. The
close uses the official closing price ({auction_txt}). Until a checkpoint arrives, its card says when it will.
{hk}
**Ranking, in order**

1. **Tier**, for reliability.
   - A: met every rule at every checkpoint so far.
   - B: met every rule at the latest checkpoint.
   - C: met every rule earlier, but not at the latest.
   - D: one rule short at the latest checkpoint, and only narrowly (for example, change within 1 point of the 3-5%
     band), so stocks far outside the band don't count.
2. **Signal score** (0-100):

| Component | Weight | High when |
|---|---|---|
| Trend | 25% | MA5 crossed above MA10 today (fresher cross = higher), both MAs rising |
| Volume | 20% | volume ratio in the 2-4 sweet spot (thin below 1, overheated above 6) |
| Main funds | 20% | large-order net inflow is a big share of value traded |
| Intraday pattern | 20% | above VWAP, near the day's high, not fading from it |
| Relative strength | 10% | well ahead of the {spec.index_name} |
| Liquidity | 5% | more value traded (harder to push around) |

**Confidence.** High when every input was measured at the checkpoint, including the close, where the end-of-day
flow *is* the checkpoint value. Medium when earlier money flow had to use the end-of-day figure. Low when no
money-flow source was reachable.
"""
