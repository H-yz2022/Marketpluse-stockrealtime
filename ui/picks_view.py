"""尾盘选股 view shared by the A-share front page and the Hong Kong page.

Built for a free hosting plan, where every request to the mainland sources is slow:

* the newest saved result shows instantly (disk, the repository archive, or the
  bundled sample) - the page is never empty;
* a checkpoint that hasn't been computed yet is rebuilt by a background job while
  that saved result stays on screen, and is swapped in when ready;
* past sessions sit in a collapsed section and load only when opened;
* before the open, the pre-open auction (集合竞价) shows indicative prices.
"""

import dataclasses
from datetime import date

import pandas as pd
import streamlit as st

from stockrt import archive, auction, checkpoint, jobs, prompts, realtime, sample, screener, watchlist
from stockrt.calendar import now_bj, status
from stockrt.export import stamp, to_csv_bytes
from stockrt.symbols import market
from ui.fmt import pct, style_signed

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
    "CN": "Checked at **14:00**, **14:30** and the **15:00 close** (Beijing time; 02:00 / 02:30 / 03:00 in New York "
          "during daylight saving).",
    "HK": "Checked at **15:00**, **15:30** and the **16:00 close** (Hong Kong = Beijing time; 03:00 / 03:30 / 04:00 "
          "in New York during daylight saving) - the same last-hour pattern, since Hong Kong closes an hour later.",
}
HK_ADAPTED = dataclasses.replace(screener.ScreenParams(), turnover_min=0.5)
TIER_TEXT = {
    "A": "A · all rules at every checkpoint",
    "B": "B · all rules at the latest checkpoint",
    "C": "C · earlier, not at the latest",
    "D": "D · one rule narrowly missed",
}
AUCTION = {
    "CN": "A-shares hold a call auction from **09:15 to 09:25** (orders can be cancelled until 09:20). The matched "
          "price becomes the opening price at 09:25 and continuous trading starts at 09:30. Until then the prices "
          "below are **indicative**: what would trade if the auction ended now. Nothing has traded yet.",
    "HK": "Hong Kong's pre-opening session runs **09:00-09:20** (matching about 09:20), and continuous trading starts "
          "at 09:30. The prices below are **indicative** until then. Free HK quotes run about 15 minutes behind.",
}


@st.cache_resource
def job_runner() -> jobs.Runner:
    """One background worker for the whole server (one job at a time keeps memory low)."""
    return jobs.Runner(workers=1)


