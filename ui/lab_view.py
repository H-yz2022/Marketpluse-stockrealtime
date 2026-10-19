"""Strategy lab · 策略实验室 - the last section of the A-share front page, loaded only when opened.

Built to stay light: opening it reads one small scorecard file; the big files (the ~80,000 backtest
trades, a strategy's candidate pool) load only for the view that needs them. It runs as a fragment, so
moving a slider re-scores the lab without reloading the rest of the front page.
"""

from datetime import date

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from stockrt import checkpoint, lab, labpools, published, sample
from stockrt.calendar import now_bj, status
from stockrt.export import to_csv_bytes
from stockrt.labdata import EXIT_LABEL, EXITS
from stockrt.strategies import STRATEGIES
from ui.charts import MUTED, palette
from ui.fmt import style_signed, up_down_colors

P = st.column_config
VIEWS = {
    "card": "Scorecard",
    "custom": "Try your own parameters · 自定义参数",
    "today": "Today · 实时信号",
    "detail": "One strategy",
    "checks": "Market checks",
    "method": "How it's tested",
}


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


@st.cache_data(max_entries=1, show_spinner="Loading the candidate pool…")
def _pool(sid: str) -> pd.DataFrame:
    return labpools.load(sid)


@st.cache_data(max_entries=1, show_spinner=False)
def _names() -> dict[str, str]:
    try:
        u = pd.read_parquet(sample.SAMPLE_DIR / "universe.parquet")
        return dict(zip(u["code"], u["name"]))
    except Exception:  # noqa: BLE001 - names are a nicety
        return {}


@st.cache_resource
def today_memo() -> dict:
    """Today's computed signals per (session, reached signal times), shared by every visitor."""
    return {}


def _badge(v: str) -> str:
    return {"Passes": "✅ Passes", "Fails": "❌ Fails"}.get(v, f"⏳ {v}" if str(v).startswith("Too early") else v)


