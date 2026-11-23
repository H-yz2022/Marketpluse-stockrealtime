"""Front-page screens: 主力暗中吸筹 (the morning accumulation screen), 长线 (after the close) and 低位吸筹.

Each keeps every candidate's measures, so the thresholds - edited right in the conditions table - re-filter
instantly. Results are computed once per checkpoint (or once after the close) in the background and
shared by every visitor; the daily job publishes them so they survive a server restart. One language at a time
(ui.i18n): the conditions tables keep fixed column names, so switching language keeps the values entered.
"""

from __future__ import annotations

from datetime import time

import pandas as pd
import streamlit as st

from stockrt import accumulation, checkpoint, dipbuy, longterm, sample
from stockrt.calendar import now_bj
from stockrt.export import to_csv_bytes
from ui.fmt import style_signed
from ui.i18n import day as fday
from ui.i18n import note, t, zh
from ui.progress import job_card

P = st.column_config
LIVE_ONLY = ("只使用实时数据——请在侧边栏打开 **实时数据**。", "Runs on live data only - turn on **Live data** in the sidebar.")


def _runner():
    from ui.picks_view import job_runner

    return job_runner()


def _until(at: time, now) -> str:
    mins = max(at.hour * 60 + at.minute - now.hour * 60 - now.minute, 1)
    return t(f"还有{mins // 60}小时{mins % 60}分钟", f"in {mins // 60} h {mins % 60} min") if mins >= 60 else \
        t(f"还有{mins}分钟", f"in {mins} min")


def _conditions(rows: list[tuple], key: str, with_on: bool, step: float) -> dict:
    """An editable conditions table (fixed column names; headings in the visitor's language)."""
    table = pd.DataFrame([{"on": r[3] if len(r) > 3 else True, "cond": t(*r[1]), "value": r[2]} for r in rows])
    if not with_on:
        table = table.drop(columns="on")
    edited = st.data_editor(table, hide_index=True, key=key, disabled=["cond"], column_config={
        "on": P.CheckboxColumn(t("启用", "On"), width="small"), "cond": P.TextColumn(t("条件", "Condition")),
        "value": P.NumberColumn(t("数值", "Value"), step=step)})
    return {r[0]: (row["value"], bool(row.get("on", True))) for r, (_, row) in zip(rows, edited.iterrows())}


MISSED_EN = {"市盈率": "P/E", "ROE": "ROE", "股价": "price", "换手率": "turnover", "机构持仓": "institutions",
             "近10日资金流入": "10-day inflow", "均线粘合": "MA convergence", "近5日跌幅": "5-session fall",
             "主力净流入": "main inflow", "扣非净利润": "profit",
             "今日涨幅": "change today"}


def _missed(text: str) -> str:
    if zh() or not isinstance(text, str) or not text:
        return text
    return ", ".join(MISSED_EN.get(x.strip(), x.strip()) for x in text.split(","))


# ---------------------------------------------------------------------------------------------- 主力暗中吸筹

ACC_TABLE = [
    ("surge_pct", ("环比增长率突增 ≥（%）", "Inflow growth vs previous checkpoint ≥ (%)"), 100.0),
    ("min_increase_m", ("且增加至少（百万元）", "and at least (¥ million)"), 3.0),
    ("pct_lo", ("涨幅下限（%）", "Change above (%)"), -1.0),
    ("pct_hi", ("涨幅上限（%）", "Change below (%)"), 1.0),
]


def acc_key(day, reached) -> tuple:
    return ("accumulation", str(day), tuple(reached))


def longterm_key(day) -> tuple:
    return ("longterm", str(day))


def dipbuy_key(day, bucket: str) -> tuple:
    return ("dipbuy", str(day), bucket)