def render(mkt: str) -> None:
    spec = checkpoint.SPECS[mkt]
    cp_name = {c: (f"{c} close" if c == spec.close else c) for c in spec.checkpoints}

    if mkt == "CN":
        st.header("尾盘选股 · Late-session picks", anchor=False)
        P = screener.ScreenParams()
    else:
        st.header("尾盘选股 · Hong Kong (live test)", anchor=False)
        st.caption("A live run of the A-share screen on Hong Kong stocks - the same pipeline (minute data, "
                   "checkpoints, rules, ranking), useful as a pre-test before the A-share checkpoints.")
        variant = st.segmented_control(
            "Rules", ["Same rules as A-shares", "HK-adapted turnover (0.5–8%)"], default="Same rules as A-shares",
            key="hk_variant", required=True,
            help="Hong Kong turnover rates are far lower than mainland ones (Tencent trades ~0.3% a day), so a "
                 "3% minimum removes almost every stock. The adapted version lowers only that floor.")
        P = screener.ScreenParams() if variant == "Same rules as A-shares" else HK_ADAPTED
    rules = checkpoint.rule_labels(P, spec)
    _query_card(mkt, P)

    if sample.is_sample() and mkt != "CN":
        st.info("The Hong Kong test runs on live data only. Turn on **Live data** in the sidebar.",
                icon=":material/inventory_2:")
        return

    if not sample.is_sample():
        _auction_panel(mkt, spec)

    # ------------------------------------------------------------------ which session, which checkpoints
    if sample.is_sample():
        saved = sample.picks()
        day = saved["day"] if saved else checkpoint.latest_session_day()
        note = (f"**Saved example: {pd.Timestamp(day):%A %d %B %Y}** - the stocks that matched as the market stood "
                "at each checkpoint that day. Turn on **Live data** in the sidebar for the latest session.")
    else:
        day = checkpoint.latest_session_day(mkt)
        note = checkpoint.session_note(day, spec)
    plan = checkpoint.plan(day, spec)
    reached = tuple(x["checkpoint"] for x in plan if x["ready"])
    if note:
        st.info(note, icon=":material/event:")
    with st.container(horizontal=True):
        for x in plan:
            with st.container(border=True):
                badge = ":green-badge[Shown]" if x["ready"] else ":gray-badge[Not yet]"
                st.markdown(f"**{cp_name[x['checkpoint']]}** {badge}")
                st.caption(f"{pd.Timestamp(day):%a %d %b}" if x["ready"] else x["reason"])

    # ------------------------------------------------------------------ result: saved now, live in the background
    if sample.is_sample():
        res, job = sample.picks(), None
    else:
        res = checkpoint.load_result(day, P, spec) if reached else None
        recompute = st.button("Recompute", icon=":material/refresh:", disabled=not reached,
                              help="Rebuild from the market data again, ignoring saved results (runs in the "
                                   "background).")
        job = None
        if reached and (res is None or recompute):
            key = (mkt, str(day), reached, repr(sorted(P.to_dict().items(), key=str)))
            job = job_runner().submit(key, lambda progress: _compute(mkt, P, day, progress, not recompute),
                                      force=recompute)
        if job is not None and job.active:
            _job_progress(job)
        elif job is not None and job.state == "failed":
            st.warning(f"The live update for {day} couldn't finish ({job.error}). Showing the last saved session; "
                       "see **Data sources** for which feed is failing.", icon=":material/warning:")
    shown = res or checkpoint.latest_saved(spec, P, on_or_before=day)
    if shown is None:
        if reached:
            st.info("No session has been saved yet - the first one is being computed above.",
                    icon=":material/hourglass_top:")
        else:
            st.info(f"No checkpoint has passed yet and no earlier session is saved. The list appears after "
                    f"{spec.checkpoints[0]} Beijing.", icon=":material/schedule:")
    else:
        if shown["day"] != day or tuple(shown["frames"]) != reached:
            cps_txt = ", ".join(shown["frames"])
            st.caption(f":material/history: Showing the last saved result: **{pd.Timestamp(shown['day']):%a %d %b}** "
                       f"at {cps_txt}.")
        _result_block(shown, spec, P, rules, cp_name, mkt, key=f"{mkt}_current")

    _past_sessions(mkt, spec, P, rules, cp_name, exclude=shown["day"] if shown else None)


# ---------------------------------------------------------------------------------------------- pieces

def _query_card(mkt: str, P: screener.ScreenParams) -> None:  # noqa: N803
    turnover = f"{P.turnover_min:g}%–{P.turnover_max:g}%"
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown("**The query**")
            lang = st.segmented_control("Language", ["en", "zh"], default="en", key="picks_lang", required=True,
                                        format_func=lambda x: {"en": "English", "zh": "中文"}[x],
                                        label_visibility="collapsed")
        st.markdown(QUERY[mkt][lang].replace("{turnover}", turnover))
        st.caption(TIMES[mkt] + (" Edit the thresholds on the Screener page." if mkt == "CN" else ""))
    st.session_state["picks_lang_value"] = lang


def _compute(mkt: str, P: screener.ScreenParams, day: date, progress, use_cache: bool) -> None:  # noqa: N803
    """Background job body: download the market, rebuild the checkpoints, save (evaluate saves)."""
    snap = realtime.market_snapshot(mkt)
    res = checkpoint.evaluate(snap, P, progress=progress, use_cache=use_cache, spec=checkpoint.SPECS[mkt], day=day)
    del snap
    if not res["frames"]:
        raise RuntimeError("; ".join(res["notes"]) or "no stock could be rebuilt")


def _job_progress(job: jobs.Job) -> None:
    @st.fragment(run_every="3s")
    def watch() -> None:
        if job.active:
            mins = (now_bj().timestamp() - job.started) / 60
            st.progress(job.progress, text=f"Live update for **{job.key[1]}** ({', '.join(job.key[2])}): "
                                           f"{job.message} · {mins:.0f} min so far. This page updates by itself.")
        else:
            st.rerun()  # finished (or failed): redraw the whole page with the new result

    watch()