@st.fragment
def render() -> None:
    st.caption("Eight A-share strategy variants with fixed rules, each judged the same way: did it beat simply buying "
               "every stock at the same moment, after trading costs? One year of backtest rebuilt from 30-minute bars, "
               "then a live record kept from 12 Oct 2026. Set your own parameters under **Try your own parameters**.")
    stamp = _stamp()
    meta = lab.meta()
    if not meta:
        st.info("No Strategy lab results yet.", icon=":material/info:")
        return
    view = st.segmented_control("Show", list(VIEWS), format_func=VIEWS.get, default="card", key="lab_view",
                                required=True)
    if view in ("card", "custom", "detail"):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            exit_ = st.segmented_control("Sell at", list(EXITS), default="t1_close", format_func=EXIT_LABEL.get,
                                         key="lab_exit", required=True,
                                         help="A-shares are T+1: a stock bought today can be sold tomorrow at the "
                                              "earliest.")
            bench = st.segmented_control("Benchmark", list(lab.BENCHES), default="ew", format_func=lab.BENCHES.get,
                                         key="lab_bench", required=True,
                                         help="Equal-weight market: buy every eligible stock at the same moment and "
                                              "sell at the same time. It removes the market's move and China's "
                                              "overnight effect.")
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
    rules = st.segmented_control("Live rules", ["all", "core"], default="all", key="lab_rules", required=True,
                                 format_func={"all": "All rules", "core": "Backtestable rules only"}.get,
                                 help="Main-fund flow (尾盘选股) and auction volume (竞价选股) have no history, so the "
                                      "backtest skips them. 'Backtestable only' compares the live record like for "
                                      "like with the backtest.")
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
            "Strategy": st_.title, "Signal": st_.time_label, "bt_verdict": _badge(b.get("verdict", "—")),
            "bt_trades": b.get("trades", 0), "bt_per_day": b.get("per_day"), "bt_ret": b.get("avg_ret"),
            "bt_excess": b.get("avg_excess"), "bt_beat": b.get("beat"), "bt_t": b.get("t"),
            "bt_placebo": b.get("placebo"), "why": b.get("why", ""),
            "lv_verdict": _badge(v.get("verdict", f"Too early (0/{lab.LIVE_MIN_DAYS} days)")),
            "lv_days": v.get("days", 0), "lv_trades": v.get("trades", 0), "lv_excess": v.get("avg_excess"),
            "lv_beat": v.get("beat")})
    tbl = pd.DataFrame(rows)
    st.caption(f"Backtest **{meta.get('start')} → {meta.get('end')}** ({meta.get('sessions')} sessions, "
               f"{meta.get('stocks', 0):,} stocks). Live since **{lab.LIVE_START:%d %b %Y}**, last recorded "
               f"{meta.get('live_updated', 'not yet')}. Returns after **{lab.ROUND_TRIP:.2%}** round-trip costs; "
               f"excess is against the **{lab.BENCHES[bench]}**.")
    st.dataframe(tbl.drop(columns=["why"]), hide_index=True, column_config={
        "Strategy": P.TextColumn("Strategy", width="medium"), "Signal": P.TextColumn("Signal"),
        "bt_verdict": P.TextColumn("Backtest"), "bt_trades": P.NumberColumn("Trades", format="%d"),
        "bt_per_day": P.NumberColumn("Picks / day", format="%.1f"),
        "bt_ret": P.NumberColumn("Avg return", format="percent", help="Per trade, after costs."),
        "bt_excess": P.NumberColumn("Avg excess", format="percent",
                                    help="Per trade, after costs, minus the benchmark."),
        "bt_beat": P.NumberColumn("Beat market", format="percent", help="Share of trades that beat the benchmark."),
        "bt_t": P.NumberColumn("t-stat", format="%.1f", help="Of the daily average excess. 2 or more = unlikely luck."),
        "bt_placebo": P.NumberColumn("vs random", format="percent",
                                     help="Share of random pickers (same number of stocks each day) it beats."),
        "lv_verdict": P.TextColumn("Live"), "lv_days": P.NumberColumn("Live days", format="%d"),
        "lv_trades": P.NumberColumn("Live trades", format="%d"),
        "lv_excess": P.NumberColumn("Live excess", format="percent"),
        "lv_beat": P.NumberColumn("Live beat", format="percent")})
    fails = tbl[tbl["bt_verdict"].str.contains("Fails")]
    if not fails.empty:
        with st.expander("Why each backtest failed", icon=":material/help:"):
            for r in fails.itertuples():
                st.markdown(f"- **{r.Strategy}**: {r.why}")
    st.caption("A strategy passes when its average excess is positive; its t-statistic is at least 2 or it beats "
               "95% of random pickers; it beats the market on more than half its trades; and at least 2 of 3 "
               f"sub-periods are positive. Live verdicts wait for {lab.LIVE_MIN_DAYS} signal days.")


# ---------------------------------------------------------------------------------------------- your parameters

CUSTOM_TITLES = {"S1": STRATEGIES["S1"].title, "S1b": STRATEGIES["S1b"].title, "S2": STRATEGIES["S2"].title,
                 "S3": STRATEGIES["S3"].title, "S4": "S4 · 首板 · First limit-up (fill: optimistic or pessimistic)",
                 "S5": STRATEGIES["S5"].title, "S6": STRATEGIES["S6"].title}


def _widget(sid: str, p: labpools.Param):
    key = f"lab_p_{sid}_{p.key}"
    if p.kind == "range":
        return st.slider(p.label, float(p.lo), float(p.hi), value=tuple(map(float, p.default)), step=float(p.step),
                         key=key, help=p.help or None)
    if p.kind in ("min", "max"):
        return st.slider(p.label, p.lo, p.hi, value=p.default, step=p.step, key=key, help=p.help or None)
    if p.kind == "toggle":
        return st.toggle(p.label, value=bool(p.default), key=key, help=p.help or None)
    fmt = {"assume": "Assume it fills", "seal_breaks": "Only if the seal breaks", 0: "Off", 1: "Today", 2: "2 sessions",
           3: "3 sessions"}
    return st.segmented_control(p.label, list(p.options), default=p.default, key=key, required=True,
                                format_func=lambda o: fmt.get(o, str(o)), help=p.help or None)


