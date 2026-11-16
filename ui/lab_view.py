"""Strategy lab · 策略实验室 - one of the A-share front page's screens, loaded only when opened.

Built to stay light: opening it reads one small scorecard file; the big files (the ~80,000 backtest
trades, a strategy's candidate pool) load only for the view that needs them. It runs as a fragment, so
moving a slider re-scores the lab without reloading the rest of the front page. One language at a time
(ui.i18n; the data layer's texts are translated in ui.lab_zh).
"""

from datetime import date

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from stockrt import checkpoint, lab, labpools, sample
from stockrt.calendar import now_bj, status
from stockrt.export import to_csv_bytes
from stockrt.labdata import EXIT_LABEL, EXITS
from stockrt.strategies import STRATEGIES
from ui import lab_zh
from ui.charts import MUTED, palette
from ui.fmt import style_signed, up_down_colors
from ui.i18n import day as fday
from ui.i18n import t, zh

P = st.column_config
VIEWS = {
    "card": ("成绩单", "Scorecard"),
    "custom": ("自定义参数", "Try your own parameters"),
    "today": ("今日信号", "Today"),
    "detail": ("单个策略", "One strategy"),
    "checks": ("市场检验", "Market checks"),
    "method": ("检验方法", "How it's tested"),
}


def title(sid: str) -> str:
    s = STRATEGIES[sid]
    return t(f"{s.id} · {s.zh}", f"{s.id} · {s.name}")


def time_label(s) -> str:
    return t("09:25（集合竞价）", "09:25 (auction)") if s.time == "open" else s.time


def _stamp() -> str:
    """Changes whenever new lab results are published (cache key)."""
    f = lab.lab_dir() / "meta.json"
    return f"{lab.lab_dir()}:{f.stat().st_mtime if f.exists() else 0}"


@st.cache_data(max_entries=2, show_spinner=False)
def _file(stamp: str, name: str) -> pd.DataFrame:
    return lab.read(name)


@st.cache_data(max_entries=1, show_spinner=False)
def _scorecards(stamp: str) -> pd.DataFrame:
    sc = lab.read("scorecards.parquet")
    if sc.empty:   # older published results: compute once per server
        sc = lab.write_scorecards()
    return sc


@st.cache_data(max_entries=1, show_spinner=False)
def _pool(sid: str) -> pd.DataFrame:
    return labpools.load(sid)


@st.cache_data(max_entries=1, show_spinner=False)
def _names() -> dict[str, str]:
    try:
        u = pd.read_parquet(sample.SAMPLE_DIR / "universe.parquet")
        return dict(zip(u["code"], u["name"]))
    except Exception:  # noqa: BLE001 - names are a nicety
        return {}


@st.fragment
def render() -> None:
    st.caption(t("八个规则固定的A股策略，用同一标准评判：扣除交易成本后，是否跑赢同一时刻买入全部股票？"
                 "先用 30 分钟K线重建一年的回测，再从 2026 年 10 月 12 日起实盘记录。可在 **自定义参数** 中设置自己的参数。",
                 "Eight A-share strategy variants with fixed rules, each judged the same way: did it beat simply "
                 "buying every stock at the same moment, after trading costs? One year of backtest rebuilt from "
                 "30-minute bars, then a live record kept from 12 Oct 2026. Set your own parameters under **Try your "
                 "own parameters**."))
    stamp = _stamp()
    meta = lab.meta()
    if not meta:
        st.info(t("还没有策略实验室结果。", "No Strategy lab results yet."), icon=":material/info:")
        return
    view = st.segmented_control(t("显示", "Show"), list(VIEWS), format_func=lambda k: t(*VIEWS[k]), default="card",
                                key="lab_view", required=True)
    if view in ("card", "custom", "detail"):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            exit_ = st.segmented_control(t("卖出时间", "Sell at"), list(EXITS), default="t1_close",
                                         format_func=lambda x: lab_zh.exit_label(EXIT_LABEL[x]), key="lab_exit",
                                         required=True, help=t("A股为 T+1：今天买入的股票最早明天才能卖出。",
                                                               "A-shares are T+1: a stock bought today can be sold "
                                                               "tomorrow at the earliest."))
            bench = st.segmented_control(t("基准", "Benchmark"), list(lab.BENCHES), default="ew",
                                         format_func=lambda b: lab_zh.bench(lab.BENCHES[b]), key="lab_bench",
                                         required=True,
                                         help=t("等权全市场：在同一时刻买入全部合格股票、同时卖出。它剔除了大盘涨跌和A股的隔夜效应。",
                                                "Equal-weight market: buy every eligible stock at the same moment and "
                                                "sell at the same time. It removes the market's move and China's "
                                                "overnight effect."))
    if view == "card":
        _card(stamp, meta, exit_, bench)
    elif view == "custom":
        _custom(stamp, exit_, bench)
    elif view == "today":
        _today(stamp)
    elif view == "detail":
        _detail(stamp, exit_, bench)
    elif view == "checks":
        _checks(stamp)
    else:
        _method()


