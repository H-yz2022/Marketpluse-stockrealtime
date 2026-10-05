"""Front page: the late-session momentum query, answered at 14:00 and 14:30 of the latest session."""

import pandas as pd
import streamlit as st

from stockrt import checkpoint, prompts, sample, screener, watchlist
from stockrt.export import stamp, to_csv_bytes
from ui import cache
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
    "A": "A · all rules at 14:00 and 14:30",
    "B": "B · all rules at 14:30",
    "C": "C · all rules at 14:00 only",
    "D": "D · one rule narrowly missed",
}


@st.cache_resource
def results_memo() -> dict:
    """Shared across visitors: a finished session is computed once."""
    return {}


st.header("尾盘选股 · Late-session picks", anchor=False)
with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("**The query**")
        lang = st.segmented_control("Language", ["en", "zh"], default="en", key="picks_lang", required=True,
                                    format_func=lambda x: {"en": "English", "zh": "中文"}[x],
                                    label_visibility="collapsed")
    st.markdown(QUERY[lang])
    st.caption("Ranked as the market stood at **14:00** and **14:30** Beijing time on the latest session - "
               "before the 15:00 close. Edit the thresholds on the Screener page.")

snap, snap_time = cache.snapshot("CN")
day = checkpoint.session_day(snap)
# A checkpoint's picture is fixed once it has passed, so recompute only when a new one is reached.
reached = tuple(c for c in checkpoint.CHECKPOINTS if checkpoint.checkpoint_reached(day, c))
key = (str(day), reached, st.session_state.get("live_data", True))
memo = results_memo()

refresh = False
if not sample.is_sample():  # recomputing or screening live makes no sense on the saved sample
    with st.container(horizontal=True, vertical_alignment="center"):
        refresh = st.button("Recompute", icon=":material/refresh:", help="Rebuild from the latest data.")
        if st.button("Run the screen on live data now", icon=":material/filter_alt:",
                     help="The Screener page evaluates the market as it is right now."):
            st.switch_page("app_pages/screener.py")

if key not in memo or refresh:
    bar = st.progress(0.0, text="Finding candidates…")
    try:
        res = checkpoint.evaluate(snap, P, progress=lambda f, msg: bar.progress(f, text=msg))
    except Exception as e:  # noqa: BLE001 - upstream sources can fail; never crash the front page
        bar.empty()
        st.error(f"Couldn't rebuild the checkpoints from the live sources ({e}). Check **Data sources**, or turn "
                 "off **Live data** in the sidebar to use the built-in sample.", icon=":material/error:")
        st.stop()
    bar.empty()
    memo.clear()  # keep only the latest result: small memory footprint
    memo[key] = {"res": res, "ranked": checkpoint.ranked(res, P)}
res, ranked = memo[key]["res"], memo[key]["ranked"]
frames = res["frames"]

if not frames:
    if res["pending"] and len(res["pending"]) == len(checkpoint.CHECKPOINTS):
        st.info(f"Today's first checkpoint is **{res['pending'][0]}** Beijing time. The list fills in "
                "automatically after it; until then use **Run the screen on live data now**.",
                icon=":material/schedule:")
    else:
        st.warning("No stock could be rebuilt for this session - minute data may be unavailable from your "
                   "network right now. See **Data sources**. " + " ".join(res["notes"]), icon=":material/warning:")
    st.stop()

cps = [c for c in checkpoint.CHECKPOINTS if c in frames]
if sample.is_sample():
    st.info(f"**Saved example: {pd.Timestamp(day):%A %d %B %Y}.** The stocks that matched the query as the market "
            "stood at 14:00 and 14:30 that day, ranked. Turn on **Live data** in the sidebar to run it on the "
            "latest session.", icon=":material/inventory_2:")
with st.container(horizontal=True):
    st.metric("Session", pd.Timestamp(day).strftime("%a %d %b %Y"), border=True)
    for c in cps:
        st.metric(f"CSI 300 at {c}", pct(res["index_pct"].get(c), already_pct=True), border=True)
    for c in cps:
        st.metric(f"All 10 rules at {c}", f"{int(frames[c]['passes'].sum())}",
                  f"of {len(frames[c])} rebuilt", delta_color="off", border=True)
for n in res["notes"]:
    st.caption(f":material/info: {n}")
if res["pending"]:
    st.caption(f"Still to come today: {', '.join(res['pending'])}.")

latest = cps[-1]
st.subheader(f"Ranked list · {pd.Timestamp(day):%d %b}", anchor=False)
if ranked.empty:
    st.warning(f"No stock met every rule - or came within one narrowly-missed rule - at {' or '.join(cps)}. "
               f"Closest at {latest}:", icon=":material/search_off:")
    show = frames[latest].head(15).copy()
    show.insert(0, "rank", range(1, len(show) + 1))
    show["tier"] = "-"
    show["confidence"] = show["flow_at"].map({"end of day": "Medium"}).fillna("High")
else:
    show = ranked.copy()

show["tier_label"] = show["tier"].map(TIER_TEXT).fillna("-")
show["rules"] = show["rules_met"].astype(int).astype(str) + f"/{len(RULES)}"
after_label = show["after_label"].dropna().iloc[0] if "after_label" in show and show["after_label"].notna().any() \
    else "to close"