def _auction_panel(mkt: str, spec: checkpoint.MarketSpec) -> None:
    """Pre-open: indicative auction prices, live. After the open: the auction result, on request."""
    s = status(mkt)
    if s.phase == "pre-open":
        @st.fragment(run_every="20s")
        def live() -> None:
            _auction_view(mkt, spec, pre_open=True)

        with st.container(border=True):
            st.subheader("Pre-open auction · 集合竞价", anchor=False)
            st.caption(AUCTION[mkt])
            live()
    elif s.phase in ("morning", "lunch", "afternoon", "closing auction", "closed") and \
            checkpoint.latest_session_day(mkt) == now_bj().date():
        exp = st.expander("Today's opening auction · 集合竞价 result", icon=":material/gavel:", on_change="rerun")
        if exp.open:
            with exp:
                _auction_view(mkt, spec, pre_open=False)


def _auction_view(mkt: str, spec: checkpoint.MarketSpec, pre_open: bool) -> None:
    from ui import cache  # loaded only when the auction panel is shown

    snap, t = cache.snapshot(mkt)
    today = now_bj().date()
    fresh = snap[pd.to_datetime(snap["time"]).dt.date == today] if "time" in snap else snap
    if fresh.empty:
        st.caption("No auction prices yet for today - they appear once the auction starts "
                   f"({'09:15' if mkt == 'CN' else '09:00'} Beijing).")
        return
    d = auction.indicative(fresh) if pre_open else auction.opening(fresh)
    b = auction.breadth(d)
    idx = cache.quotes((spec.index,))
    idx_gap = None
    if not idx.empty:
        i = auction.indicative(idx) if pre_open else auction.opening(idx)
        idx_gap = i["gap_pct"].iloc[0] if not i.empty else None
    word = "Indicative" if pre_open else "Opening"
    with st.container(horizontal=True):
        st.metric(f"{spec.index_name} · {word.lower()} gap", pct(idx_gap, already_pct=True), border=True)
        st.metric("Gapping up / down", f"{b.get('up', 0):,} / {b.get('down', 0):,}", border=True)
        st.metric("Median gap", pct(b.get("median"), already_pct=True), border=True)
        st.metric("Gaps of 3% or more", f"↑ {b.get('up_3', 0)} · ↓ {b.get('down_3', 0)}", border=True)
    cols = ["code", "name", "prev_close", "indicative" if pre_open else "open", "gap_pct", "volume", "time"]
    cfg = {"code": st.column_config.TextColumn("Code"), "name": st.column_config.TextColumn("Name"),
           "prev_close": st.column_config.NumberColumn("Prev close", format="%.2f"),
           "indicative": st.column_config.NumberColumn("Indicative", format="%.2f"),
           "open": st.column_config.NumberColumn("Open", format="%.2f"),
           "gap_pct": st.column_config.NumberColumn("Gap", format="%+.2f%%"),
           "volume": st.column_config.NumberColumn("Matched vol.", format="compact"),
           "time": st.column_config.DatetimeColumn("Quote time", format="HH:mm:ss")}
    wl = [c for c in st.session_state.get("watchlist", []) if market(c) == mkt]
    left, right = st.columns(2)
    with left:
        st.markdown("**Your watchlist**")
        w = d[d["code"].isin(wl)]
        if w.empty:
            st.caption("No watchlist stocks in this market.")
        else:
            st.dataframe(style_signed(w[cols], ["gap_pct"]), hide_index=True, column_config=cfg)
    with right:
        up, down = auction.movers(d, 8, min_value=2e9)
        st.markdown("**Biggest gaps** (float cap over 2 bn)")
        st.dataframe(style_signed(pd.concat([up, down])[cols], ["gap_pct"]), hide_index=True, column_config=cfg)
    st.caption(f"Snapshot taken {t} Beijing; refreshes every 20 s before the open.")


def _past_sessions(mkt: str, spec: checkpoint.MarketSpec, P, rules, cp_name, exclude: date | None) -> None:  # noqa: N803
    exp = st.expander("Past sessions", icon=":material/history:", on_change="rerun")
    if not exp.open:
        return
    with exp:
        days = [d for d in archive.sessions(mkt) if d != exclude] if P == screener.ScreenParams() else []
        if not days:
            st.caption("No earlier sessions saved yet. Each finished session is added to results/ automatically.")
            return
        pick = st.selectbox("Session", days, format_func=lambda d: f"{d:%a %d %b %Y}", key=f"past_{mkt}")
        res = archive.load(mkt, pick)
        if res:
            _result_block(res, spec, P, rules, cp_name, mkt, key=f"{mkt}_past")