# ---------------------------------------------------------------------------------------------- scorecard

def _card(stamp: str, meta: dict, exit_: str, bench: str) -> None:
    rules = st.segmented_control(t("实盘规则", "Live rules"), ["all", "core"], default="all", key="lab_rules",
                                 required=True,
                                 format_func=lambda r: t("全部规则", "All rules") if r == "all" else
                                 t("只用可回测的规则", "Backtestable rules only"),
                                 help=t("主力资金（尾盘选股）和竞价量（竞价选股）没有历史数据，回测不检查它们。"
                                        "“只用可回测的规则”让实盘记录与回测可以同口径比较。",
                                        "Main-fund flow (尾盘选股) and auction volume (竞价选股) have no history, so "
                                        "the backtest skips them. 'Backtestable only' compares the live record like "
                                        "for like with the backtest."))
    sc = _scorecards(stamp)
    pick = sc[(sc["exit"] == exit_) & (sc["bench"] == bench)] if not sc.empty else sc
    bt = pick[pick["period"] == "backtest"].set_index("strategy") if not pick.empty else pd.DataFrame()
    lv = pick[(pick["period"] == "live") & (pick["rules"] == rules)].set_index("strategy") \
        if not pick.empty else pd.DataFrame()
    rows = []
    for sid, st_ in STRATEGIES.items():
        b = bt.loc[sid].to_dict() if sid in bt.index else {}
        v = lv.loc[sid].to_dict() if sid in lv.index else {}
        rows.append({
            "strategy": title(sid), "signal": time_label(st_), "bt_verdict": lab_zh.verdict(b.get("verdict", "—")),
            "bt_trades": b.get("trades", 0), "bt_per_day": b.get("per_day"), "bt_ret": b.get("avg_ret"),
            "bt_excess": b.get("avg_excess"), "bt_beat": b.get("beat"), "bt_t": b.get("t"),
            "bt_placebo": b.get("placebo"), "why": lab_zh.why(b.get("why", "")), "failed": b.get("verdict") == "Fails",
            "lv_verdict": lab_zh.verdict(v.get("verdict", f"Too early (0/{lab.LIVE_MIN_DAYS} days)")),
            "lv_days": v.get("days", 0), "lv_trades": v.get("trades", 0), "lv_excess": v.get("avg_excess"),
            "lv_beat": v.get("beat")})
    tbl = pd.DataFrame(rows)
    b_name = lab_zh.bench(lab.BENCHES[bench])
    st.caption(t(f"回测 **{meta.get('start')} → {meta.get('end')}**（{meta.get('sessions')} 个交易日，"
                 f"{meta.get('stocks', 0):,} 只股票）。实盘自 **{lab.LIVE_START:%Y-%m-%d}** 起，最近记录于 "
                 f"{meta.get('live_updated', '尚无')}。收益已扣除 **{lab.ROUND_TRIP:.2%}** 的双边成本；超额以 **{b_name}** 为基准。",
                 f"Backtest **{meta.get('start')} → {meta.get('end')}** ({meta.get('sessions')} sessions, "
                 f"{meta.get('stocks', 0):,} stocks). Live since **{lab.LIVE_START:%d %b %Y}**, last recorded "
                 f"{meta.get('live_updated', 'not yet')}. Returns after **{lab.ROUND_TRIP:.2%}** round-trip costs; "
                 f"excess is against the **{b_name}**."))
    st.dataframe(tbl.drop(columns=["why", "failed"]), hide_index=True, column_config={
        "strategy": P.TextColumn(t("策略", "Strategy"), width="medium"), "signal": P.TextColumn(t("信号时间", "Signal")),
        "bt_verdict": P.TextColumn(t("回测", "Backtest")), "bt_trades": P.NumberColumn(t("交易数", "Trades"), format="%d"),
        "bt_per_day": P.NumberColumn(t("每日选股数", "Picks / day"), format="%.1f"),
        "bt_ret": P.NumberColumn(t("平均收益", "Avg return"), format="percent",
                                 help=t("每笔交易，扣除成本后。", "Per trade, after costs.")),
        "bt_excess": P.NumberColumn(t("平均超额", "Avg excess"), format="percent",
                                    help=t("每笔交易，扣除成本后减去基准。", "Per trade, after costs, minus the benchmark.")),
        "bt_beat": P.NumberColumn(t("跑赢市场", "Beat market"), format="percent",
                                  help=t("跑赢基准的交易占比。", "Share of trades that beat the benchmark.")),
        "bt_t": P.NumberColumn(t("t 值", "t-stat"), format="%.1f",
                               help=t("日均超额的 t 值，≥ 2 表示不太可能是运气。",
                                      "Of the daily average excess. 2 or more = unlikely luck.")),
        "bt_placebo": P.NumberColumn(t("胜过随机", "vs random"), format="percent",
                                     help=t("每天随机选同样数量股票的选股者中，被它跑赢的比例。",
                                            "Share of random pickers (same number of stocks each day) it beats.")),
        "lv_verdict": P.TextColumn(t("实盘", "Live")), "lv_days": P.NumberColumn(t("实盘天数", "Live days"), format="%d"),
        "lv_trades": P.NumberColumn(t("实盘交易数", "Live trades"), format="%d"),
        "lv_excess": P.NumberColumn(t("实盘超额", "Live excess"), format="percent"),
        "lv_beat": P.NumberColumn(t("实盘跑赢率", "Live beat"), format="percent")})
    fails = tbl[tbl["failed"]]
    if not fails.empty:
        with st.expander(t("每个回测未通过的原因", "Why each backtest failed"), icon=":material/help:"):
            for r in fails.itertuples():
                st.markdown(f"- **{r.strategy}**: {r.why}")
    st.caption(t(f"通过的条件：平均超额为正；t 值 ≥ 2 或跑赢 95% 的随机选股；超过一半的交易跑赢市场；3 个子区间中至少 2 个为正。"
                 f"实盘结论需等满 {lab.LIVE_MIN_DAYS} 个信号日。",
                 "A strategy passes when its average excess is positive; its t-statistic is at least 2 or it beats "
                 "95% of random pickers; it beats the market on more than half its trades; and at least 2 of 3 "
                 f"sub-periods are positive. Live verdicts wait for {lab.LIVE_MIN_DAYS} signal days."))