def _custom(stamp: str, exit_: str, bench: str) -> None:
    sid = st.selectbox("Strategy", list(labpools.SPECS), format_func=CUSTOM_TITLES.get, key="lab_custom_sid")
    spec = labpools.SPECS[sid]
    if st.button("Reset to the official rules", icon=":material/restart_alt:", key=f"lab_reset_{sid}"):
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
    pool = _pool(sid)
    if pool.empty:
        st.info("The candidate pools aren't installed (research/lab_pools/).", icon=":material/info:")
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
    rows = []
    for label, m in (("Official rules", m_off), ("Your settings" if not is_official else "Your settings (= official)",
                                                 m_you)):
        rows.append({"": label, "Trades": m.get("trades"), "Picks / day": m.get("per_day"),
                     "Avg return": m.get("avg_ret"), "Avg excess": m.get("avg_excess"), "Beat market": m.get("beat"),
                     "t": m.get("t"), "vs random": m.get("placebo"), "Sub-periods +": m.get("thirds"),
                     "Verdict": _badge(m.get("verdict", "—"))})
    tbl = pd.DataFrame(rows)
    for c in tbl.columns.drop(["", "Verdict"]):
        tbl[c] = pd.to_numeric(tbl[c], errors="coerce")
    st.dataframe(tbl, hide_index=True, column_config={
        c: P.NumberColumn(c, format="percent") for c in ("Avg return", "Avg excess", "Beat market", "vs random")}
        | {"t": P.NumberColumn("t", format="%.1f"), "Picks / day": P.NumberColumn("Picks / day", format="%.1f"),
           "Trades": P.NumberColumn("Trades", format="%d"), "Sub-periods +": P.NumberColumn("Sub-periods +",
                                                                                            format="%d")})
    if not is_official and m_you.get("why"):
        st.caption(f"Your settings: {m_you['verdict']} - {m_you['why']}.")
    curves = []
    for label, t in (("Official rules", base), ("Your settings", yours)):
        d = lab.daily(t.assign(strategy=official_id), days, official_id, exit_, bench)
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
                y=alt.Y("cum_excess:Q", title="Cumulative excess (sum of daily %)", axis=alt.Axis(format="%")),
                color=alt.Color("who:N", title=None, scale=alt.Scale(domain=["Official rules", "Your settings"],
                                                                     range=[pal[0], pal[1]]),
                                legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("day:T", title="Session"), alt.Tooltip("who:N", title=""),
                         alt.Tooltip("n:Q", title="Picks"), alt.Tooltip("excess:Q", title="Excess", format="+.2%"),
                         alt.Tooltip("cum_excess:Q", title="Cumulative", format="+.1%")])).properties(height=260),
            width="stretch")
    st.warning("Trying many settings on the same year will always find some that look good by chance - the best "
               "of twenty tries usually is. Treat a good-looking setting as an idea to watch, not proof: it has to "
               "work on sessions it hasn't seen (the live record) before it means anything.",
               icon=":material/science:")
    names = _names()
    log = yours.sort_values(["day", "code"], ascending=[False, True]).copy()
    log["name"] = log["code"].astype(str).map(names)
    keep = ["day", "code", "name"] + [c for c in log.columns if c not in ("day", "code", "name", "strategy", "all")]
    st.download_button(f"Your picks for the year ({len(log):,}, CSV)", to_csv_bytes(log[keep]),
                       file_name=f"lab_{sid}_custom.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    st.caption("Computed from the strategy's candidate pool (every stock-session near the rules over the backtest "
               "year; returns kept in whole basis points). The official row is the official backtest itself.")


