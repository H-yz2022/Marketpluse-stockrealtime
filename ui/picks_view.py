"""尾盘选股 view shared by the A-share front page and the Hong Kong page.

Kept deliberately light for a free hosting plan: the session is found from the
official calendar and a saved result is shown when one exists, so the whole-market
download only happens the first time a new checkpoint is computed.
"""

import dataclasses
import gc

import pandas as pd
import streamlit as st

from stockrt import checkpoint, prompts, sample, screener, watchlist
from stockrt.export import stamp, to_csv_bytes
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


@st.cache_resource
def results_memo() -> dict:
    """Shared across visitors; one latest result per market (small memory footprint)."""
    return {}


def render(mkt: str) -> None:
    spec = checkpoint.SPECS[mkt]
    cp_name = {c: (f"{c} close" if c == spec.close else c) for c in spec.checkpoints}
    _render(mkt, spec, cp_name)


def _render(mkt: str, spec: checkpoint.MarketSpec, CP_NAME: dict) -> None:  # noqa: N803
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
    RULES = checkpoint.rule_labels(P, spec)
    turnover = f"{P.turnover_min:g}%–{P.turnover_max:g}%"
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown("**The query**")
            lang = st.segmented_control("Language", ["en", "zh"], default="en", key="picks_lang", required=True,
                                        format_func=lambda x: {"en": "English", "zh": "中文"}[x],
                                        label_visibility="collapsed")
        st.markdown(QUERY[mkt][lang].replace("{turnover}", turnover))
        st.caption(TIMES[mkt] + (" Edit the thresholds on the Screener page." if mkt == "CN" else ""))

    # ---------------------------------------------------------------------------------------------- which session
    if sample.is_sample() and mkt != "CN":
        st.info("The Hong Kong test runs on live data only. Turn on **Live data** in the sidebar.",
                icon=":material/inventory_2:")
        return
    if sample.is_sample():
        saved = sample.picks()
        day = saved["day"] if saved else checkpoint.latest_session_day()
        note = (f"**Saved example: {pd.Timestamp(day):%A %d %B %Y}** - the stocks that matched as the market stood at "
                "each checkpoint that day. Turn on **Live data** in the sidebar for the latest session.")
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
                cp = x["checkpoint"]
                badge = ":green-badge[Shown]" if x["ready"] else ":gray-badge[Not yet]"
                st.markdown(f"**{CP_NAME[cp]}** {badge}")
                st.caption(f"{pd.Timestamp(day):%a %d %b}" if x["ready"] else x["reason"])

    refresh = False
    if not sample.is_sample():
        with st.container(horizontal=True, vertical_alignment="center"):
            refresh = st.button("Recompute", icon=":material/refresh:", disabled=not reached,
                                help="Rebuild from the market data again, ignoring saved results.")
            if mkt == "CN" and st.button("Run the screen on live data now", icon=":material/filter_alt:",
                                         help="The Screener page evaluates the market as it is right now."):
                st.switch_page("app_pages/screener.py")

    if not reached:
        st.info(f"No checkpoint has passed yet for this session, so there is nothing to rank yet - the list appears "
                f"here automatically after {spec.checkpoints[0]} Beijing.", icon=":material/schedule:")
        return

    # ---------------------------------------------------------------------------------------------- result
    memo = results_memo()
    key = (str(day), reached, sample.is_sample(), tuple(sorted(P.to_dict().items(), key=str)).__repr__())
    if memo.get(mkt, {}).get("key") != key or refresh:
        res = None if refresh else (sample.picks() if sample.is_sample() else checkpoint.load_result(day, P, spec))
        if res is None:
            from ui import cache  # loaded only when a new result has to be computed

            bar = st.progress(0.0, text="Downloading the market snapshot…")
            try:
                snap, _ = cache.snapshot(mkt)
                res = checkpoint.evaluate(snap, P, progress=lambda f, msg: bar.progress(f, text=msg),
                                          use_cache=not refresh, spec=spec)
            except Exception as e:  # noqa: BLE001 - upstream sources can fail; never crash the front page
                bar.empty()
                st.error(f"Couldn't rebuild the checkpoints from the live sources ({e}). Check **Data sources**, or "
                         "turn off **Live data** in the sidebar to see the saved example.", icon=":material/error:")
                return
            bar.empty()
            del snap
        memo[mkt] = {"key": key, "res": res, "ranked": checkpoint.ranked(res, P, spec=spec)}  # one per market
        gc.collect()  # return the snapshot's memory before rendering
    res, ranked = memo[mkt]["res"], memo[mkt]["ranked"]
    frames = res["frames"]
    if not frames:
        st.warning("No stock could be rebuilt for this session - minute data may be unavailable right now. See "
                   "**Data sources**. " + " ".join(res["notes"]), icon=":material/warning:")
        return

    cps = [c for c in spec.checkpoints if c in frames]
    index_short = "CSI 300" if mkt == "CN" else "HSI"
    with st.container(horizontal=True):
        st.metric("Session", pd.Timestamp(res["day"]).strftime("%a %d %b %Y"), border=True)
        for c in cps:
            idx = res["index_pct"].get(c)
            st.metric(f"Met all {len(RULES)} at {CP_NAME[c]}", f"{int(frames[c]['passes'].sum())}",
                      f"{index_short} {pct(idx, already_pct=True)}" if idx is not None else None,
                      delta_color="off", border=True, help=f"Out of {len(frames[c])} stocks rebuilt at {c}.")
    for n in res["notes"]:
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
    show["rules"] = show["rules_met"].astype(int).astype(str) + f"/{len(RULES)}"
    first_label = "close" if "after_first_label" in show and (show["after_first_label"] == "to close").any() else "now"
    cp_cols = [f"at_{c}" for c in cps if f"at_{c}" in show]
    cols = ["rank", "tier_label", "code", "name", "score", "rules", *cp_cols, "missed", "pct_change", "volume_ratio",
            "turnover_rate", "float_mcap", "cross_days_ago", "main_net", "confidence", "after_first"]
    view = show[[c for c in cols if c in show.columns]]
    event = st.dataframe(
        style_signed(view, ["pct_change", "main_net", "after_first"]),
        hide_index=True, on_select="rerun", selection_mode="single-row", key=f"picks_table_{mkt}",
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
            **{f"at_{c}": st.column_config.TextColumn(c, width="small", help=f"✓ = all 10 rules met at {c}")
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
            if st.button(f"Open {code} on the Live dashboard", icon=":material/monitoring:"):
                st.session_state.live_code = code
                st.switch_page("app_pages/live.py")
            if st.button("Add to watchlist", icon=":material/playlist_add:"):
                if code not in st.session_state.watchlist:
                    st.session_state.watchlist.append(code)
                    watchlist.save(st.session_state.watchlist)
                st.toast(f"{code} added to the watchlist")
    st.caption("Ranking: tier first (a signal that held across checkpoints is more reliable than one seen once), then "
               "signal score. Select a row to open it.")

    with st.container(horizontal=True):
        st.download_button("Ranked list (CSV)", to_csv_bytes(show.drop(columns=["tier_label"], errors="ignore")),
                           file_name=f"picks_{res['day']}_{stamp()}.csv", mime="text/csv", on_click="ignore",
                           icon=":material/download:", type="primary")
        with st.popover("AI prompt", icon=":material/content_copy:"):
            plang = st.segmented_control("Language", ["en", "zh"], default=lang, key=f"picks_prompt_lang_{mkt}",
                                         required=True, format_func=lambda x: {"en": "English", "zh": "中文"}[x])
            st.code(prompts.checkpoint_review(checkpoint_rules(P, spec), str(res["day"]), res["index_pct"], show,
                                              plang, market=mkt, checkpoints=spec.checkpoints),
                    language=None, wrap_lines=True, height=340)

    with st.expander("Every rule at every checkpoint, and how the ranking works", icon=":material/rule:"):
        labels = dict(RULES)
        pick = st.segmented_control("Checkpoint", list(reversed(cps)), default=latest, key=f"picks_detail_cp_{mkt}",
                                    required=True, format_func=lambda c: CP_NAME[c])
        f = frames[pick]
        near = f[f["rules_met"] >= len(RULES) - 2]
        st.caption(f"{len(near)} stocks met at least {len(RULES) - 2} of {len(RULES)} rules at {pick} "
                   f"(of {len(f)} rebuilt). ✓ = rule met.")
        grid = near[["code", "name", "score"] + list(labels)].rename(columns=labels)
        st.dataframe(grid, hide_index=True, column_config={
            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
            **{lbl: st.column_config.CheckboxColumn(lbl, width="small") for lbl in labels.values()}})
        st.download_button("Every stock rebuilt at each checkpoint (CSV)",
                           to_csv_bytes(pd.concat([fr.assign(checkpoint=c) for c, fr in frames.items()],
                                                  ignore_index=True)),
                           file_name=f"checkpoints_{mkt.lower()}_{res['day']}_{stamp()}.csv", mime="text/csv",
                           on_click="ignore", icon=":material/download:")
        st.markdown(explainer(spec))


def checkpoint_rules(p: screener.ScreenParams, spec: checkpoint.MarketSpec) -> list[str]:
    """Plain-English rules for prompts, with the market's own index and currency."""
    return [lbl for _, lbl in checkpoint.rule_labels(p, spec)]


def explainer(spec: checkpoint.MarketSpec) -> str:
    first, mid, close = spec.checkpoints
    auction = ("A-shares' 14:57-15:00 closing auction prints at 15:00" if spec.mkt == "CN"
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
close uses the official closing price ({auction}). Until a checkpoint arrives, its card says when it will.
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
