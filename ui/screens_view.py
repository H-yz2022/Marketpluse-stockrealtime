"""Front-page screens: 主力暗中吸筹 (the morning accumulation screen) and 长线 (after the close).

Both keep every candidate's measures, so the thresholds - edited right in the conditions table - re-filter
instantly. Results are computed once per checkpoint (or once after the close) in the background and
shared by every visitor; the daily job publishes them so they survive a server restart.
"""

from __future__ import annotations

from datetime import time

import pandas as pd
import streamlit as st

from stockrt import accumulation, checkpoint, longterm, sample
from stockrt.calendar import now_bj
from stockrt.export import to_csv_bytes
from ui.fmt import style_signed
from ui.progress import job_card

P = st.column_config


def _runner():
    from ui.picks_view import job_runner

    return job_runner()


def _until(t: time, now) -> str:
    mins = t.hour * 60 + t.minute - now.hour * 60 - now.minute
    return f"{mins // 60} 小时 {mins % 60} 分 · in {mins // 60} h {mins % 60} min" if mins >= 60 else \
        f"{max(mins, 1)} 分钟 · in {max(mins, 1)} min"


# ---------------------------------------------------------------------------------------------- 主力暗中吸筹

ACC_TABLE = [
    ("surge_pct", "环比增长率突增 · Inflow growth vs previous checkpoint ≥ (%)", 100.0),
    ("min_increase_m", "且增加至少 · and at least (¥ million)", 3.0),
    ("pct_lo", "涨幅下限 · Change above (%)", -1.0),
    ("pct_hi", "涨幅上限 · Change below (%)", 1.0),
]


def acc_key(day, reached) -> tuple:
    return ("accumulation", str(day), tuple(reached))


def longterm_key(day) -> tuple:
    return ("longterm", str(day))