# ---------------------------------------------------------------------------------------------- your parameters

def custom_title(sid: str) -> str:
    if sid == "S4":
        return t("S4 · 首板（成交假设：乐观或悲观）", "S4 · First limit-up (fill: optimistic or pessimistic)")
    return title(sid)


def _widget(sid: str, p: labpools.Param):
    key = f"lab_p_{sid}_{p.key}"
    label, help_ = lab_zh.param(p.label), (p.help or None)
    if p.kind == "range":
        return st.slider(label, float(p.lo), float(p.hi), value=tuple(map(float, p.default)), step=float(p.step),
                         key=key, help=help_)
    if p.kind in ("min", "max"):
        return st.slider(label, p.lo, p.hi, value=p.default, step=p.step, key=key, help=help_)
    if p.kind == "toggle":
        return st.toggle(label, value=bool(p.default), key=key, help=help_)
    fmt = {"assume": t("假设能成交", "Assume it fills"), "seal_breaks": t("只在开板时成交", "Only if the seal breaks"),
           0: t("不要求", "Off"), 1: t("今天", "Today"), 2: t("2 个交易日", "2 sessions"), 3: t("3 个交易日", "3 sessions")}
    return st.segmented_control(label, list(p.options), default=p.default, key=key, required=True,
                                format_func=lambda o: fmt.get(o, str(o)), help=help_)