# ---------------------------------------------------------------------------------------------- today

def _today(stamp: str) -> None:
    if sample.is_sample():
        st.caption("Today's signals need live data - turn on **Live data** in the sidebar.")
        return
    from ui.picks_view import job_runner

    day = checkpoint.latest_session_day("CN")
    now = now_bj()
    live_day = day == now.date()
    s = status("CN")
    if live_day and s.phase in ("pre-open", "before open"):
        st.caption("The first signal (09:25 auction) is read from 09:31 Beijing.")
        return
    live = _file(stamp, "live_trades.parquet")
    recorded = live[live["day"] == pd.Timestamp(day)] if not live.empty else pd.DataFrame()
    over = not live_day or s.phase == "closed"
    if not recorded.empty and over:
        st.caption(f"Signals of **{pd.Timestamp(day):%a %d %b}**, as recorded in the live log after the close "
                   "(fixed).")
        _show_today(recorded, {}, day)
        return
    times = [t for t in lab.SIGNAL_ORDER if not live_day or lab.reached(t, now)]
    if not times:
        st.caption("No signal time has passed yet today.")
        return
    key = ("lab_today", str(day), tuple(times))
    memo = today_memo()
    if over and published.await_close("CN", day):
        st.info("The market has closed. Today's signals are recorded once by the daily update and then shown here, "
                "fixed.", icon=":material/lock_clock:")
        earlier = [k for k in memo if k[:2] == key[:2]]
        if earlier:
            res = memo[max(earlier, key=lambda k: len(k[2]))]
            st.caption(f"Provisional, computed during the session at {res['computed']} Beijing:")
            _show_today(res["picks"], res["pending"], day)
        return
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
               "stocks. Each list is fixed once its signal time has passed; the daily update records it after the "
               "close.")
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
                label = f"final at {st_.known_at}" if st_.known_at else f"at {pending[sid]}"
                st.markdown(f"**{st_.title}** :gray-badge[{label}]")
                continue
            p = picks[picks["strategy"] == sid] if not picks.empty else picks
            st.markdown(f"**{st_.title}** " + (f":blue-badge[{len(p)} picks]" if len(p) else ":gray-badge[no picks]"))
            if st_.live_only and len(p):
                n_all = int((p["all"] == True).sum())  # noqa: E712 - object column
                st.caption(f"{n_all} also pass the live-only rules ({', '.join(r.label for r in st_.live_only)}).")
            if p.empty:
                continue
            t = p.assign(now=p["code"].map(now_px))
            t["since"] = t["now"] / t["price"] - 1
            show = ["code", "name", "price", "pct_change", "now", "since"]
            st.dataframe(style_signed(t[show], ["pct_change", "since"]), hide_index=True,
                         height=min(35 * len(t) + 38, 260),
                         column_config={"code": P.TextColumn("Code"), "name": P.TextColumn("Name"),
                                        "price": P.NumberColumn(f"Price at {st_.time_label}", format="%.2f"),
                                        "pct_change": P.NumberColumn("Change then", format="%+.2f%%"),
                                        "now": P.NumberColumn("Now", format="%.2f"),
                                        "since": P.NumberColumn("Since signal", format="percent")})


# ---------------------------------------------------------------------------------------------- one strategy

