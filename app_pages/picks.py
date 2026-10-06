"""Front page - 尾盘选股: the late-session query at 14:00, 14:30 and the 15:00 close.

Kept deliberately light for a free hosting plan: the session is found from the
official calendar and a saved result is shown when one exists, so the whole-market
download (~5,500 quotes) only happens the first time a new checkpoint is computed.
"""

import gc

import pandas as pd
import streamlit as st

from stockrt import checkpoint, prompts, sample, screener, watchlist
from stockrt.export import stamp, to_csv_bytes
from ui.fmt import pct, style_signed

P = screener.ScreenParams()  # the query exactly as written below
RULES = checkpoint.rule_labels(P)
QUERY = {
    "en": "Non-ST stocks up **3%–5%**, volume ratio **> 1**, turnover **3%–8%**, the **5-day MA crossing above "
          "the 10-day MA**, moving averages **rising**, **volume and price both increasing**, **main funds "
          "flowing in**, **outperforming the market**, and float market value **CNY 5–20 billion**.",
    "zh": "非ST，涨幅 **3%–5%**，量比 **大于1**，换手率 **3%–8%**，**5日均线上穿10日均线**，均线**向上**，"
          "**量价齐升**，**主力资金流入**，**强于大盘**，流通市值 **50亿–200亿**。",
}
TIER_TEXT = {
    "A": "A · all rules at every checkpoint",
    "B": "B · all rules at the latest checkpoint",
    "C": "C · earlier, not at the latest",
    "D": "D · one rule narrowly missed",
}
CP_NAME = {"14:00": "14:00", "14:30": "14:30", "15:00": "15:00 close"}


@st.cache_resource
def results_memo() -> dict:
    """Shared across visitors and kept to the latest result only (small memory footprint)."""
    return {}


st.header("尾盘选股 · Late-session picks", anchor=False)
with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("**The query**")
        lang = st.segmented_control("Language", ["en", "zh"], default="en", key="picks_lang", required=True,
                                    format_func=lambda x: {"en": "English", "zh": "中文"}[x],
                                    label_visibility="collapsed")
    st.markdown(QUERY[lang])
    st.caption("Checked at **14:00**, **14:30** and the **15:00 close** (Beijing time; 02:00 / 02:30 / 03:00 in "
               "New York during daylight saving). Edit the thresholds on the Screener page.")

# ---------------------------------------------------------------------------------------------- which session
if sample.is_sample():
    saved = sample.picks()
    day = saved["day"] if saved else checkpoint.latest_session_day()
    note = (f"**Saved example: {pd.Timestamp(day):%A %d %B %Y}** - the stocks that matched as the market stood at "
            "each checkpoint that day. Turn on **Live data** in the sidebar for the latest session.")
else:
    day = checkpoint.latest_session_day()
    note = checkpoint.session_note(day)
plan = checkpoint.plan(day)
reached = tuple(x["checkpoint"] for x in plan if x["ready"])

if note:
    st.info(note, icon=":material/event:")
with st.container(horizontal=True):
    for x in plan:
        with st.container(border=True):
            cp = x["checkpoint"]
            st.markdown(f"**{CP_NAME[cp]}** :green-badge[Shown]" if x["ready"] else f"**{CP_NAME[cp]}** :gray-badge[Not yet]")
            st.caption(f"{pd.Timestamp(day):%a %d %b}" if x["ready"] else x["reason"])

refresh = False
if not sample.is_sample():
    with st.container(horizontal=True, vertical_alignment="center"):
        refresh = st.button("Recompute", icon=":material/refresh:", disabled=not reached,
                            help="Rebuild from the market data again, ignoring saved results.")
        if st.button("Run the screen on live data now", icon=":material/filter_alt:",
                     help="The Screener page evaluates the market as it is right now."):
            st.switch_page("app_pages/screener.py")

if not reached:
    st.info("No checkpoint has passed yet for this session, so there is nothing to rank yet - the list appears "
            "here automatically after 14:00 Beijing. Until then use **Run the screen on live data now**.",
            icon=":material/schedule:")
    st.stop()