def _custom(stamp: str, exit_: str, bench: str) -> None:
    sid = st.selectbox(t("策略", "Strategy"), list(labpools.SPECS), format_func=custom_title, key="lab_custom_sid")
    spec = labpools.SPECS[sid]
    if st.button(t("恢复官方规则", "Reset to the official rules"), icon=":material/restart_alt:", key=f"lab_reset_{sid}"):
        for p in spec.params:
            st.session_state.pop(f"lab_p_{sid}_{p.key}", None)
        st.rerun(scope="fragment")
    values = {}
    with st.container(border=True):
        cols = st.columns(2)
        for i, p in enumerate(spec.params):
            with cols[i % 2]:
                values[p.key] = _widget(sid, p)
    defaults = labpools.defaults(sid)
    official_id = "S4b" if sid == "S4" and values.get("fill") == "seal_breaks" else sid
    # 首板's two fills are both official (S4 and S4b); every other parameter must be at its default.
    is_official = all(values[k] == v for k, v in defaults.items() if not (sid == "S4" and k == "fill"))
    with st.spinner(t("正在载入候选池…", "Loading the candidate pool…")):
        pool = _pool(sid)
    if pool.empty:
        st.info(t("候选池没有安装（research/lab_pools/）。", "The candidate pools aren't installed (research/lab_pools/)."),
                icon=":material/info:")
        return
    days = _file(stamp, "days.parquet")
    yours = labpools.picks(sid, pool, values)
    base = labpools.picks(sid, pool, defaults | ({"fill": values["fill"]} if sid == "S4" else {}))
    sc = _scorecards(stamp)
    off = sc[(sc["period"] == "backtest") & (sc["exit"] == exit_) & (sc["bench"] == bench) &
             (sc["strategy"] == official_id)]
    m_off = off.iloc[0].to_dict() if not off.empty else lab.metrics(base.assign(strategy=official_id), days,
                                                                     official_id, exit_, bench)
    m_you = m_off if is_official else lab.metrics(yours.assign(strategy=official_id), days, official_id, exit_, bench)
    official, mine = t("官方规则", "Official rules"), t("你的设置", "Your settings")
    rows = []
    for label, m in ((official, m_off), (mine if not is_official else t("你的设置（= 官方）", "Your settings (= official)"),
                                         m_you)):
        rows.append({"who": label, "trades": m.get("trades"), "per_day": m.get("per_day"), "ret": m.get("avg_ret"),
                     "excess": m.get("avg_excess"), "beat": m.get("beat"), "t": m.get("t"), "placebo": m.get("placebo"),
                     "thirds": m.get("thirds"), "verdict": lab_zh.verdict(m.get("verdict", "—"))})
    tbl = pd.DataFrame(rows)
    for c in tbl.columns.drop(["who", "verdict"]):
        tbl[c] = pd.to_numeric(tbl[c], errors="coerce")
    st.dataframe(tbl, hide_index=True, column_config={
        "who": P.TextColumn(""), "trades": P.NumberColumn(t("交易数", "Trades"), format="%d"),
        "per_day": P.NumberColumn(t("每日选股数", "Picks / day"), format="%.1f"),
        "ret": P.NumberColumn(t("平均收益", "Avg return"), format="percent"),
        "excess": P.NumberColumn(t("平均超额", "Avg excess"), format="percent"),
        "beat": P.NumberColumn(t("跑赢市场", "Beat market"), format="percent"), "t": P.NumberColumn("t", format="%.1f"),
        "placebo": P.NumberColumn(t("胜过随机", "vs random"), format="percent"),
        "thirds": P.NumberColumn(t("正收益子区间", "Sub-periods +"), format="%d"),
        "verdict": P.TextColumn(t("结论", "Verdict"))})
    if not is_official and m_you.get("why"):
        st.caption(t(f"你的设置：{lab_zh.verdict(m_you['verdict'])}——{lab_zh.why(m_you['why'])}。",
                     f"Your settings: {m_you['verdict']} - {m_you['why']}."))
    curves = []
    for label, trades in ((official, base), (mine, yours)):
        d = lab.daily(trades.assign(strategy=official_id), days, official_id, exit_, bench)
        if not d.empty:
            curves.append(d.assign(who=label))
    if curves:
        both = pd.concat(curves, ignore_index=True)
        both["day"] = pd.to_datetime(both["day"])
        pal = palette()
        st.altair_chart(alt.layer(
            alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED).encode(y="y:Q"),
            alt.Chart(both).mark_line(strokeWidth=1.8).encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)),
                y=alt.Y("cum_excess:Q", title=t("累计超额（日超额之和）", "Cumulative excess (sum of daily %)"),
                        axis=alt.Axis(format="%")),
                color=alt.Color("who:N", title=None, scale=alt.Scale(domain=[official, mine], range=[pal[0], pal[1]]),
                                legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("day:T", title=t("交易日", "Session")), alt.Tooltip("who:N", title=""),
                         alt.Tooltip("n:Q", title=t("选股数", "Picks")),
                         alt.Tooltip("excess:Q", title=t("超额", "Excess"), format="+.2%"),
                         alt.Tooltip("cum_excess:Q", title=t("累计", "Cumulative"), format="+.1%")])).properties(
            height=260), width="stretch")
    st.warning(t("在同一年的数据上反复尝试参数，总能碰巧找到一些看起来不错的——二十次中最好的一次通常就是这样。"
                 "把好看的设置当作值得观察的想法，而不是证据：它必须在没见过的交易日（实盘记录）上也有效才有意义。",
                 "Trying many settings on the same year will always find some that look good by chance - the best "
                 "of twenty tries usually is. Treat a good-looking setting as an idea to watch, not proof: it has to "
                 "work on sessions it hasn't seen (the live record) before it means anything."),
               icon=":material/science:")
    names = _names()
    log = yours.sort_values(["day", "code"], ascending=[False, True]).copy()
    log["name"] = log["code"].astype(str).map(names)
    keep = ["day", "code", "name"] + [c for c in log.columns if c not in ("day", "code", "name", "strategy", "all")]
    st.download_button(t(f"你这一年的选股（{len(log):,} 条，CSV）", f"Your picks for the year ({len(log):,}, CSV)"),
                       to_csv_bytes(log[keep]), file_name=f"lab_{sid}_custom.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    st.caption(t("由该策略的候选池计算（回测年度内所有接近规则的股票-交易日；收益保留到整数基点）。官方一行就是官方回测本身。",
                 "Computed from the strategy's candidate pool (every stock-session near the rules over the backtest "
                 "year; returns kept in whole basis points). The official row is the official backtest itself."))