def _detail(stamp: str, exit_: str, bench: str) -> None:
    sid = st.selectbox("Strategy", list(STRATEGIES), format_func=lambda k: STRATEGIES[k].title, key="lab_sid")
    S = STRATEGIES[sid]
    part = st.segmented_control("Part", ["Performance", "Rules & why", "Market mood split", "Pick log"],
                                default="Performance", key="lab_detail_part", required=True)
    days = _file(stamp, "days.parquet")
    if part == "Rules & why":
        st.markdown(f"**Signal:** {S.time_label} Beijing · **Entry:** {S.entry} · **Exit:** the next open, the next "
                    "close, or the close two sessions later (T+1 rule).")
        st.markdown("**Rules** (all must hold):\n" + "\n".join(f"- {r.label}" for r in S.rules)
                    + "\n- Buyable at the entry price" + (" (not sealed at the limit since the open)"
                                                          if S.limit_entry else " (not at the limit-up price)"))
        if S.live_only:
            st.markdown("**Live only** (no history, so the backtest skips them):\n"
                        + "\n".join(f"- {r.label}" for r in S.live_only))
        st.markdown(f"**Why:** {S.why}")
        if S.sources:
            st.markdown("**Research:** " + " · ".join(f"[{t}]({u})" for t, u in S.sources))
        return
    bt_all = _file(stamp, "backtest_trades.parquet")
    bt = bt_all[bt_all["strategy"] == sid]
    lv_all = _file(stamp, "live_trades.parquet")
    lv = lv_all[lv_all["strategy"] == sid] if not lv_all.empty else lv_all
    if part == "Performance":
        bt_d = lab.daily(bt, days, sid, exit_, bench, "core").assign(period="Backtest")
        lv_d = lab.daily(lv, days, sid, exit_, bench, "all").assign(period="Live") if not lv.empty else pd.DataFrame()
        if not lv_d.empty and not bt_d.empty:
            lv_d["cum_excess"] = lv_d["excess"].cumsum() + bt_d["cum_excess"].iloc[-1]
        both = pd.concat([x for x in (bt_d, lv_d) if not x.empty], ignore_index=True)
        if both.empty:
            st.caption("No signals.")
            return
        both["day"] = pd.to_datetime(both["day"])
        pal = palette()
        st.altair_chart(alt.layer(
            alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=MUTED).encode(y="y:Q"),
            alt.Chart(both).mark_line(strokeWidth=1.8).encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)),
                y=alt.Y("cum_excess:Q", title="Cumulative excess (sum of daily %)", axis=alt.Axis(format="%")),
                color=alt.Color("period:N", title=None, scale=alt.Scale(domain=["Backtest", "Live"],
                                                                        range=[pal[0], pal[1]]),
                                legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("day:T", title="Session"), alt.Tooltip("n:Q", title="Picks"),
                         alt.Tooltip("excess:Q", title="Excess after costs", format="+.2%"),
                         alt.Tooltip("cum_excess:Q", title="Cumulative", format="+.1%")])).properties(height=280),
            width="stretch")
        up, down = up_down_colors()
        m = both.assign(month=both["day"].dt.to_period("M").dt.to_timestamp()).groupby(["month", "period"]).agg(
            excess=("excess", "mean"), days=("excess", "size")).reset_index()
        st.altair_chart(alt.Chart(m).mark_bar().encode(
            x=alt.X("month:T", title=None, timeUnit="yearmonth", axis=alt.Axis(grid=False, labelColor=MUTED)),
            y=alt.Y("excess:Q", title="Average daily excess", axis=alt.Axis(format="%")),
            color=alt.condition("datum.excess > 0", alt.value(up), alt.value(down)),
            tooltip=[alt.Tooltip("month:T", title="Month", format="%b %Y"),
                     alt.Tooltip("excess:Q", title="Avg excess", format="+.2%")]).properties(height=170),
            width="stretch")
    elif part == "Market mood split":
        mm = lab.metrics(bt, days, sid, exit_, bench, "core")
        c1, c2 = st.columns(2)
        c1.metric("Warm days · 情绪好", f"{mm.get('warm_excess', np.nan):+.2%}" if mm.get("warm_days") else "—",
                  border=True, help="Sessions when yesterday's limit-up stocks were up on average at the signal time.")
        c2.metric("Cold days · 情绪差", f"{mm.get('cold_excess', np.nan):+.2%}" if mm.get("cold_days") else "—",
                  border=True)
        st.caption(f"Average daily excess · {mm.get('warm_days', 0)} warm and {mm.get('cold_days', 0)} cold "
                   "signal days.")
    else:
        tr = pd.concat([lv.assign(period="Live") if not lv.empty else None, bt.assign(period="Backtest")],
                       ignore_index=True).sort_values(["day", "code"], ascending=[False, True])
        cols = ["period", "day", "code", "name", "price", "pct_change", "volume_ratio", "turnover_rate", "float_mcap",
                *[f"ret_{x}" for x in EXITS]]
        shown = tr[cols].head(300).copy()
        shown["day"] = pd.to_datetime(shown["day"]).dt.date
        st.dataframe(style_signed(shown, ["pct_change", *[f"ret_{x}" for x in EXITS]]), hide_index=True,
                     column_config={"period": P.TextColumn(""), "day": P.DateColumn("Session"),
                                    "price": P.NumberColumn("Entry", format="%.2f"),
                                    "pct_change": P.NumberColumn("Change at signal", format="%+.2f%%"),
                                    "volume_ratio": P.NumberColumn("Vol. ratio", format="%.2f"),
                                    "turnover_rate": P.NumberColumn("Turnover", format="%.2f%%"),
                                    "float_mcap": P.NumberColumn("Float cap", format="compact"),
                                    **{f"ret_{x}": P.NumberColumn(EXIT_LABEL[x], format="percent") for x in EXITS}})
        st.download_button(f"All {sid} picks (CSV)", to_csv_bytes(tr[cols]), file_name=f"strategy_lab_{sid}.csv",
                           mime="text/csv", on_click="ignore", icon=":material/download:")