# ---------------------------------------------------------------------------------------------- result
memo = results_memo()
key = (str(day), reached, sample.is_sample())
if key not in memo or refresh:
    res = None if refresh else (sample.picks() if sample.is_sample() else checkpoint.load_result(day, P))
    if res is None:
        from ui import cache  # loaded only when a new result has to be computed

        bar = st.progress(0.0, text="Downloading the market snapshot…")
        try:
            snap, _ = cache.snapshot("CN")
            res = checkpoint.evaluate(snap, P, progress=lambda f, msg: bar.progress(f, text=msg),
                                      use_cache=not refresh)
        except Exception as e:  # noqa: BLE001 - upstream sources can fail; never crash the front page
            bar.empty()
            st.error(f"Couldn't rebuild the checkpoints from the live sources ({e}). Check **Data sources**, or "
                     "turn off **Live data** in the sidebar to see the saved example.", icon=":material/error:")
            st.stop()
        bar.empty()
        del snap
    memo.clear()
    memo[key] = {"res": res, "ranked": checkpoint.ranked(res, P)}
    gc.collect()  # return the snapshot's memory before rendering
res, ranked = memo[key]["res"], memo[key]["ranked"]
frames = res["frames"]
if not frames:
    st.warning("No stock could be rebuilt for this session - minute data may be unavailable right now. See "
               "**Data sources**. " + " ".join(res["notes"]), icon=":material/warning:")
    st.stop()

cps = [c for c in checkpoint.CHECKPOINTS if c in frames]
with st.container(horizontal=True):
    st.metric("Session", pd.Timestamp(res["day"]).strftime("%a %d %b %Y"), border=True)
    for c in cps:
        idx = res["index_pct"].get(c)
        st.metric(f"Met all 10 at {CP_NAME[c]}", f"{int(frames[c]['passes'].sum())}",
                  f"CSI 300 {pct(idx, already_pct=True)}" if idx is not None else None,
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
    hide_index=True, on_select="rerun", selection_mode="single-row", key="picks_table",
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
                               "session's end-of-day figure."),
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
        plang = st.segmented_control("Language", ["en", "zh"], default=lang, key="picks_prompt_lang", required=True,
                                     format_func=lambda x: {"en": "English", "zh": "中文"}[x])
        st.code(prompts.checkpoint_review(screener.describe(P), str(res["day"]), res["index_pct"], show, plang),
                language=None, wrap_lines=True, height=340)

with st.expander("Every rule at every checkpoint, and how the ranking works", icon=":material/rule:"):
    labels = dict(RULES)
    pick = st.segmented_control("Checkpoint", list(reversed(cps)), default=latest, key="picks_detail_cp",
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
                       file_name=f"checkpoints_{res['day']}_{stamp()}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    st.markdown("""
**Why 14:00, 14:30 and 15:00?** The screen is meant for the last hour before the 15:00 close. After the close,
quote feeds only show end-of-day values, so each stock is rebuilt from its minute-by-minute data as it stood at
each checkpoint. Checked against the real end-of-day quotes, the rebuild matches change % to 0.005 points, and
volume ratio and turnover to within about 1%. A checkpoint appears one minute after its time, so its minute
(and, at 15:00, the closing auction) has printed. Until then its card says when it will come.

**Ranking, in order**

1. **Tier**, for reliability.
   - A: met every rule at every checkpoint so far.
   - B: met every rule at the latest checkpoint.
   - C: met every rule earlier, but not at the latest.
   - D: one rule short at the latest checkpoint, and only narrowly (for example, change within 1 point of the
     3-5% band), so stocks at the daily limit don't count.
2. **Signal score** (0-100):

| Component | Weight | High when |
|---|---|---|
| Trend | 25% | MA5 crossed above MA10 today (fresher cross = higher), both MAs rising |
| Volume | 20% | volume ratio in the 2-4 sweet spot (thin below 1, overheated above 6) |
| Main funds | 20% | large-order net inflow is a big share of value traded |
| Intraday pattern | 20% | above VWAP, near the day's high, not fading from it |
| Relative strength | 10% | well ahead of the CSI 300 |
| Liquidity | 5% | more value traded (harder to push around) |

**Confidence.** High when every input was measured at the checkpoint. That includes 15:00, where the
end-of-day flow *is* the checkpoint value. Medium when 14:00 / 14:30 money flow had to use the end-of-day
figure. Captures from the scheduled script or auto-capture record the real values at each checkpoint.
""")