# ---------------------------------------------------------------------------------------------- today

def _today(stamp: str) -> None:
    if sample.is_sample():
        st.caption(t("今日信号需要实时数据——请在侧边栏打开 **实时数据**。",
                     "Today's signals need live data - turn on **Live data** in the sidebar."))
        return
    from ui.picks_view import job_runner

    day = checkpoint.latest_session_day("CN")
    now = now_bj()
    live_day = day == now.date()
    s = status("CN")
    if live_day and s.phase in ("pre-open", "before open"):
        st.caption(t("第一个信号（09:25 集合竞价）在北京时间 09:31 读取。",
                     "The first signal (09:25 auction) is read from 09:31 Beijing."))
        return
    live = _file(stamp, "live_trades.parquet")
    recorded = live[live["day"] == pd.Timestamp(day)] if not live.empty else pd.DataFrame()
    over = not live_day or s.phase == "closed"
    if not recorded.empty and over:
        st.caption(t(f"**{fday(day)}** 的信号，收盘后记录在实盘日志中（已固定）。",
                     f"Signals of **{fday(day)}**, as recorded in the live log after the close (fixed)."))
        _show_today(recorded, {}, day)
        return
    times = tuple(x for x in lab.SIGNAL_ORDER if not live_day or lab.reached(x, now))
    if not times:
        st.caption(t("今天还没有到任何信号时间。", "No signal time has passed yet today."))
        return
    res = lab.load_today(day)
    if res is None or tuple(res["times"]) != times:
        job = job_runner().submit(("lab_today", str(day), times), "lab_today", {"workers": checkpoint.WORKERS},
                                  front=True)
        if job.active or job.state == "failed":
            from ui.progress import job_card

            job_card(job, t("策略实验室 · 正在读取今日信号", "Strategy lab · reading today's signals"),
                     t("每个策略的今日信号", "today's signals for every strategy"), typical=90)
        res = lab.load_today(day) or res
    if res is None:
        return
    when = t("今日", "today") if live_day else fday(day)
    st.caption(t(f"**{when}** 的信号 · 北京时间 {res['computed']} 从 {res['candidates']} 只初筛股票中计算。每个名单在其信号时间过后固定；"
                 "收盘后由每日更新记录。",
                 f"Signals for **{when}** · computed {res['computed']} Beijing from {res['candidates']} shortlisted "
                 "stocks. Each list is fixed once its signal time has passed; the daily update records it after the "
                 "close."))
    _show_today(res["picks"], res["pending"], day)


def _show_today(picks: pd.DataFrame, pending: dict, day: date) -> None:
    from ui import cache

    now_px = {}
    try:
        snap, _ = cache.snapshot("CN")
        now_px = dict(zip(snap["code"], snap["price"]))
    except Exception:  # noqa: BLE001 - the "since signal" column is a nicety
        pass
    cols = st.columns(2)
    for i, (sid, st_) in enumerate(STRATEGIES.items()):
        with cols[i % 2].container(border=True):
            if sid in pending:
                label = t(f"{st_.known_at} 确定", f"final at {st_.known_at}") if st_.known_at else \
                    t(f"{pending[sid]} 出", f"at {pending[sid]}")
                st.markdown(f"**{title(sid)}** :gray-badge[{label}]")
                continue
            p = picks[picks["strategy"] == sid] if not picks.empty else picks
            st.markdown(f"**{title(sid)}** " + (f":blue-badge[{t(f'{len(p)} 只', f'{len(p)} picks')}]" if len(p) else
                                                f":gray-badge[{t('无', 'no picks')}]"))
            if st_.live_only and len(p):
                n_all = int((p["all"] == True).sum())  # noqa: E712 - object column
                names = "、".join(lab_zh.rule(r.label) for r in st_.live_only) if zh() else \
                    ", ".join(r.label for r in st_.live_only)
                st.caption(t(f"其中 {n_all} 只也满足仅实盘检查的条件（{names}）。",
                             f"{n_all} also pass the live-only rules ({names})."))
            if p.empty:
                continue
            tb = p.assign(now=p["code"].map(now_px))
            tb["since"] = tb["now"] / tb["price"] - 1
            show = ["code", "name", "price", "pct_change", "now", "since"]
            st.dataframe(style_signed(tb[show], ["pct_change", "since"]), hide_index=True,
                         height=min(35 * len(tb) + 38, 260),
                         column_config={"code": P.TextColumn(t("代码", "Code")), "name": P.TextColumn(t("名称", "Name")),
                                        "price": P.NumberColumn(t(f"{time_label(st_)} 价格",
                                                                  f"Price at {time_label(st_)}"), format="%.2f"),
                                        "pct_change": P.NumberColumn(t("当时涨幅", "Change then"), format="%+.2f%%"),
                                        "now": P.NumberColumn(t("现价", "Now"), format="%.2f"),
                                        "since": P.NumberColumn(t("信号后涨跌", "Since signal"), format="percent")})