# ---------------------------------------------------------------------------------------------- market checks

def _checks(stamp: str) -> None:
    days = _file(stamp, "days.parquet")
    o = days[days["time"] == "open"].dropna(subset=["ew_overnight", "ew_open_close"]).sort_values("day")
    if not o.empty:
        st.markdown("**Overnight vs daytime** (average stock). Research says China's overnight return is negative and "
                    "its daytime return positive - the reverse of the US - because of the T+1 rule.")
        c1, c2 = st.columns(2)
        c1.metric("Close → next open", f"{o['ew_overnight'].mean():+.3%}", help="Average per session.", border=True)
        c2.metric("Open → close", f"{o['ew_open_close'].mean():+.3%}", help="Average per session.", border=True)
    mom = _file(stamp, "csi300_intraday.parquet")
    ms = lab.momentum_stats(mom) if not mom.empty else {}
    if ms:
        st.markdown("**CSI 300 intraday momentum** - does the move up to 10:00 predict the last half hour?")
        c1, c2, c3 = st.columns(3)
        c1.metric("Correlation", f"{ms['corr']:+.2f}", help=f"{ms['sessions']} sessions", border=True)
        c2.metric("Slope t-stat", f"{ms['t']:+.1f}", border=True)
        c3.metric("Same direction", f"{ms['same_sign']:.0%}", border=True)
    sent = days[days["time"] == "14:30"].sort_values("day")
    if not sent.empty:
        st.markdown("**短线情绪 at 14:30**: yesterday's limit-ups today, and 炸板率 (touched the limit, fell back).")
        c1, c2 = st.columns(2)
        for col, field, title, k in ((c1, "yday_lu_avg", "Yesterday's limit-ups: average move (%)", 1),
                                     (c2, "zhaban_rate", "炸板率 (%)", 100)):
            dd = sent[["day", field]].dropna()
            dd = dd.assign(v=dd[field] * k)
            col.caption(title)
            col.altair_chart(alt.Chart(dd).mark_line(strokeWidth=1.2, color=palette()[0]).encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(grid=False, labelColor=MUTED)), y=alt.Y("v:Q", title=None),
                tooltip=[alt.Tooltip("day:T", title="Session"), alt.Tooltip("v:Q", title=title, format=".1f")]
            ).properties(height=160), width="stretch")


def _method() -> None:
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