cols = ["rank", "tier_label", "code", "name", "score", "rules", "missed", "pct_change", "volume_ratio",
        "turnover_rate", "float_mcap", "cross_days_ago", "main_net", "confidence", "after"]
view = show[[c for c in cols if c in show.columns]]
event = st.dataframe(
    style_signed(view, ["pct_change", "main_net", "after"]),
    hide_index=True, on_select="rerun", selection_mode="single-row", key="picks_table",
    column_config={
        "rank": st.column_config.NumberColumn("#", width="small"),
        "tier_label": st.column_config.TextColumn("Tier", help="A is most reliable: the signal held for 30 minutes. "
                                                               + " · ".join(TIER_TEXT.values())),
        "code": st.column_config.TextColumn("Code"),
        "name": st.column_config.TextColumn("Name"),
        "score": st.column_config.ProgressColumn("Signal score", min_value=0, max_value=100, format="%.0f",
                                                 help="0-100 at the latest checkpoint: trend 25%, volume 20%, "
                                                      "main funds 20%, intraday pattern 20%, relative strength "
                                                      "10%, liquidity 5%."),
        "rules": st.column_config.TextColumn("Rules met", width="small"),
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
        "after": st.column_config.NumberColumn(f"{latest} → {after_label.split()[-1]}", format="percent",
                                               help="What happened after the checkpoint (hindsight). "
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
st.caption("Ranking: tier first (a signal that held from 14:00 to 14:30 is more reliable than one seen once), "
           "then signal score. Select a row to open it.")

with st.container(horizontal=True):
    st.download_button("Ranked list (CSV)", to_csv_bytes(show.drop(columns=["tier_label"], errors="ignore")),
                       file_name=f"picks_{day}_{stamp()}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:", type="primary")
    all_rows = pd.concat([f.assign(checkpoint=c) for c, f in frames.items()], ignore_index=True)
    st.download_button("Every stock rebuilt at 14:00 & 14:30 (CSV)", to_csv_bytes(all_rows),
                       file_name=f"checkpoints_{day}_{stamp()}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    with st.popover("AI prompt", icon=":material/content_copy:"):
        plang = st.segmented_control("Language", ["en", "zh"], default=lang, key="picks_prompt_lang", required=True,
                                     format_func=lambda x: {"en": "English", "zh": "中文"}[x])
        st.code(prompts.checkpoint_review(screener.describe(P), str(day), res["index_pct"], show, plang),
                language=None, wrap_lines=True, height=340)

st.divider()
tabs = st.tabs([f"Rules at {c}" for c in reversed(cps)] + ["How the ranking works"])
labels = dict(RULES)
for tab, c in zip(tabs, reversed(cps)):
    with tab:
        f = frames[c]
        near = f[f["rules_met"] >= len(RULES) - 2]
        st.caption(f"{len(near)} stocks met at least {len(RULES) - 2} of {len(RULES)} rules at {c} "
                   f"(of {len(f)} rebuilt). ✓ = rule met.")
        grid = near[["code", "name", "score"] + list(labels)].rename(columns=labels)
        st.dataframe(grid, hide_index=True, column_config={
            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
            **{lbl: st.column_config.CheckboxColumn(lbl, width="small") for lbl in labels.values()}})
with tabs[-1]:
    st.markdown("""
**Why replay 14:00 and 14:30?** The screen is meant to be run shortly before the 15:00 close. After the close,
quote feeds only show end-of-day values, so each stock is rebuilt from its minute-by-minute data as it stood at
each checkpoint. Checked against the real end-of-day quotes, the rebuild matches change % to 0.005 points,
volume ratio and turnover to within about 1%.

**Ranking, in order**

1. **Tier**, for reliability. A: met every rule at 14:00 *and* still at 14:30. B: met every rule at 14:30.
   C: met every rule at 14:00 but slipped by 14:30. D: one rule short, and only narrowly (for example change
   within 1 point of the 3-5% band), so stocks at the daily limit don't count as near misses.
2. **Signal score** (0-100), from six components shown on the details tabs:

| Component | Weight | High when |
|---|---|---|
| Trend | 25% | MA5 crossed above MA10 today (fresher cross = higher), both MAs rising |
| Volume | 20% | volume ratio in the 2-4 sweet spot (thin below 1, overheated above 6) |
| Main funds | 20% | large-order net inflow is a big share of value traded |
| Intraday pattern | 20% | above VWAP, near the day's high, not fading from it |
| Relative strength | 10% | well ahead of the CSI 300 |
| Liquidity | 5% | more value traded (harder to push around) |

**Confidence.** High when every input was measured at the checkpoint. Medium when main-fund flow is the
session's end-of-day figure, because minute-level flow wasn't reachable from your network. Captures from
*Intraday at 14:00 → Capture now*, auto-capture, or `scripts/capture_snapshot.py` record the real values at
14:00 / 14:30, and this page uses them automatically for that day.

**After column.** The move from the checkpoint to the close is shown for judging the screen in hindsight. It is
never used for ranking.
""")