# ---------------------------------------------------------------------------------------------- one strategy

PARTS = {"perf": ("表现", "Performance"), "rules": ("规则与理由", "Rules & why"),
         "mood": ("情绪分组", "Market mood split"), "log": ("选股记录", "Pick log")}


def _detail(stamp: str, exit_: str, bench: str) -> None:
    sid = st.selectbox(t("策略", "Strategy"), list(STRATEGIES), format_func=title, key="lab_sid")
    S = STRATEGIES[sid]
    part = st.segmented_control(t("内容", "Part"), list(PARTS), format_func=lambda k: t(*PARTS[k]), default="perf",
                                key="lab_detail_part", required=True)
    days = _file(stamp, "days.parquet")
    if part == "rules":
        st.markdown(t(f"**信号：** 北京时间 {time_label(S)} · **买入：** {lab_zh.ENTRY.get(sid, S.entry)} · **卖出：** 次日开盘、"
                      "次日收盘，或第2个交易日收盘（T+1 规则）。",
                      f"**Signal:** {time_label(S)} Beijing · **Entry:** {S.entry} · **Exit:** the next open, the next "
                      "close, or the close two sessions later (T+1 rule)."))
        buyable = t("（开盘以来未封涨停）" if S.limit_entry else "（不在涨停价）",
                    " (not sealed at the limit since the open)" if S.limit_entry else " (not at the limit-up price)")
        st.markdown(t("**规则**（必须全部满足）：\n", "**Rules** (all must hold):\n")
                    + "\n".join(f"- {lab_zh.rule(r.label)}" for r in S.rules)
                    + t("\n- 能以买入价买到", "\n- Buyable at the entry price") + buyable)
        if S.live_only:
            st.markdown(t("**仅实盘检查**（没有历史数据，回测跳过）：\n", "**Live only** (no history, so the backtest skips "
                          "them):\n") + "\n".join(f"- {lab_zh.rule(r.label)}" for r in S.live_only))
        st.markdown(t(f"**理由：** {lab_zh.WHY.get(sid, S.why)}", f"**Why:** {S.why}"))
        if S.sources:
            st.markdown(t("**研究：** ", "**Research:** ") + " · ".join(f"[{n}]({u})" for n, u in S.sources))
        return
    bt_all = _file(stamp, "backtest_trades.parquet")
    bt = bt_all[bt_all["strategy"] == sid]
    lv_all = _file(stamp, "live_trades.parquet")
    lv = lv_all[lv_all["strategy"] == sid] if not lv_all.empty else lv_all
    backtest, live = t("回测", "Backtest"), t("实盘", "Live")
    if part == "perf":
        bt_d = lab.daily(bt, days, sid, exit_, bench, "core").assign(period=backtest)
        lv_d = lab.daily(lv, days, sid, exit_, bench, "all").assign(period=live) if not lv.empty else pd.DataFrame()
        if not lv_d.empty and not bt_d.empty:
            lv_d["cum_excess"] = lv_d["excess"].cumsum() + bt_d["cum_excess"].iloc[-1]
        both = pd.concat([x for x in (bt_d, lv_d) if not x.empty], ignore_index=True)
        if both.empty:
            st.caption(t("没有信号。", "No signals."))
            return
        both["day"] = pd.to_datetime(both["day"])
        pal = palette()
        st.altair_chart(alt.layer(
            alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED).encode(y="y:Q"),
            alt.Chart(both).mark_line(strokeWidth=1.8).encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)),
                y=alt.Y("cum_excess:Q", title=t("累计超额（日超额之和）", "Cumulative excess (sum of daily %)"),
                        axis=alt.Axis(format="%")),
                color=alt.Color("period:N", title=None, scale=alt.Scale(domain=[backtest, live], range=[pal[0], pal[1]]),
                                legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("day:T", title=t("交易日", "Session")),
                         alt.Tooltip("n:Q", title=t("选股数", "Picks")),
                         alt.Tooltip("excess:Q", title=t("扣除成本后超额", "Excess after costs"), format="+.2%"),
                         alt.Tooltip("cum_excess:Q", title=t("累计", "Cumulative"), format="+.1%")])).properties(
            height=280), width="stretch")
        up, down = up_down_colors()
        m = both.assign(month=both["day"].dt.to_period("M").dt.to_timestamp()).groupby(["month", "period"]).agg(
            excess=("excess", "mean"), days=("excess", "size")).reset_index()
        st.altair_chart(alt.Chart(m).mark_bar().encode(
            x=alt.X("month:T", title=None, timeUnit="yearmonth", axis=alt.Axis(grid=False, labelColor=MUTED)),
            y=alt.Y("excess:Q", title=t("日均超额", "Average daily excess"), axis=alt.Axis(format="%")),
            color=alt.condition("datum.excess > 0", alt.value(up), alt.value(down)),
            tooltip=[alt.Tooltip("month:T", title=t("月份", "Month"), format="%Y-%m"),
                     alt.Tooltip("excess:Q", title=t("平均超额", "Avg excess"), format="+.2%")]).properties(height=170),
            width="stretch")
    elif part == "mood":
        mm = lab.metrics(bt, days, sid, exit_, bench, "core")
        c1, c2 = st.columns(2)
        c1.metric(t("情绪好的日子", "Warm days"), f"{mm.get('warm_excess', np.nan):+.2%}" if mm.get("warm_days") else "—",
                  border=True, help=t("昨日涨停股在信号时间平均上涨的交易日。",
                                      "Sessions when yesterday's limit-up stocks were up on average at the signal time."))
        c2.metric(t("情绪差的日子", "Cold days"), f"{mm.get('cold_excess', np.nan):+.2%}" if mm.get("cold_days") else "—",
                  border=True)
        st.caption(t(f"日均超额 · 情绪好 {mm.get('warm_days', 0)} 天，情绪差 {mm.get('cold_days', 0)} 天。",
                     f"Average daily excess · {mm.get('warm_days', 0)} warm and {mm.get('cold_days', 0)} cold "
                     "signal days."))
    else:
        tr = pd.concat([lv.assign(period=live) if not lv.empty else None, bt.assign(period=backtest)],
                       ignore_index=True).sort_values(["day", "code"], ascending=[False, True])
        cols = ["period", "day", "code", "name", "price", "pct_change", "volume_ratio", "turnover_rate", "float_mcap",
                *[f"ret_{x}" for x in EXITS]]
        shown = tr[cols].head(300).copy()
        shown["day"] = pd.to_datetime(shown["day"]).dt.date
        st.dataframe(style_signed(shown, ["pct_change", *[f"ret_{x}" for x in EXITS]]), hide_index=True,
                     column_config={"period": P.TextColumn(""), "day": P.DateColumn(t("交易日", "Session")),
                                    "code": P.TextColumn(t("代码", "Code")), "name": P.TextColumn(t("名称", "Name")),
                                    "price": P.NumberColumn(t("买入价", "Entry"), format="%.2f"),
                                    "pct_change": P.NumberColumn(t("信号时涨幅", "Change at signal"), format="%+.2f%%"),
                                    "volume_ratio": P.NumberColumn(t("量比", "Vol. ratio"), format="%.2f"),
                                    "turnover_rate": P.NumberColumn(t("换手率", "Turnover"), format="%.2f%%"),
                                    "float_mcap": P.NumberColumn(t("流通市值", "Float cap"), format="compact"),
                                    **{f"ret_{x}": P.NumberColumn(lab_zh.exit_label(EXIT_LABEL[x]), format="percent")
                                       for x in EXITS}})
        st.download_button(t(f"{sid} 全部选股（CSV）", f"All {sid} picks (CSV)"), to_csv_bytes(tr[cols]),
                           file_name=f"strategy_lab_{sid}.csv", mime="text/csv", on_click="ignore",
                           icon=":material/download:")