def _result_block(res: dict, spec, P, rules, cp_name, mkt: str, key: str) -> None:  # noqa: N803
    frames = res["frames"]
    if not frames:
        st.warning("No stock could be rebuilt for this session. " + " ".join(res.get("notes", [])),
                   icon=":material/warning:")
        return
    ranked = checkpoint.ranked(res, P, spec=spec)
    cps = [c for c in spec.checkpoints if c in frames]
    index_short = "CSI 300" if mkt == "CN" else "HSI"
    with st.container(horizontal=True):
        st.metric("Session", pd.Timestamp(res["day"]).strftime("%a %d %b %Y"), border=True)
        for c in cps:
            idx = res["index_pct"].get(c)
            st.metric(f"Met all {len(rules)} at {cp_name[c]}", f"{int(frames[c]['passes'].sum())}",
                      f"{index_short} {pct(idx, already_pct=True)}" if idx is not None else None,
                      delta_color="off", border=True, help=f"Out of {len(frames[c])} stocks rebuilt at {c}.")
    for n in res.get("notes", []):
        st.caption(f":material/info: {n}")

    latest = cps[-1]
    st.subheader(f"Ranked list · {pd.Timestamp(res['day']):%d %b}", anchor=False)
    if ranked.empty:
        st.warning(f"No stock met every rule - or came within one narrowly-missed rule - at {', '.join(cps)}. "
                   f"Closest at {latest}:", icon=":material/search_off:")
        show = frames[latest].head(15).copy()
        show.insert(0, "rank", range(1, len(show) + 1))
        show["tier"] = "-"
        show["confidence"] = show["flow_at"].map({"end of day": "Medium"}).fillna("High")
        if not res.get("flow_checked", True):
            show["confidence"] = "Low"
    else:
        show = ranked.copy()

    show["tier_label"] = show["tier"].map(TIER_TEXT).fillna("-")
    show["rules"] = show["rules_met"].astype(int).astype(str) + f"/{len(rules)}"
    first_label = "close" if "after_first_label" in show and (show["after_first_label"] == "to close").any() else "now"
    cp_cols = [f"at_{c}" for c in cps if f"at_{c}" in show]
    cols = ["rank", "tier_label", "code", "name", "score", "rules", *cp_cols, "missed", "pct_change", "volume_ratio",
            "turnover_rate", "float_mcap", "cross_days_ago", "main_net", "confidence", "after_first"]
    view = show[[c for c in cols if c in show.columns]]
    event = st.dataframe(
        style_signed(view, ["pct_change", "main_net", "after_first"]),
        hide_index=True, on_select="rerun", selection_mode="single-row", key=f"picks_table_{key}",
        column_config={
            "rank": st.column_config.NumberColumn("#", width="small"),
            "tier_label": st.column_config.TextColumn("Tier", help="A is most reliable: every rule held at every "
                                                                   "checkpoint. " + " · ".join(TIER_TEXT.values())),
            "code": st.column_config.TextColumn("Code"),
            "name": st.column_config.TextColumn("Name"),
            "score": st.column_config.ProgressColumn("Signal score", min_value=0, max_value=100, format="%.0f",
                                                     help="0-100 at the latest checkpoint: trend 25%, volume 20%, "
                                                          "main funds 20%, intraday pattern 20%, relative strength "
                                                          "10%, liquidity 5%."),
            "rules": st.column_config.TextColumn("Rules met", width="small", help=f"At {latest}"),
            **{f"at_{c}": st.column_config.TextColumn(c, width="small", help=f"✓ = every rule met at {c}")
               for c in cps},
            "missed": st.column_config.TextColumn("Missed"),
            "pct_change": st.column_config.NumberColumn(f"Change at {latest}", format="%.2f%%"),
            "volume_ratio": st.column_config.NumberColumn("Vol ratio", format="%.2f"),
            "turnover_rate": st.column_config.NumberColumn("Turnover", format="%.2f%%"),
            "float_mcap": st.column_config.NumberColumn("Float cap", format="compact"),
            "cross_days_ago": st.column_config.NumberColumn("MA cross (days ago)", format="%d"),
            "main_net": st.column_config.NumberColumn("Main net inflow", format="compact"),
            "confidence": st.column_config.TextColumn(
                "Confidence", help="High: every input measured at the checkpoint. Medium: main-fund flow is the "
                                   "session's end-of-day figure. Low: no money-flow source was reachable, so that "
                                   "rule was not checked."),
            "after_first": st.column_config.NumberColumn(f"{cps[0]} → {first_label}", format="percent",
                                                         help="What happened after the first checkpoint (hindsight). "
                                                              "Not used for ranking."),
        })
    sel = event.selection.rows if event else []
    if sel:
        code = view.iloc[sel[0]]["code"]
        with st.container(horizontal=True):
            if st.button(f"Open {code} on the Live dashboard", icon=":material/monitoring:", key=f"open_{key}"):
                st.session_state.live_code = code
                st.switch_page("app_pages/live.py")
            if st.button("Add to watchlist", icon=":material/playlist_add:", key=f"wl_{key}"):
                if code not in st.session_state.watchlist:
                    st.session_state.watchlist.append(code)
                    watchlist.save(st.session_state.watchlist)
                st.toast(f"{code} added to the watchlist")
    st.caption("Ranking: tier first (a signal that held across checkpoints is more reliable than one seen once), then "
               "signal score. Select a row to open it.")

    lang = st.session_state.get("picks_lang_value", "en")
    with st.container(horizontal=True):
        st.download_button("Ranked list (CSV)", to_csv_bytes(show.drop(columns=["tier_label"], errors="ignore")),
                           file_name=f"picks_{mkt.lower()}_{res['day']}_{stamp()}.csv", mime="text/csv",
                           on_click="ignore", icon=":material/download:", type="primary", key=f"dl_{key}")
        with st.popover("AI prompt", icon=":material/content_copy:"):
            plang = st.segmented_control("Language", ["en", "zh"], default=lang, key=f"picks_prompt_lang_{key}",
                                         required=True, format_func=lambda x: {"en": "English", "zh": "中文"}[x])
            st.code(prompts.checkpoint_review(checkpoint_rules(P, spec), str(res["day"]), res["index_pct"], show,
                                              plang, market=mkt, checkpoints=spec.checkpoints),
                    language=None, wrap_lines=True, height=340)

    exp = st.expander("Every rule at every checkpoint, and how the ranking works", icon=":material/rule:",
                      on_change="rerun", key=f"rules_exp_{key}")
    if exp.open:
        with exp:
            labels = dict(rules)
            pick = st.segmented_control("Checkpoint", list(reversed(cps)), default=latest,
                                        key=f"picks_detail_cp_{key}", required=True,
                                        format_func=lambda c: cp_name[c])
            f = frames[pick]
            near = f[f["rules_met"] >= len(rules) - 2]
            st.caption(f"{len(near)} stocks met at least {len(rules) - 2} of {len(rules)} rules at {pick} "
                       f"(of {len(f)} rebuilt). ✓ = rule met.")
            grid = near[["code", "name", "score"] + list(labels)].rename(columns=labels)
            st.dataframe(grid, hide_index=True, column_config={
                "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
                **{lbl: st.column_config.CheckboxColumn(lbl, width="small") for lbl in labels.values()}})
            st.download_button("Every stock rebuilt at each checkpoint (CSV)",
                               to_csv_bytes(pd.concat([fr.assign(checkpoint=c) for c, fr in frames.items()],
                                                      ignore_index=True)),
                               file_name=f"checkpoints_{mkt.lower()}_{res['day']}_{stamp()}.csv", mime="text/csv",
                               on_click="ignore", icon=":material/download:", key=f"dl_all_{key}")
            st.markdown(explainer(spec))


def checkpoint_rules(p: screener.ScreenParams, spec: checkpoint.MarketSpec) -> list[str]:
    """Plain-English rules for prompts, with the market's own index and currency."""
    return [lbl for _, lbl in checkpoint.rule_labels(p, spec)]


def explainer(spec: checkpoint.MarketSpec) -> str:
    first, mid, close = spec.checkpoints
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