def accumulation_section() -> None:
    st.subheader("主力暗中吸筹 · Quiet accumulation by main funds (9:45 – 11:15)", anchor=False)
    with st.container(border=True):
        st.markdown(f"**{accumulation.QUERY_ZH}**")
        st.caption(accumulation.QUERY_EN + " 主力 here is Sina's extra-large-order (特大单) net inflow, cumulative "
                   "from the open; 环比 compares each checkpoint with the previous one (09:31 for 9:45).")
    if sample.is_sample():
        st.caption("Runs on live data only - turn on **Live data** in the sidebar.")
        return
    now = now_bj()
    day = checkpoint.latest_session_day("CN")
    reached = accumulation.reached(day, now)
    with st.container(horizontal=True):
        for c in accumulation.CHECKPOINTS:
            with st.container(border=True):
                ok = c in reached
                st.markdown(f"**{c}** " + (":green-badge[已出 · Shown]" if ok else ":gray-badge[未到 · Not yet]"))
                hh, mm = map(int, c.split(":"))
                st.caption(f"{pd.Timestamp(day):%a %d %b}" if ok else _until(time(hh, mm), now))
    saved = accumulation.load(day)
    res = saved if saved is not None and tuple(saved["checkpoints"]) == reached else None
    if res is None and reached:
        res = accumulation.load_partial(day, reached)
    if res is None and reached:
        job = _runner().submit(acc_key(day, reached), "accumulation",
                               {"day": str(day), "reached": list(reached), "workers": checkpoint.WORKERS})
        if job.active or job.state == "failed":
            job_card(job, "主力暗中吸筹 · 正在读取分时资金流", f"reading each candidate's minute money flow for "
                     f"{', '.join(reached)}", typical=90)
        res = accumulation.load_partial(day, reached)
    if res is None:
        older = [d for d in accumulation.days() if d < day]
        res = accumulation.load(older[0]) if older else None
        if res is None:
            if not reached:
                st.caption(f"The first checkpoint is 9:45 Beijing ({_until(time(9, 46), now)}).")
            return
        st.caption(f":material/history: Showing the latest saved session, {pd.Timestamp(res['day']):%a %d %b}.")
    settings = st.data_editor(pd.DataFrame([{"条件 · Condition": lbl, "数值 · Value": v} for _, lbl, v in ACC_TABLE]),
                              hide_index=True, key="acc_settings", disabled=["条件 · Condition"],
                              column_config={"数值 · Value": P.NumberColumn(step=0.5)})
    vals = dict(zip([k for k, _, _ in ACC_TABLE], settings["数值 · Value"]))
    need_inflow = st.toggle("需为净流入 · Must be a net inflow", value=True, key="acc_inflow")
    p = accumulation.Params(surge_pct=vals["surge_pct"], min_increase_m=vals["min_increase_m"],
                            need_inflow=need_inflow, pct_lo=vals["pct_lo"], pct_hi=vals["pct_hi"])
    cps = tuple(res["checkpoints"])
    if res["frame"].empty:
        st.info("; ".join(res["notes"]) or "No candidates.", icon=":material/info:")
        return
    d = accumulation.apply(res["frame"], cps, p)
    hits = d[d["hits"] > 0]
    yday = res.get("yesterday")
    st.markdown(f"**{len(hits)} stocks** met the conditions at one or more checkpoints ({', '.join(cps)}) · "
                f"{res['candidates']} candidates whose volume on {yday} beat the 4 sessions before it")
    for c in cps:
        hits = hits.assign(**{c: hits[f"hit_{c}"].map({True: "✓", False: "·"})})
    cols = ["code", "name", "yday_vs_ref", *cps] + [f"{k}_{c}" for c in cps for k in ("growth", "pct")]
    cfg = {"code": P.TextColumn("Code"), "name": P.TextColumn("Name"),
           "yday_vs_ref": P.NumberColumn("昨日量 × 前4日最高", format="%.2f×")}
    for c in cps:
        cfg[c] = P.TextColumn(c)
        cfg[f"growth_{c}"] = P.NumberColumn(f"环比 {c}", format="%+.0f%%")
        cfg[f"pct_{c}"] = P.NumberColumn(f"涨幅 {c}", format="%+.2f%%")
    st.dataframe(style_signed(hits[cols], [f"pct_{c}" for c in cps]), hide_index=True, column_config=cfg, height=320)
    st.download_button("主力暗中吸筹 (CSV)", to_csv_bytes(d[d["hits"] > 0]), file_name=f"accumulation_{res['day']}.csv",
                       mime="text/csv", on_click="ignore", icon=":material/download:")
    for n in res.get("notes", []):
        st.caption(n)


# ---------------------------------------------------------------------------------------------- 长线

LT_TABLE = [
    ("pe_max", "市盈率 < · P/E (TTM) below", 20.0, True),
    ("roe_min", "ROE > (%) · trailing 12 months, P/B ÷ P/E", 5.0, True),
    ("price_min", "股价 > · Price above (¥)", 5.0, True),
    ("price_max", "股价 < · Price below (¥)", 10.0, True),
    ("turnover_min", "换手率 > · Turnover above (%)", 4.0, True),
    ("institutions_min", "机构持仓 · Institutions holding at least", 1.0, True),
    ("inflow10_min_m", "近10日主力资金净流入 > · 10-day main inflow above (¥ million)", 0.0, True),
    ("ma_spread_max", "均线粘合 · MA5/10/20 within (% of close)", 2.0, True),
    ("ma_within", "…on one of the last N sessions (1-3)", 3.0, True),
]
LT_AT = time(15, 5)