# ---------------------------------------------------------------------------------------------- market checks

def _checks(stamp: str) -> None:
    days = _file(stamp, "days.parquet")
    o = days[days["time"] == "open"].dropna(subset=["ew_overnight", "ew_open_close"]).sort_values("day")
    if not o.empty:
        st.markdown(t("**隔夜与日内**（平均个股）。研究表明，受 T+1 影响，A股隔夜收益为负、日内收益为正——与美股相反。",
                      "**Overnight vs daytime** (average stock). Research says China's overnight return is negative "
                      "and its daytime return positive - the reverse of the US - because of the T+1 rule."))
        c1, c2 = st.columns(2)
        c1.metric(t("收盘 → 次日开盘", "Close → next open"), f"{o['ew_overnight'].mean():+.3%}",
                  help=t("每个交易日平均。", "Average per session."), border=True)
        c2.metric(t("开盘 → 收盘", "Open → close"), f"{o['ew_open_close'].mean():+.3%}",
                  help=t("每个交易日平均。", "Average per session."), border=True)
    mom = _file(stamp, "csi300_intraday.parquet")
    ms = lab.momentum_stats(mom) if not mom.empty else {}
    if ms:
        st.markdown(t("**沪深300 日内动量**——10:00 前的涨跌能否预测最后半小时？",
                      "**CSI 300 intraday momentum** - does the move up to 10:00 predict the last half hour?"))
        c1, c2, c3 = st.columns(3)
        c1.metric(t("相关系数", "Correlation"), f"{ms['corr']:+.2f}",
                  help=t(f"{ms['sessions']} 个交易日", f"{ms['sessions']} sessions"), border=True)
        c2.metric(t("斜率 t 值", "Slope t-stat"), f"{ms['t']:+.1f}", border=True)
        c3.metric(t("方向一致", "Same direction"), f"{ms['same_sign']:.0%}", border=True)
    sent = days[days["time"] == "14:30"].sort_values("day")
    if not sent.empty:
        st.markdown(t("**14:30 的短线情绪**：昨日涨停股今日表现，以及炸板率（触及涨停后回落）。",
                      "**短线情绪 at 14:30**: yesterday's limit-ups today, and 炸板率 (touched the limit, fell back)."))
        c1, c2 = st.columns(2)
        for col, field, label, k in ((c1, "yday_lu_avg", t("昨日涨停股：平均涨跌（%）", "Yesterday's limit-ups: average "
                                                                                    "move (%)"), 1),
                                     (c2, "zhaban_rate", t("炸板率（%）", "炸板率 (%)"), 100)):
            dd = sent[["day", field]].dropna()
            dd = dd.assign(v=dd[field] * k)
            col.caption(label)
            col.altair_chart(alt.Chart(dd).mark_line(strokeWidth=1.2, color=palette()[0]).encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)), y=alt.Y("v:Q", title=None),
                tooltip=[alt.Tooltip("day:T", title=t("交易日", "Session")),
                         alt.Tooltip("v:Q", title=label, format=".1f")]).properties(height=160), width="stretch")


