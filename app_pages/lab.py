"""Strategy lab · 策略实验室 - A-share strategies tested against the market: one year of backtest,
a live record from 12 Oct 2026, and today's signals in real time."""

from datetime import date

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from stockrt import checkpoint, lab, sample
from stockrt.calendar import now_bj, status
from stockrt.export import to_csv_bytes
from stockrt.labdata import EXIT_LABEL, EXITS
from stockrt.strategies import STRATEGIES
from ui.charts import MUTED, palette
from ui.fmt import style_signed, up_down_colors
from ui.picks_view import job_runner

P = st.column_config


def _mtime() -> float:
    f = lab.LAB_DIR / "meta.json"
    return f.stat().st_mtime if f.exists() else 0.0


@st.cache_data(max_entries=1, show_spinner=False)
def data(_mtime: float) -> dict:
    return lab.load()


@st.cache_data(max_entries=6, show_spinner=False)
def card(_mtime: float, exit: str, bench: str, rules: str) -> pd.DataFrame:
    d = data(_mtime)
    bt = lab.scorecard(d["backtest"], d["days"], exit, bench, "core") if not d["backtest"].empty else pd.DataFrame()
    lv = lab.scorecard(d["live"], d["days"], exit, bench, rules, live=True) if not d["live"].empty else pd.DataFrame()
    rows = []
    for sid, st_ in STRATEGIES.items():
        b = bt.set_index("strategy").loc[sid].to_dict() if not bt.empty else {}
        v = lv.set_index("strategy").loc[sid].to_dict() if not lv.empty else {}
        rows.append({
            "Strategy": st_.title, "Signal": st_.time_label,
            "bt_verdict": _badge(b.get("verdict", "—")), "bt_trades": b.get("trades", 0),
            "bt_per_day": b.get("per_day"), "bt_ret": b.get("avg_ret"), "bt_excess": b.get("avg_excess"),
            "bt_beat": b.get("beat"), "bt_t": b.get("t"), "bt_placebo": b.get("placebo"), "bt_why": b.get("why", ""),
            "lv_verdict": _badge(v.get("verdict", f"Too early (0/{lab.LIVE_MIN_DAYS} days)")),
            "lv_days": v.get("days", 0), "lv_trades": v.get("trades", 0), "lv_excess": v.get("avg_excess"),
            "lv_beat": v.get("beat"),
        })
    return pd.DataFrame(rows)


def _badge(v: str) -> str:
    return {"Passes": "✅ Passes", "Fails": "❌ Fails"}.get(v, f"⏳ {v}" if v.startswith("Too early") else v)


@st.cache_resource
def today_memo() -> dict:
    """Today's computed signals per (session, reached signal times), shared by every visitor."""
    return {}


# ---------------------------------------------------------------------------------------------- page

st.header("Strategy lab · 策略实验室", anchor=False)
st.caption("Eight A-share strategy variants with fixed rules, each judged the same way: did it beat simply buying every "
           "stock at the same moment, after trading costs? One year of backtest rebuilt from 30-minute bars, then a "
           "live record kept from 12 Oct 2026, written down the day it happens.")

mt = _mtime()
D = data(mt)
if D["backtest"].empty:
    st.info("No backtest saved yet. Build it with `python scripts/strategy_lab.py fetch` and then "
            "`python scripts/strategy_lab.py backtest`.", icon=":material/info:")
    st.stop()
meta = D["meta"]

with st.container(horizontal=True, vertical_alignment="bottom"):
    exit_ = st.segmented_control("Sell at", list(EXITS), default="t1_close", format_func=EXIT_LABEL.get,
                                 key="lab_exit", required=True,
                                 help="A-shares are T+1: a stock bought today can be sold tomorrow at the earliest.")
    bench = st.segmented_control("Benchmark", list(lab.BENCHES), default="ew", format_func=lab.BENCHES.get,
                                 key="lab_bench", required=True,
                                 help="Equal-weight market: buy every eligible stock at the same moment and sell at "
                                      "the same time. It removes the market's move and China's overnight effect.")
    rules = st.segmented_control("Live rules", ["all", "core"], default="all", key="lab_rules", required=True,
                                 format_func={"all": "All rules", "core": "Backtestable rules only"}.get,
                                 help="Main-fund flow (尾盘选股) and auction volume (竞价选股) have no history, so the "
                                      "backtest skips them. 'Backtestable only' compares the live record like for "
                                      "like with the backtest.")