def accumulation_section() -> None:
    st.subheader(t("主力暗中吸筹（9:45 – 11:15）", "Quiet accumulation by main funds (9:45 – 11:15)"), anchor=False)
    with st.container(border=True):
        st.markdown(f"**{t(accumulation.QUERY_ZH, accumulation.QUERY_EN)}**")
        st.caption(t("这里的主力指新浪的特大单净流入（自开盘起累计）；环比是与上一个检查点比较（9:45 与 09:31 比较）。",
                     "主力 here is Sina's extra-large-order (特大单) net inflow, cumulative from the open; 环比 compares "
                     "each checkpoint with the previous one (09:31 for 9:45)."))
    if sample.is_sample():
        st.caption(t(*LIVE_ONLY))
        return
    now = now_bj()
    day = checkpoint.latest_session_day("CN")
    reached = accumulation.reached(day, now)
    with st.container(horizontal=True):
        for c in accumulation.CHECKPOINTS:
            with st.container(border=True):
                ok = c in reached
                st.markdown(f"**{c}** " + (f":green-badge[{t('已出', 'Shown')}]" if ok else
                                           f":gray-badge[{t('未到', 'Not yet')}]"))
                hh, mm = map(int, c.split(":"))
                st.caption(fday(day) if ok else _until(time(hh, mm), now))
    saved = accumulation.load(day)
    res = saved if saved is not None and tuple(saved["checkpoints"]) == reached else None
    if res is None and reached:
        res = accumulation.load_partial(day, reached)
    if res is None and reached:
        job = _runner().submit(acc_key(day, reached), "accumulation",
                               {"day": str(day), "reached": list(reached), "workers": checkpoint.WORKERS},
                               front=True)
        if job.active or job.state == "failed":
            job_card(job, t("主力暗中吸筹 · 正在读取分时资金流", "Quiet accumulation · reading minute money flow"),
                     t(f"读取每只候选股 {'、'.join(reached)} 的分时资金流",
                       f"each candidate's minute money flow for {', '.join(reached)}"), typical=90)
        res = accumulation.load_partial(day, reached)
    if res is None:
        older = [d for d in accumulation.days() if d < day]
        res = accumulation.load(older[0]) if older else None
        if res is None:
            if not reached:
                st.caption(t(f"第一个检查点是北京时间 9:45（{_until(time(9, 46), now)}）。",
                             f"The first checkpoint is 9:45 Beijing ({_until(time(9, 46), now)})."))
            return
        st.caption(t(f":material/history: 显示最近保存的交易日：{fday(res['day'])}。",
                     f":material/history: Showing the latest saved session, {fday(res['day'])}."))
    vals = {k: v for k, (v, _) in _conditions(ACC_TABLE, "acc_settings", with_on=False, step=0.5).items()}
    need_inflow = st.toggle(t("需为净流入", "Must be a net inflow"), value=True, key="acc_inflow")
    p = accumulation.Params(surge_pct=vals["surge_pct"], min_increase_m=vals["min_increase_m"],
                            need_inflow=need_inflow, pct_lo=vals["pct_lo"], pct_hi=vals["pct_hi"])
    cps = tuple(res["checkpoints"])
    if res["frame"].empty:
        st.info("; ".join(note(n) for n in res["notes"]) or t("没有候选股。", "No candidates."), icon=":material/info:")
        return
    d = accumulation.apply(res["frame"], cps, p)
    hits = d[d["hits"] > 0]
    yday = res.get("yesterday")
    st.markdown(t(f"**{len(hits)} 只股票**在一个或多个检查点（{'、'.join(cps)}）满足条件 · "
                  f"候选股 {res['candidates']} 只（{yday} 成交量大于前4日）",
                  f"**{len(hits)} stocks** met the conditions at one or more checkpoints ({', '.join(cps)}) · "
                  f"{res['candidates']} candidates whose volume on {yday} beat the 4 sessions before it"))
    for c in cps:
        hits = hits.assign(**{c: hits[f"hit_{c}"].map({True: "✓", False: "·"})})
    cols = ["code", "name", "yday_vs_ref", *cps] + [f"{k}_{c}" for c in cps for k in ("growth", "pct")]
    cfg = {"code": P.TextColumn(t("代码", "Code")), "name": P.TextColumn(t("名称", "Name")),
           "yday_vs_ref": P.NumberColumn(t("昨日量 × 前4日最高", "Yesterday's volume × 4-day high"), format="%.2f×")}
    for c in cps:
        cfg[c] = P.TextColumn(c)
        cfg[f"growth_{c}"] = P.NumberColumn(t(f"环比 {c}", f"Growth {c}"), format="%+.0f%%")
        cfg[f"pct_{c}"] = P.NumberColumn(t(f"涨幅 {c}", f"Change {c}"), format="%+.2f%%")
    st.dataframe(style_signed(hits[cols], [f"pct_{c}" for c in cps]), hide_index=True, column_config=cfg, height=320)
    st.download_button(t("主力暗中吸筹（CSV）", "Quiet accumulation (CSV)"), to_csv_bytes(d[d["hits"] > 0]),
                       file_name=f"accumulation_{res['day']}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    for n in res.get("notes", []):
        st.caption(note(n))


# ---------------------------------------------------------------------------------------------- 长线

LT_TABLE = [
    ("pe_max", ("市盈率 <", "P/E (TTM) below"), 20.0, True),
    ("roe_min", ("ROE >（%，近12个月，市净率 ÷ 市盈率）", "ROE > (%) · trailing 12 months, P/B ÷ P/E"), 5.0, True),
    ("price_min", ("股价 >（元）", "Price above (¥)"), 5.0, True),
    ("price_max", ("股价 <（元）", "Price below (¥)"), 10.0, True),
    ("turnover_min", ("换手率 >（%）", "Turnover above (%)"), 4.0, True),
    ("institutions_min", ("机构持仓家数至少", "Institutions holding at least"), 1.0, True),
    ("inflow10_min_m", ("近10日主力资金净流入 >（百万元）", "10-day main inflow above (¥ million)"), 0.0, True),
    ("ma_spread_max", ("均线粘合：MA5/10/20 相差不超过（收盘价的 %）", "MA5/10/20 within (% of close)"), 2.0, True),
    ("ma_within", ("……在最近 N 个交易日之一（1–3）", "…on one of the last N sessions (1-3)"), 3.0, True),
]
LT_AT = time(15, 5)


def longterm_section() -> None:
    st.subheader(t("长线 · 收盘数据，给明天建仓做参考", "Long-term · closing data, a reference for tomorrow"), anchor=False)
    with st.container(border=True):
        st.markdown(f"**{t(longterm.QUERY_ZH, LT_QUERY_EN)}**")
        st.caption(t("收盘后（北京时间 15:05 起）计算一次；可在下表修改任何阈值。",
                     "Long-term candidates from the session's closing data, as a reference for tomorrow. Computed "
                     "once after the close (from 15:05 Beijing); edit any threshold in the table below."))
    if sample.is_sample():
        st.caption(t(*LIVE_ONLY))
        return
    now = now_bj()
    day = checkpoint.latest_session_day("CN")
    closed = day < now.date() or now.time() >= LT_AT
    res = longterm.load(day) if closed else None
    if res is None and closed:
        job = _runner().submit(longterm_key(day), "longterm", {"day": str(day), "workers": checkpoint.WORKERS},
                               front=True)
        if job.active or job.state == "failed":
            job_card(job, t("长线 · 正在计算收盘数据", "Long-term · computing on the closing data"),
                     t("机构持仓、近10日资金与均线", "institutions, 10-day flow and moving averages"), typical=60)
        res = longterm.load(day)
    if res is None:
        older = [d for d in longterm.days() if d < day or not closed]
        res = longterm.load(older[0]) if older else None
        if not closed:
            st.caption(t(f":material/schedule: 今日名单在收盘后（北京时间 15:05 起，{_until(LT_AT, now)}）出现。"
                         + ("先显示最近保存的交易日。" if res else ""),
                         f":material/schedule: Today's list comes after the close, from 15:05 Beijing "
                         f"({_until(LT_AT, now)})." + (" Showing the latest saved session." if res else "")))
        if res is None:
            return
    v = _conditions(LT_TABLE, "lt_settings", with_on=True, step=0.5)
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
    d["missed"] = d["missed"].map(_missed)
    hits = d[d["passes"]]
    st.markdown(t(f"{fday(res['day'])}：**{len(hits)} 只股票**满足全部条件（宽松初筛 {res.get('shortlisted')} 只）。",
                  f"**{len(hits)} stocks** met every condition on {fday(res['day'])} ({res.get('shortlisted')} on the "
                  "broad shortlist)."))
    show = hits if not hits.empty else d[d["rules_met"] == d["rules_met"].max()].head(15)
    if hits.empty:
        st.caption(t("没有股票满足全部条件——显示最接近的。", "Nothing met every condition - showing the closest."))
    cols = ["code", "name", "price", "pct_change", "pe_ttm", "roe", "turnover_rate", "institutions", "inst_float_pct",
            "inflow10", "ma_spread_min", "float_mcap", "missed"]
    st.dataframe(style_signed(show[cols], ["pct_change", "inflow10"]), hide_index=True, height=320, column_config={
        "code": P.TextColumn(t("代码", "Code")), "name": P.TextColumn(t("名称", "Name")),
        "price": P.NumberColumn(t("股价", "Price"), format="%.2f"),
        "pct_change": P.NumberColumn(t("涨幅", "Change"), format="%+.2f%%"),
        "pe_ttm": P.NumberColumn(t("市盈率", "P/E"), format="%.1f"), "roe": P.NumberColumn("ROE", format="%.1f%%"),
        "turnover_rate": P.NumberColumn(t("换手率", "Turnover"), format="%.2f%%"),
        "institutions": P.NumberColumn(t("机构数", "Institutions"), format="%d"),
        "inst_float_pct": P.NumberColumn(t("机构持流通%", "Institutions % of float"), format="%.1f%%"),
        "inflow10": P.NumberColumn(t("近10日主力净流入", "10-day main inflow"), format="compact"),
        "ma_spread_min": P.NumberColumn(t("均线粘合（差距）", "MA spread"), format="%.2f%%"),
        "float_mcap": P.NumberColumn(t("流通市值", "Float cap"), format="compact"),
        "missed": P.TextColumn(t("未满足", "Missed"))})
    st.download_button(t("长线（CSV）", "Long-term (CSV)"), to_csv_bytes(show[cols]),
                       file_name=f"longterm_{res['day']}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    for n in res.get("notes", []):
        st.caption(note(n))


LT_QUERY_EN = ("Long-term, from the closing data as a reference for tomorrow: P/E below 20, ROE above 5%, price "
               "between ¥5 and ¥10, turnover above 4%, held by institutional investors, main-fund inflow over the "
               "last 10 days, and the 5-, 10- and 20-day moving averages converged on one of the last 3 sessions.")


# ---------------------------------------------------------------------------------------------- 低位吸筹

DIP_TABLE = [
    ("drop5_min", ("近5个交易日跌幅超过（%）", "Fall over the last 5 sessions above (%)"), 10.0),
    ("inflow_min_m", ("今日主力资金净流入 >（百万元）", "Today's main-fund net inflow above (¥ million)"), 30.0),
    ("ma_n", ("现价站上N日均线（20 / 60 / 120）", "Price above the N-day moving average (20 / 60 / 120)"), 60.0),
    ("profit_min_m", ("近12个月扣非净利润 >（百万元）", "Net profit excl. one-offs, last 12 months, above (¥ million)"), 0.0),
    ("change_max", ("今日涨幅 <（%）", "Change today below (%)"), 5.0),
]


def dipbuy_section() -> None:
    st.subheader(t("低位吸筹（抄底不接飞刀）", "Buy the dip, avoid the falling knife"), anchor=False)
    with st.container(border=True):
        st.markdown(f"**{t(dipbuy.QUERY_ZH, dipbuy.QUERY_EN)}**")
        st.caption(t("交易时间内实时计算（打开时更新，最多每 15 分钟一次），15:00 收盘后计算一次并固定。",
                     "Live during the session (recomputed when opened, at most every 15 minutes), computed once and "
                     "fixed after the 15:00 close."))
    if sample.is_sample():
        st.caption(t(*LIVE_ONLY))
        return
    now = now_bj()
    day = checkpoint.latest_session_day("CN")
    res = dipbuy.load(day)
    if dipbuy.stale(res, now):
        job = _runner().submit(dipbuy_key(day, dipbuy.bucket(now) if day == now.date() else "close"), "dipbuy",
                               {"day": str(day), "workers": checkpoint.WORKERS}, front=True)
        if job.active or job.state == "failed":
            job_card(job, t("低位吸筹 · 正在筛选", "Buy the dip · screening"),
                     t("近5日跌幅、均线、今日主力资金与利润", "5-session fall, moving averages, today's main-fund flow and "
                       "profit"), typical=90)
        res = dipbuy.load(day) or res
    if res is None:
        older = [d for d in dipbuy.days() if d < day]
        res = dipbuy.load(older[0]) if older else None
        if res is None:
            return
    when = t("收盘后固定", "fixed after the close") if res["final"] else \
        t(f"实时，截至北京时间 {res['computed'][-5:]}", f"live, as of {res['computed'][-5:]} Beijing")
    st.caption(f":material/schedule: {fday(res['day'])} · {when}")
    v = _conditions(DIP_TABLE, "dip_settings", with_on=True, step=1.0)
    p = dipbuy.Params(drop5_min=v["drop5_min"][0], use_drop=v["drop5_min"][1],
                      inflow_min_m=v["inflow_min_m"][0], use_inflow=v["inflow_min_m"][1],
                      ma_n=int(v["ma_n"][0]), use_ma=v["ma_n"][1],
                      profit_min_m=v["profit_min_m"][0], use_profit=v["profit_min_m"][1],
                      change_max=v["change_max"][0], use_change=v["change_max"][1])
    d = dipbuy.apply(res["frame"], p)
    d["missed"] = d["missed"].map(_missed)
    hits = d[d["passes"]]
    st.markdown(t(f"**{len(hits)} 只股票**满足全部条件 · 检查了近5个交易日下跌 {dipbuy.SHORTLIST_DROP:g}% 以上的 "
                  f"{res.get('shortlisted')} 只股票。",
                  f"**{len(hits)} stocks** met every condition · {res.get('shortlisted')} stocks fell "
                  f"{dipbuy.SHORTLIST_DROP:g}% or more over 5 sessions and were checked."))
    show = hits if not hits.empty else d[d["rules_met"] == d["rules_met"].max()].head(15)
    if hits.empty:
        st.caption(t("没有股票满足全部条件——显示最接近的。", "Nothing met every condition - showing the closest."))
    ma_n = min((20, 60, 120), key=lambda n: abs(n - p.ma_n))
    cols = ["code", "name", "price", "pct_change", "drop5", "main_net", f"above_ma{ma_n}_pct", "profit_ttm",
            "turnover_rate", "float_mcap", "missed"]
    st.dataframe(style_signed(show[cols], ["pct_change", "drop5", "main_net"]), hide_index=True, height=320,
                 column_config={
                     "code": P.TextColumn(t("代码", "Code")), "name": P.TextColumn(t("名称", "Name")),
                     "price": P.NumberColumn(t("现价", "Price"), format="%.2f"),
                     "pct_change": P.NumberColumn(t("今日涨幅", "Change today"), format="%+.2f%%"),
                     "drop5": P.NumberColumn(t("近5日涨跌", "5-session change"), format="%+.1f%%"),
                     "main_net": P.NumberColumn(t("今日主力净流入", "Main inflow today"), format="compact"),
                     f"above_ma{ma_n}_pct": P.NumberColumn(t(f"距MA{ma_n}", f"vs MA{ma_n}"), format="%+.1f%%"),
                     "profit_ttm": P.NumberColumn(t("扣非净利润 TTM", "Profit excl. one-offs, TTM"), format="compact"),
                     "turnover_rate": P.NumberColumn(t("换手率", "Turnover"), format="%.2f%%"),
                     "float_mcap": P.NumberColumn(t("流通市值", "Float cap"), format="compact"),
                     "missed": P.TextColumn(t("未满足", "Missed"))})
    st.download_button(t("低位吸筹（CSV）", "Buy the dip (CSV)"), to_csv_bytes(show[cols]),
                       file_name=f"dipbuy_{res['day']}.csv", mime="text/csv", on_click="ignore",
                       icon=":material/download:")
    for n in res.get("notes", []):
        st.caption(note(n))