def longterm_section() -> None:
    st.subheader("长线 · 收盘数据，给明天建仓做参考", anchor=False)
    with st.container(border=True):
        st.markdown(f"**{longterm.QUERY_ZH}**")
        st.caption("Long-term candidates from the session's closing data, as a reference for tomorrow. Computed once "
                   "after the close (from 15:05 Beijing); edit any threshold in the table below.")
    if sample.is_sample():
        st.caption("Runs on live data only - turn on **Live data** in the sidebar.")
        return
    now = now_bj()
    day = checkpoint.latest_session_day("CN")
    closed = day < now.date() or now.time() >= LT_AT
    res = longterm.load(day) if closed else None
    if res is None and closed:
        job = _runner().submit(longterm_key(day), "longterm", {"day": str(day), "workers": checkpoint.WORKERS})
        if job.active or job.state == "failed":
            job_card(job, "长线 · 正在计算收盘数据", "institutions, 10-day flow and moving averages", typical=60)
        res = longterm.load(day)
    if res is None:
        older = [d for d in longterm.days() if d < day or not closed]
        res = longterm.load(older[0]) if older else None
        if not closed:
            st.caption(f":material/schedule: Today's list comes after the close, from 15:05 Beijing "
                       f"({_until(LT_AT, now)})." + (" Showing the latest saved session." if res else ""))
        if res is None:
            return
    table = pd.DataFrame([{"启用 · On": on, "条件 · Condition": lbl, "数值 · Value": v}
                          for _, lbl, v, on in LT_TABLE])
    edited = st.data_editor(table, hide_index=True, key="lt_settings", disabled=["条件 · Condition"],
                            column_config={"启用 · On": P.CheckboxColumn(width="small"),
                                           "数值 · Value": P.NumberColumn(step=0.5)})
    v = {k: (row["数值 · Value"], bool(row["启用 · On"]))
         for (k, _, _, _), (_, row) in zip(LT_TABLE, edited.iterrows())}
    off = float("inf")
    p = longterm.Params(pe_max=v["pe_max"][0] if v["pe_max"][1] else off,
                        roe_min=v["roe_min"][0] if v["roe_min"][1] else -off,
                        price_min=v["price_min"][0] if v["price_min"][1] else -off,
                        price_max=v["price_max"][0] if v["price_max"][1] else off,
                        turnover_min=v["turnover_min"][0] if v["turnover_min"][1] else -off,
                        institutions_min=int(v["institutions_min"][0]), use_institutions=v["institutions_min"][1],
                        inflow10_min_m=v["inflow10_min_m"][0], use_inflow=v["inflow10_min_m"][1],
                        ma_spread_max=v["ma_spread_max"][0], use_ma=v["ma_spread_max"][1],
                        ma_within=int(v["ma_within"][0]))
    d = longterm.apply(res["frame"], p)
    hits = d[d["passes"]]
    st.markdown(f"**{len(hits)} stocks** met every condition on {pd.Timestamp(res['day']):%a %d %b} "
                f"({res.get('shortlisted')} on the broad shortlist).")
    show = hits if not hits.empty else d[d["rules_met"] == d["rules_met"].max()].head(15)
    if hits.empty:
        st.caption("Nothing met every condition - showing the closest.")
    cols = ["code", "name", "price", "pct_change", "pe_ttm", "roe", "turnover_rate", "institutions", "inst_float_pct",
            "inflow10", "ma_spread_min", "float_mcap", "missed"]
    st.dataframe(style_signed(show[cols], ["pct_change", "inflow10"]), hide_index=True, height=320, column_config={
        "code": P.TextColumn("Code"), "name": P.TextColumn("Name"), "price": P.NumberColumn("股价", format="%.2f"),
        "pct_change": P.NumberColumn("涨幅", format="%+.2f%%"), "pe_ttm": P.NumberColumn("市盈率", format="%.1f"),
        "roe": P.NumberColumn("ROE", format="%.1f%%"), "turnover_rate": P.NumberColumn("换手率", format="%.2f%%"),
        "institutions": P.NumberColumn("机构数", format="%d"),
        "inst_float_pct": P.NumberColumn("机构持流通%", format="%.1f%%"),
        "inflow10": P.NumberColumn("近10日主力净流入", format="compact"),
        "ma_spread_min": P.NumberColumn("均线粘合 (spread)", format="%.2f%%"),
        "float_mcap": P.NumberColumn("流通市值", format="compact"), "missed": P.TextColumn("Missed")})
    st.download_button("长线 (CSV)", to_csv_bytes(show[cols]), file_name=f"longterm_{res['day']}.csv", mime="text/csv",
                       on_click="ignore", icon=":material/download:")
    for n in res.get("notes", []):
        st.caption(n)