sc = card(mt, exit_, bench, rules)
st.subheader("Scorecard", anchor=False)
st.caption(f"Backtest **{meta.get('start')} → {meta.get('end')}** ({meta.get('sessions')} sessions, "
           f"{meta.get('stocks', 0):,} stocks). Live since **{lab.LIVE_START:%d %b %Y}**, last recorded "
           f"{meta.get('live_updated', 'not yet')}. Returns after **{lab.ROUND_TRIP:.2%}** round-trip costs; excess "
           f"is against the **{lab.BENCHES[bench]}**.")
st.dataframe(sc.drop(columns=["bt_why"]), hide_index=True, column_config={
    "Strategy": P.TextColumn("Strategy", width="medium"), "Signal": P.TextColumn("Signal"),
    "bt_verdict": P.TextColumn("Backtest"), "bt_trades": P.NumberColumn("Trades", format="%d"),
    "bt_per_day": P.NumberColumn("Picks / day", format="%.1f"),
    "bt_ret": P.NumberColumn("Avg return", format="percent", help="Per trade, after costs."),
    "bt_excess": P.NumberColumn("Avg excess", format="percent", help="Per trade, after costs, minus the benchmark."),
    "bt_beat": P.NumberColumn("Beat market", format="percent", help="Share of trades that beat the benchmark."),
    "bt_t": P.NumberColumn("t-stat", format="%.1f", help="Of the daily average excess. 2 or more = unlikely luck."),
    "bt_placebo": P.NumberColumn("vs random", format="percent",
                                 help="Share of random pickers (same number of stocks each day) it beats."),
    "lv_verdict": P.TextColumn("Live"), "lv_days": P.NumberColumn("Live days", format="%d"),
    "lv_trades": P.NumberColumn("Live trades", format="%d"),
    "lv_excess": P.NumberColumn("Live excess", format="percent"),
    "lv_beat": P.NumberColumn("Live beat", format="percent")})
fails = sc[sc["bt_verdict"].str.contains("Fails")]
if not fails.empty:
    with st.expander("Why each backtest failed", icon=":material/help:"):
        for r in fails.itertuples():
            st.markdown(f"- **{r.Strategy}**: {r.bt_why}")
st.caption("A strategy passes when its average excess is positive; its t-statistic is at least 2 or it beats 95% of "
           "random pickers; it beats the market on more than half its trades; and at least 2 of 3 sub-periods are "
           f"positive. Live verdicts wait for {lab.LIVE_MIN_DAYS} signal days.")


# ---------------------------------------------------------------------------------------------- today

def _today_panel() -> None:
    st.subheader("Today · 实时信号", anchor=False)
    if sample.is_sample():
        st.caption("Today's signals need live data - turn on **Live data** in the sidebar.")
        return
    day = checkpoint.latest_session_day("CN")
    now = now_bj()
    live_day = day == now.date()
    s = status("CN")
    if live_day and s.phase in ("pre-open", "before open"):
        st.caption("The first signal (09:25 auction) is read from 09:31 Beijing.")
        return
    recorded = D["live"][D["live"]["day"] == pd.Timestamp(day)] if not D["live"].empty else pd.DataFrame()
    if not recorded.empty and not live_day:
        st.caption(f"Signals of **{pd.Timestamp(day):%a %d %b}**, as recorded in the live log.")
        _show_today(recorded, {}, day, None)
        return
    times = [t for t in lab.SIGNAL_ORDER if not live_day or lab.reached(t, now)]
    if not times:
        st.caption("No signal time has passed yet today.")
        return
    key = ("lab_today", str(day), tuple(times))
    memo = today_memo()
    if key not in memo:
        def run(progress) -> None:
            from stockrt import realtime

            memo[key] = lab.today(realtime.market_snapshot("CN"), progress, workers=checkpoint.WORKERS)

        job = job_runner().submit(key, run)
        if job.active:
            @st.fragment(run_every="3s")
            def watch() -> None:
                if job.active:
                    st.progress(job.progress, text=f"Reading today's signals: {job.message}")
                else:
                    st.rerun()

            watch()
            return
        if job.state == "failed":
            st.warning(f"Today's signals couldn't be read ({job.error}).", icon=":material/warning:")
            return
    res = memo.get(key)
    if res is None:
        return
    when = "today" if live_day else f"{pd.Timestamp(day):%a %d %b}"
    st.caption(f"Signals for **{when}** · computed {res['computed']} Beijing from {res['candidates']} shortlisted "
               "stocks. Each list is fixed once its signal time has passed; the daily update records it in the live "
               "log after the close.")
    if res["cut"] is None:
        st.caption("超跌反弹 needs the volume books of the 5 previous sessions to rank the market; not available.")
    _show_today(res["picks"], res["pending"], day, res)