def _method() -> None:
    if zh():
        st.markdown(f"""
- **数据。** 用腾讯的 30 分钟K线（保留一年）把每只股票还原到开盘、10:00、10:30、14:00 和 14:30 时的状态；日线提供历史。
  14:30 的还原已与首页对真实交易日的逐分钟重放核对过。
- **股票池**（所有策略和基准相同）：非ST的沪深A股，有 60 个以上交易日的历史（北交所股票没有 30 分钟K线）。
- **基准。** 等权全市场 = 在同一时刻买入全部合格股票、同时卖出；以及同一时间段的沪深300和中证1000。
- **成本**（每次买卖来回）：{lab.ROUND_TRIP:.3%}（佣金、卖出印花税、过户费、滑点）。
- **随机选股。** “胜过随机”指：每个交易日随机抽取同样数量股票的选股者中，被该策略跑赢的比例。**t 值**按日均超额计算（按天数而不是交易笔数）。
- **未建模。** 涨停价的挂单不一定能成交（首板同时展示两种极端）；跌停的股票不一定能卖出；ST 状态和股票列表为今日的。
  跨除权日或停牌的交易被剔除。
- **实盘记录。** 每天收盘后，每日更新按全部规则记录每个策略的选股，并在卖出发生时补上收益。选股记录之后不会修改。规则见
  docs/STRATEGY_RESEARCH.md。
""")
        st.caption("仅供研究，不构成投资建议。")
        return
    st.markdown(f"""
- **Data.** Tencent's 30-minute bars (one year kept) rebuild every stock as it stood at the open, 10:00, 10:30,
  14:00 and 14:30; daily bars give the history. The 14:30 rebuild was checked against the front page's
  minute-by-minute replay of real sessions.
- **Universe** for every strategy and the benchmark: non-ST Shanghai / Shenzhen A-shares with 60+ sessions of
  history (Beijing-exchange stocks have no 30-minute bars).
- **Benchmarks.** Equal-weight market = every eligible stock bought at the same moment and sold at the same time;
  CSI 300 and CSI 1000 over the same window.
- **Costs** per round trip: {lab.ROUND_TRIP:.3%} (commission, stamp duty on the sale, transfer fee, slippage).
- **Random pickers.** "vs random" is the share of pickers drawing the same number of stocks at random each session
  that the strategy beats. **t-stat** of the daily average excess (days, not trades, are counted).
- **Not modelled.** An order at the limit-up price may not fill (首板 shows both extremes); a stock at limit-down
  may not be sellable; ST status and the stock list are today's. Trades spanning an ex-date or a suspension are
  dropped.
- **Live record.** After each close the daily update records every strategy's picks with all rules and fills in
  exits as they happen. Picks are never changed afterwards. Rules: docs/STRATEGY_RESEARCH.md.
""")
    st.caption("Research tooling, not investment advice.")