def _show_today(picks: pd.DataFrame, pending: dict, day: date, res: dict | None) -> None:
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
                if st_.known_at:
                    st.markdown(f"**{st_.title}** :gray-badge[final at {st_.known_at}]")
                    st.caption(f"The 14:30 首板 picks whose seal breaks before the close; known at {st_.known_at}.")
                else:
                    st.markdown(f"**{st_.title}** :gray-badge[at {pending[sid]}]")
                    st.caption(_until(pending[sid]))
                continue
            p = picks[picks["strategy"] == sid] if not picks.empty else picks
            n_all = int((p["all"] == True).sum()) if not p.empty else 0  # noqa: E712 - object column
            badge = f":blue-badge[{len(p)} picks]" if len(p) else ":gray-badge[no picks]"
            st.markdown(f"**{st_.title}** {badge}")
            if st_.live_only and len(p):
                unknown = int(p["all"].isna().sum())
                st.caption(f"{n_all} also pass the live-only rules ({', '.join(r.label for r in st_.live_only)})"
                           + (f"; {unknown} couldn't be checked" if unknown else "") + ".")
            if p.empty:
                st.caption(f"Nothing met every rule at {st_.time_label}.")
                continue
            t = p.assign(now=p["code"].map(now_px))
            t["since"] = t["now"] / t["price"] - 1
            t["live"] = t["all"].map(lambda v: "✓" if v is True or v == True else ("✗" if v is False or v == False  # noqa: E712
                                                                                else "?"))
            show = ["code", "name", "price", "pct_change", "now", "since"] + (["live"] if st_.live_only else [])
            st.dataframe(style_signed(t[show], ["pct_change", "since"]), hide_index=True, height=min(35 * len(t) + 38, 260),
                         column_config={"code": P.TextColumn("Code"), "name": P.TextColumn("Name"),
                                        "price": P.NumberColumn(f"Price at {st_.time_label}", format="%.2f"),
                                        "pct_change": P.NumberColumn("Change then", format="%+.2f%%"),
                                        "now": P.NumberColumn("Now", format="%.2f"),
                                        "since": P.NumberColumn("Since signal", format="percent"),
                                        "live": P.TextColumn("Live rules", help="✓ passes the live-only rules, ✗ "
                                                                                "fails them, ? data unavailable")})


def _until(t: str) -> str:
    if t == "open":
        return "Read from 09:31."
    now = now_bj()
    hh, mm = map(int, t.split(":"))
    left = (hh * 60 + mm) - (now.hour * 60 + now.minute)
    if left <= 0:
        return f"Signal time {t} has passed - refreshing."
    return f"Signal at {t} Beijing (in {left // 60} h {left % 60} min)." if left >= 60 else \
        f"Signal at {t} Beijing (in {left} min)."


_today_panel()


# ---------------------------------------------------------------------------------------------- detail

st.subheader("One strategy in detail", anchor=False)
sid = st.selectbox("Strategy", list(STRATEGIES), format_func=lambda k: STRATEGIES[k].title, key="lab_sid")
S = STRATEGIES[sid]
tab_perf, tab_rules, tab_mood, tab_log = st.tabs(["Performance", "Rules & why", "Market mood split", "Pick log"])

with tab_perf:
    bt_d = lab.daily(D["backtest"], D["days"], sid, exit_, bench, "core").assign(period="Backtest")
    lv_d = lab.daily(D["live"], D["days"], sid, exit_, bench, rules).assign(period="Live") \
        if not D["live"].empty else pd.DataFrame()
    if not lv_d.empty and not bt_d.empty:
        lv_d["cum_excess"] = lv_d["excess"].cumsum() + bt_d["cum_excess"].iloc[-1]
    both = pd.concat([bt_d, lv_d], ignore_index=True)
    if both.empty:
        st.caption("No signals.")
    else:
        pal = palette()
        color = alt.Color("period:N", title=None, scale=alt.Scale(domain=["Backtest", "Live"], range=[pal[0], pal[1]]),
                          legend=alt.Legend(orient="top"))
        base = alt.Chart(both).encode(x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)))
        line = base.mark_line(strokeWidth=1.8).encode(
            y=alt.Y("cum_excess:Q", title="Cumulative excess (sum of daily %)", axis=alt.Axis(format="%")),
            color=color, tooltip=[alt.Tooltip("day:T", title="Session"), alt.Tooltip("n:Q", title="Picks"),
                                  alt.Tooltip("ret:Q", title="Avg return (gross)", format="+.2%"),
                                  alt.Tooltip("bench:Q", title="Benchmark", format="+.2%"),
                                  alt.Tooltip("excess:Q", title="Excess after costs", format="+.2%"),
                                  alt.Tooltip("cum_excess:Q", title="Cumulative", format="+.1%")])
        zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED).encode(y="y:Q")
        st.altair_chart(alt.layer(zero, line).properties(height=300), width="stretch")
        st.caption("Each point adds that session's average excess (after costs, against the benchmark). Rising = "
                   "beating the market; a flat or falling line = no edge.")
        up, down = up_down_colors()
        m = both.assign(month=both["day"].dt.to_period("M").dt.to_timestamp()).groupby(["month", "period"]).agg(
            excess=("excess", "mean"), days=("excess", "size")).reset_index()
        bars = alt.Chart(m).mark_bar().encode(
            x=alt.X("month:T", title=None, timeUnit="yearmonth", axis=alt.Axis(grid=False, labelColor=MUTED)),
            y=alt.Y("excess:Q", title="Average daily excess", axis=alt.Axis(format="%")),
            color=alt.condition("datum.excess > 0", alt.value(up), alt.value(down)),
            tooltip=[alt.Tooltip("month:T", title="Month", format="%b %Y"), alt.Tooltip("days:Q", title="Signal days"),
                     alt.Tooltip("excess:Q", title="Avg excess", format="+.2%")])
        st.altair_chart(bars.properties(height=180), width="stretch")
        rows = []
        for label, tr, live, rl in (("Backtest", D["backtest"], False, "core"), ("Live", D["live"], True, rules)):
            if tr.empty:
                continue
            for x in EXITS:
                mm = lab.metrics(tr, D["days"], sid, x, bench, rl, live)
                rows.append({"Period": label, "Sell at": EXIT_LABEL[x], "Trades": mm["trades"],
                             "Avg return": mm.get("avg_ret"), "Avg excess": mm.get("avg_excess"),
                             "Median excess": mm.get("median_excess"), "Win rate": mm.get("win"),
                             "Beat market": mm.get("beat"), "t": mm.get("t"), "vs random": mm.get("placebo"),
                             "Sub-periods +": mm.get("thirds"), "Max drawdown": mm.get("max_dd"),
                             "Verdict": mm["verdict"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
            c: P.NumberColumn(c, format="percent") for c in ("Avg return", "Avg excess", "Median excess", "Win rate",
                                                            "Beat market", "vs random", "Max drawdown")}
            | {"t": P.NumberColumn("t", format="%.1f")})

with tab_rules:
    st.markdown(f"**Signal:** {S.time_label} Beijing · **Entry:** {S.entry} · **Exit:** the next open, the next "
                "close, or the close two sessions later (T+1 rule).")
    st.markdown("**Rules** (all must hold):\n" + "\n".join(f"- {r.label}" for r in S.rules)
                + "\n- Buyable at the entry price" + (" (not sealed at the limit since the open)" if S.limit_entry
                                                      else " (not at the limit-up price)"))
    if S.live_only:
        st.markdown("**Live only** (no history, so the backtest skips them):\n"
                    + "\n".join(f"- {r.label}" for r in S.live_only))
    st.markdown(f"**Why:** {S.why}")
    if S.sources:
        st.markdown("**Research:** " + " · ".join(f"[{t}]({u})" for t, u in S.sources))

with tab_mood:
    mm = lab.metrics(D["backtest"], D["days"], sid, exit_, bench, "core")
    c1, c2 = st.columns(2)
    c1.metric("Warm days · 情绪好", f"{mm.get('warm_excess', np.nan):+.2%}" if mm.get("warm_days") else "—",
              help="Average daily excess on sessions when yesterday's limit-up stocks were up on average at the "
                   "signal time.", border=True)
    c1.caption(f"{mm.get('warm_days', 0)} signal days")
    c2.metric("Cold days · 情绪差", f"{mm.get('cold_excess', np.nan):+.2%}" if mm.get("cold_days") else "—",
              border=True)
    c2.caption(f"{mm.get('cold_days', 0)} signal days")
    st.caption("短线情绪 filter: practitioners only trade momentum when short-term sentiment is warm. Here 'warm' "
               "means yesterday's limit-up stocks were up on average at this strategy's signal time - something "
               "known at that moment, so it could be used as a switch. Backtest, chosen exit and benchmark.")

with tab_log:
    tr = pd.concat([D["live"].assign(period="Live") if not D["live"].empty else None,
                    D["backtest"].assign(period="Backtest")], ignore_index=True)
    tr = tr[tr["strategy"] == sid].sort_values(["day", "code"], ascending=[False, True])
    cols = ["period", "day", "code", "name", "price", "pct_change", "volume_ratio", "turnover_rate", "float_mcap",
            "all", *[f"ret_{x}" for x in EXITS]]
    shown = tr[cols].head(300).copy()
    shown["day"] = shown["day"].dt.date
    shown["all"] = shown["all"].map(lambda v: "✓" if v is True or v == True else ("✗" if v is False or v == False  # noqa: E712
                                                                             else "—"))
    st.dataframe(style_signed(shown, ["pct_change", *[f"ret_{x}" for x in EXITS]]), hide_index=True, column_config={
        "period": P.TextColumn(""), "day": P.DateColumn("Session"), "code": P.TextColumn("Code"),
        "name": P.TextColumn("Name"), "price": P.NumberColumn("Entry", format="%.2f"),
        "pct_change": P.NumberColumn("Change at signal", format="%+.2f%%"),
        "volume_ratio": P.NumberColumn("Vol. ratio", format="%.2f"),
        "turnover_rate": P.NumberColumn("Turnover", format="%.2f%%"),
        "float_mcap": P.NumberColumn("Float cap", format="compact"), "all": P.TextColumn("Live rules"),
        **{f"ret_{x}": P.NumberColumn(EXIT_LABEL[x], format="percent", help="Gross, before costs") for x in EXITS}})
    st.caption(f"Newest 300 of {len(tr):,} picks. Returns are gross (before the {lab.ROUND_TRIP:.2%} costs); blank = "
               "not yet known, or dropped (suspended, or an ex-dividend date inside the holding window).")
    st.download_button(f"All {sid} picks (CSV)", to_csv_bytes(tr[cols]), file_name=f"strategy_lab_{sid}.csv",
                       mime="text/csv", on_click="ignore", icon=":material/download:")


# ---------------------------------------------------------------------------------------------- market checks

with st.expander("Market checks: overnight effect, intraday momentum, 短线情绪", icon=":material/insights:"):
    days = D["days"]
    o = days[days["time"] == "open"].dropna(subset=["ew_overnight", "ew_open_close"]).sort_values("day")
    if not o.empty:
        st.markdown("**Overnight vs daytime** (average stock, equal weight). Research says China's overnight return "
                    "is negative and its daytime return positive - the reverse of the US - because of the T+1 rule.")
        c1, c2 = st.columns(2)
        c1.metric("Close → next open", f"{o['ew_overnight'].mean():+.3%}", help="Average per session.", border=True)
        c2.metric("Open → close", f"{o['ew_open_close'].mean():+.3%}", help="Average per session.", border=True)
        long = pd.concat([pd.DataFrame({"day": o["day"], "what": "Close → next open", "v": o["ew_overnight"].cumsum()}),
                          pd.DataFrame({"day": o["day"], "what": "Open → close", "v": o["ew_open_close"].cumsum()})])
        pal = palette()
        st.altair_chart(alt.Chart(long).mark_line(strokeWidth=1.8).encode(
            x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)),
            y=alt.Y("v:Q", title="Cumulative (sum)", axis=alt.Axis(format="%")),
            color=alt.Color("what:N", title=None, scale=alt.Scale(range=[pal[0], pal[1]]), legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("day:T", title="Session"), alt.Tooltip("what:N", title="Part"),
                     alt.Tooltip("v:Q", title="Cumulative", format="+.1%")]).properties(height=220), width="stretch")
    mom = D["momentum"]
    ms = lab.momentum_stats(mom) if not mom.empty else {}
    if ms:
        st.markdown("**CSI 300 intraday momentum** - does the move up to 10:00 (including the overnight gap) predict "
                    "the last half hour (14:30 → 15:00)? Tradable only through index futures (stocks are T+1).")
        c1, c2, c3 = st.columns(3)
        c1.metric("Correlation", f"{ms['corr']:+.2f}", help=f"{ms['sessions']} sessions", border=True)
        c2.metric("Slope t-stat", f"{ms['t']:+.1f}", help="2 or more = unlikely luck.", border=True)
        c3.metric("Same direction", f"{ms['same_sign']:.0%}", help="Sessions where both moves had the same sign.",
                  border=True)
        st.caption(f"Following the sign of the move to 10:00 in the last half hour averaged {ms['rule_bp']:+.1f} bp "
                   f"a session (t {ms['rule_t']:+.1f}), before costs.")
    sent = days[days["time"] == "14:30"].sort_values("day")
    if not sent.empty:
        st.markdown("**短线情绪 at 14:30** - how yesterday's limit-up stocks are doing today (the warm / cold switch), "
                    "and the share of stocks that touched the limit-up but fell back (炸板率).")
        c1, c2 = st.columns(2)
        for col, field, title, scale in ((c1, "yday_lu_avg", "Yesterday's limit-ups: average move (%)", 1),
                                         (c2, "zhaban_rate", "炸板率: touched the limit, fell back (%)", 100)):
            dd = sent[["day", field]].dropna().assign(v=lambda x, f=field, k=scale: x[f] * k)
            ch = alt.Chart(dd).mark_line(strokeWidth=1.2, color=palette()[0]).encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)),
                y=alt.Y("v:Q", title=None), tooltip=[alt.Tooltip("day:T", title="Session"),
                                                     alt.Tooltip("v:Q", title=title, format=".1f")])
            zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED).encode(y="y:Q")
            col.caption(title)
            col.altair_chart(alt.layer(zero, ch).properties(height=180), width="stretch")
        st.caption(f"Warm sessions (yesterday's limit-ups up on average at 14:30): "
                   f"{int((sent['yday_lu_avg'] > 0).sum())} of {int(sent['yday_lu_avg'].notna().sum())}. "
                   f"Average limit-up streak high (连板高度): {sent['max_streak'].mean():.1f}.")

with st.expander("How the testing works", icon=":material/rule:"):
    st.markdown(f"""
- **Data.** Tencent's 30-minute bars (one year kept) rebuild every stock as it stood at the open, 10:00, 10:30,
  14:00 and 14:30; daily bars give the history (moving averages, 20-day high, 5-day return, float shares).
  The 14:30 rebuild was checked against the front page's minute-by-minute replay of a real session.
- **Universe** for every strategy and the benchmark: non-ST Shanghai / Shenzhen A-shares with 60+ sessions of
  history. Beijing-exchange stocks have no 30-minute bars.
- **Benchmarks.** Equal-weight market = every eligible stock bought at the same moment and sold at the same time.
  CSI 300 and CSI 1000 over the same window.
- **Costs**, deducted from every trade ({lab.ROUND_TRIP:.3%} round trip):
""" + "\n".join(f"  - {k}: {v:.3%}" for k, v in lab.COSTS.items()) + """
- **Random pickers.** Each session, the same number of stocks drawn at random from the universe. "vs random" is
  the share of such pickers the strategy beats (computed exactly from each session's spread of returns).
- **t-stat** of the daily average excess (picks on one day move together, so days, not trades, are counted).
- **Not modelled.** An order at the limit-up price may not fill (首板 assumes it does); a stock at limit-down may not
  be sellable; ST status and the stock list are today's (stocks delisted during the year are missing). Trades
  spanning an ex-dividend date or a suspension are dropped.
- **Live record.** After each close the daily update (GitHub Actions, or `python scripts/daily_update.py`)
  records the session's picks with every rule - including main-fund flow and auction volume - and fills in exits
  as they happen. Picks are never changed afterwards.
""")
    st.caption("Research tooling, not investment advice.")
