"""资金选股 - two money-flow screens on the latest session: live during it, fixed after the close.

    基础版（快速筛选）：近5个交易日主力资金净流入，排除ST，股价站稳20日均线。
      进阶条件（剔除假信号）：近3个交易日主力资金净流入，大单净流入占比大于5%，流通市值50亿–300亿，
      近20个交易日涨幅小于20%。
    热点题材共振版（龙头板块内选股）：所属概念题材（含AI算力），近3个交易日主力资金净流入，近3个交易日成交量
      放大50%以上，股价站稳20日均线。

How each condition is read (every threshold can be changed on the page):

    近5 / 近3日主力净流入   main-fund (large + extra-large orders) net inflow summed over the last 5 / 3 sessions,
                          today included - during the session, today so far (East Money's 5日 / 3日主力净额; after
                          the close, the stocks its batch request doesn't answer for are summed from East Money's
                          own daily history - the same definition. Sina's figures are not mixed in: its order
                          buckets differ, e.g. 贵州茅台 on 23 Nov 2026: Sina -832M, East Money +469M)
    大单净流入占比 > 5%     that 3-day main-fund net inflow as a share of the 3 sessions' turnover (East Money's
                          3日主力净占比)
    站稳20日均线           today's close above the average of the last 20 closes (today's included)
    流通市值               free-float market value at today's close
    近20日涨幅 < 20%        today's close against the close 20 sessions ago (forward-adjusted)
    近3日成交量放大 > 50%    volume of the last 3 sessions against the 3 sessions before them; during the session
                          today's volume is projected to a full day with the measured intraday curve (VOLUME_CURVE)
    所属概念               the company is in one of the chosen concepts (stockrt.themes, refreshed weekly)

Every condition except the theme moves with each session: during it the screen is recomputed every half hour
(10:00 to 14:30, or when opened, at most every 30 minutes), and once more after the close (from 15:08 Beijing),
which is the session's fixed result. Price, moving-average, volume and market-value measures are kept for every
non-ST stock trading that day; money flow is fetched for the stocks above their 20-day average (both versions
require it), so the thresholds re-filter instantly and the theme ranking covers the whole market.
"""

from __future__ import annotations

import json
import time as time_mod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import archive, http, sample
from .symbols import em_secid, from_em_secid, is_st

QUERY_BASIC_ZH = "基础版（快速筛选）：近5个交易日主力资金净流入，排除ST，股价站稳20日均线。"
QUERY_BASIC_EN = ("Basic (quick screen): main-fund net inflow over the last 5 trading days, no ST stocks, price "
                  "holding above the 20-day moving average.")
QUERY_ADV_ZH = ("进阶条件（剔除假信号）：近3个交易日主力资金净流入，大单净流入占比大于5%，流通市值50亿–300亿，"
                "近20个交易日涨幅小于20%。")
QUERY_ADV_EN = ("Advanced (removing false signals): main-fund net inflow over the last 3 trading days, large-order "
                "net inflow above 5% of turnover, free-float market value CNY 5-30 billion, and up less than 20% "
                "over the last 20 trading days.")
QUERY_HOT_ZH = ("热点题材共振版（龙头板块内选股）：所属概念题材（含AI算力），近3个交易日主力资金净流入，"
                "近3个交易日成交量放大50%以上，股价站稳20日均线。")
QUERY_HOT_EN = ("Hot-theme convergence (picks inside the leading sectors): in a chosen concept / theme (AI "
                "computing power included), main-fund net inflow over the last 3 trading days, volume up more than "
                "50% over the last 3 trading days, price holding above the 20-day moving average.")
AT = time(15, 8)            # the fixed after-close run
CLOSE = time(15, 0)
LIVE_FROM = time(9, 45)     # first live run: 15 minutes of trading
REFRESH_MIN = 30            # minutes between live recomputes
FLOW_CHUNK = 100
# Share of the day's volume traded after N minutes of continuous trading (A-shares): the average of the Shanghai
# Composite, Shenzhen Component, CSI 300 and ChiNext indices over 18-23 Nov 2026 (spread ±2-5 points). Volume is
# front-loaded - 29% by 10:00 against 12.5% of the time - so a straight-line projection would overstate a
# morning's full-day volume about 2.3 times.
VOLUME_CURVE = ((0, 0.0), (15, 0.191), (30, 0.289), (60, 0.414), (90, 0.503), (120, 0.586), (150, 0.698),
                (180, 0.780), (210, 0.854), (240, 1.0))


def day_share(elapsed_min: float) -> float:
    """The share of a normal day's volume traded after `elapsed_min` continuous-trading minutes."""
    xs, ys = zip(*VOLUME_CURVE)
    return float(max(np.interp(elapsed_min, xs, ys), 0.05))


@dataclass
class BasicParams:
    inflow5_min_m: float = 0.0           # CNY millions over 5 sessions
    use_inflow5: bool = True
    ma_above_min: float = 0.0            # % above MA20
    use_ma: bool = True
    advanced: bool = True
    inflow3_min_m: float = 0.0           # CNY millions over 3 sessions
    use_inflow3: bool = True
    ratio3_min: float = 5.0              # % of the 3 sessions' turnover
    use_ratio3: bool = True
    cap_min_bn: float = 5.0              # free float, CNY billions
    cap_max_bn: float = 30.0
    use_cap: bool = True
    chg20_max: float = 20.0              # % over 20 sessions
    use_chg20: bool = True


@dataclass
class HotParams:
    boards: list[str] = field(default_factory=lambda: ["算力概念"])
    inflow3_min_m: float = 0.0
    use_inflow3: bool = True
    vol3_min: float = 50.0               # % more volume than the 3 sessions before
    use_vol3: bool = True
    ma_above_min: float = 0.0
    use_ma: bool = True


# --------------------------------------------------------------------------- measures

def measures(code: str, day: date, price: float, prev_close: float | None, volume: float | None,
             amount: float | None) -> dict:
    """MA20, the 20- and 3-session change, the 3-session volume change and the 3-session turnover, from the saved
    history panel when it covers the stock (else downloaded) plus today's close."""
    from . import panel
    from .sources import tencent

    h = panel.history(code, day, prev_close, min_rows=25)
    if h is None:
        h = tencent.daily(code, start=day - timedelta(days=70), end=day, fq="qfq")
        h = h[h["date"] < pd.Timestamp(day)]
    out = {"code": code}
    c = list(h["close"].astype(float)) + [float(price)]
    if len(c) < 21:
        return out
    out["ma20"] = float(np.mean(c[-20:]))
    out["above_ma20_pct"] = 100 * (c[-1] / out["ma20"] - 1)
    out["chg20"] = 100 * (c[-1] / c[-21] - 1)
    out["chg3"] = 100 * (c[-1] / c[-4] - 1)
    if volume:
        v = list(h["volume"].astype(float)) + [float(volume)]
        before = sum(v[-6:-3])
        out["vol3_change"] = 100 * (sum(v[-3:]) / before - 1) if before > 0 else np.nan
    if amount and "amount" in h:
        out["amount3"] = float(h["amount"].tail(2).sum()) + float(amount)
    return out


def flows(codes: list[str], day: date, amount3: dict[str, float], workers: int = 8,
          live: bool = False) -> pd.DataFrame:
    """3- and 5-session main-fund net inflow and the 3-session net share of turnover, all East Money's definition.
    Its batch figures first (100 stocks a request, fetched patiently - eastmoney.gather). After the close, the
    stocks it still doesn't answer for are summed from its per-stock daily history (another server, same figures:
    the 3-day number is exactly the last 3 daily ones). During the session that history stops at yesterday, so
    those stocks stay unknown rather than mixing in another source's figures."""
    from .sources import eastmoney

    cols = ["code", "main_net3", "main_ratio3", "main_net5", "main_ratio5", "flow_source"]

    def batch(chunk: list[str]) -> list[dict]:
        data = eastmoney.push_patient("/api/qt/ulist.np/get", {
            "fltt": 2, "secids": ",".join(em_secid(c) for c in chunk), "fields": "f12,f13,f267,f268,f164,f165"})
        num = lambda d, k: d.get(k) if isinstance(d.get(k), (int, float)) else None  # noqa: E731
        return [{"code": from_em_secid(f"{d['f13']}.{d['f12']}"), "main_net3": num(d, "f267"),
                 "main_ratio3": num(d, "f268"), "main_net5": num(d, "f164"), "main_ratio5": num(d, "f165"),
                 "flow_source": "eastmoney"} for d in data.get("diff") or [] if num(d, "f267") is not None]

    chunks = [codes[i:i + FLOW_CHUNK] for i in range(0, len(codes), FLOW_CHUNK)]
    rows, _ = eastmoney.gather(chunks, batch, sleep=time_mod.sleep)
    have = {r["code"] for r in rows}

    def one(code: str) -> dict | None:
        f = eastmoney.daily_flow(code, 6)
        if f.empty or f["date"].iloc[-1] != day:
            return None                                   # not up to the session: unknown rather than wrong
        n3 = float(f["main_net"].tail(3).sum())
        a3 = amount3.get(code)
        return {"code": code, "main_net3": n3, "main_ratio3": 100 * n3 / a3 if a3 else None,
                "main_net5": float(f["main_net"].tail(5).sum()) if len(f) >= 5 else None, "main_ratio5": None,
                "flow_source": "eastmoney history"}

    missing = [c for c in codes if c not in have]
    if missing and not live:
        rows += [r for r in http.pmap(one, missing, workers=workers) if isinstance(r, dict)]
    return pd.DataFrame(rows, columns=cols).drop_duplicates("code")


def run(day: date, snap: pd.DataFrame, progress: Callable[[float, str], None] | None = None,
        workers: int = 12, now: datetime | None = None) -> dict:
    """Screen the session `day` from a snapshot - live during the session, the closing one after it - and save it."""
    from .calendar import now_bj, status

    now = now or now_bj()
    notes: list[str] = []
    live = day == now.date() and now.time() < CLOSE
    snap = snap[pd.to_datetime(snap["time"]).dt.date == day]
    base = snap[(snap["price"] > 0) & (snap["volume"].fillna(0) > 0)].copy()
    base = base[~base["name"].fillna("").map(is_st)]
    if live:
        share = day_share(status("CN", now).elapsed_min)
        base["volume"] = base["volume"] / share           # today's volume as a projected full day
        notes.append(f"Live as of {now:%H:%M}: today's volume projected to a full day (about {100 * share:.0f}% "
                     "of a day's volume is usually traded by now); money flow is today's so far.")
    if progress:
        progress(0.02, f"资金选股: {len(base)} non-ST stocks trading")
    recs = base[["code", "price", "prev_close", "volume", "amount"]].to_dict("records")
    rows = []
    for i in range(0, len(recs), 400):
        for r in http.pmap(lambda r: measures(r["code"], day, r["price"], r["prev_close"], r["volume"], r["amount"]),
                           recs[i:i + 400], workers=workers):
            if isinstance(r, dict):
                rows.append(r)
        if progress:
            progress(0.02 + 0.5 * min(i + 400, len(recs)) / max(len(recs), 1),
                     f"均线与成交量 · Moving averages and volume {min(i + 400, len(recs))}/{len(recs)}")
    d = base.merge(pd.DataFrame(rows) if rows else pd.DataFrame(columns=["code"]), on="code", how="left")
    above = d.loc[d["above_ma20_pct"] > 0, "code"].tolist()
    if progress:
        progress(0.55, f"近3日 / 近5日主力资金 · Main-fund flow for {len(above)} stocks above MA20")
    amount3 = dict(zip(d["code"], d.get("amount3", pd.Series(np.nan, index=d.index))))
    f = flows(above, day, amount3, workers=workers, live=live)
    if live and above and len(f) < 0.5 * len(above):
        # A live list with most money flow unknown would show nothing - keep the last good one (the page falls
        # back to it) and let the scheduler retry in 10 minutes. East Money throttles some servers for a while.
        raise http.SourceUnavailable(f"East Money answered for only {len(f)} of {len(above)} stocks' money flow; "
                                     "keeping the last list and retrying shortly")
    d = d.merge(f, on="code", how="left")
    src = " · ".join(f"{s} {n:,}" for s, n in f["flow_source"].value_counts().items()) if not f.empty else "none"
    notes.append(f"3- and 5-day money flow: {src} (of {len(above):,} stocks above MA20).")
    if len(f) < 0.9 * len(above):
        notes.append(f"Money flow unknown for {len(above) - len(f):,} stocks (no answer from East Money).")
    keep = ["code", "name", "price", "pct_change", "ma20", "above_ma20_pct", "chg20", "chg3", "vol3_change",
            "main_net3", "main_ratio3", "main_net5", "main_ratio5", "float_mcap", "turnover_rate", "amount",
            "flow_source"]
    frame = d.reindex(columns=keep)
    res = {"day": day, "frame": frame, "notes": notes, "checked": len(base), "flow_checked": len(above),
           "computed": now.strftime("%Y-%m-%d %H:%M"), "hot": hot_ranking(frame), "final": not live}
    save(res)
    return res


def stale(res: dict | None, now: datetime) -> bool:
    """Whether today's saved result should be recomputed: none yet, or a live one older than REFRESH_MIN (or from
    before the close, once the session is over)."""
    if res is None:
        return True
    if res.get("final", True):
        return False
    if now.time() >= CLOSE or res["day"] < now.date():
        return True
    age = now.replace(tzinfo=None) - datetime.strptime(res["computed"], "%Y-%m-%d %H:%M")
    return age > timedelta(minutes=REFRESH_MIN)


def bucket(now: datetime) -> str:
    """The job key's time part: one live run per half hour, one after the close."""
    if now.time() >= CLOSE:
        return "close"
    return f"{now.hour:02d}{now.minute // REFRESH_MIN * REFRESH_MIN:02d}"


# --------------------------------------------------------------------------- the conditions

def _finish(d: pd.DataFrame, labels: dict[str, str], sort: str) -> pd.DataFrame:
    cols = list(labels)
    ok = d[cols].fillna(False).astype(bool)
    d["rules_met"] = ok.sum(axis=1)
    d["passes"] = d["rules_met"] == len(cols)
    d["missed"] = [", ".join(labels[c] for c, y in zip(cols, row) if not y) for row in ok.itertuples(index=False)]
    return d.sort_values(["passes", "rules_met", sort], ascending=False, na_position="last").reset_index(drop=True)


def apply_basic(frame: pd.DataFrame, p: BasicParams | None = None) -> pd.DataFrame:
    p = p or BasicParams()
    d = frame.copy()
    d["r_inflow5"] = (d["main_net5"] > p.inflow5_min_m * 1e6) if p.use_inflow5 else True
    d["r_ma"] = (d["above_ma20_pct"] > p.ma_above_min) if p.use_ma else True
    labels = {"r_inflow5": "近5日主力净流入", "r_ma": "20日均线"}
    if p.advanced:
        d["r_inflow3"] = (d["main_net3"] > p.inflow3_min_m * 1e6) if p.use_inflow3 else True
        d["r_ratio3"] = (d["main_ratio3"] > p.ratio3_min) if p.use_ratio3 else True
        d["r_cap"] = d["float_mcap"].between(p.cap_min_bn * 1e9, p.cap_max_bn * 1e9) if p.use_cap else True
        d["r_chg20"] = (d["chg20"] < p.chg20_max) if p.use_chg20 else True
        labels |= {"r_inflow3": "近3日主力净流入", "r_ratio3": "大单净占比", "r_cap": "流通市值",
                   "r_chg20": "近20日涨幅"}
    return _finish(d, labels, "main_net5")


def apply_hot(frame: pd.DataFrame, theme_members: pd.DataFrame, p: HotParams | None = None) -> pd.DataFrame:
    """theme_members: themes.members(map, boards) - code and the chosen concepts each company is in."""
    p = p or HotParams()
    d = frame.merge(theme_members, on="code", how="left")
    d["r_theme"] = d["themes"].notna()
    d["r_inflow3"] = (d["main_net3"] > p.inflow3_min_m * 1e6) if p.use_inflow3 else True
    d["r_vol3"] = (d["vol3_change"] > p.vol3_min) if p.use_vol3 else True
    d["r_ma"] = (d["above_ma20_pct"] > p.ma_above_min) if p.use_ma else True
    labels = {"r_theme": "所属概念", "r_inflow3": "近3日主力净流入", "r_vol3": "近3日放量", "r_ma": "20日均线"}
    return _finish(d, labels, "main_net3")


# --------------------------------------------------------------------------- archive (rolling window)

def _base(day: date):
    return archive.RESULTS_DIR / "cn" / "flowpicks" / day.isoformat()


def save(res: dict):
    if sample.is_sample():
        return None
    base = _base(res["day"])
    base.parent.mkdir(parents=True, exist_ok=True)
    res["frame"].to_parquet(base.with_suffix(".parquet"), index=False, compression="zstd")
    meta = {k: res.get(k) for k in ("notes", "checked", "flow_checked", "computed", "hot", "final")}
    base.with_suffix(".json").write_text(json.dumps({"day": res["day"].isoformat(), **meta}, ensure_ascii=False),
                                         encoding="utf-8")
    return base


def load(day: date) -> dict | None:
    base = _base(day)
    if not base.with_suffix(".parquet").exists():
        return None
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8")) if base.with_suffix(".json").exists() \
        else {}
    return {"day": day, "frame": pd.read_parquet(base.with_suffix(".parquet")), "notes": meta.get("notes", []),
            "checked": meta.get("checked"), "flow_checked": meta.get("flow_checked"), "computed": meta.get("computed"),
            "hot": meta.get("hot"), "compact": bool(meta.get("compact")),
            "final": meta.get("final") is not False}


def days() -> list[date]:
    d = archive.RESULTS_DIR / "cn" / "flowpicks"
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.parquet")), reverse=True) if d.exists() else []


def hot_ranking(frame: pd.DataFrame) -> list[dict] | None:
    """The session's leading themes (themes.hot), kept with the session: it needs every stock's 3-day change, which
    an older, shrunk session no longer has."""
    from . import themes

    m = themes.load_map()
    if m is None or frame.empty:
        return None
    return json.loads(themes.hot(m, frame, n=30).to_json(orient="records", force_ascii=False))


def prune(keep: int = archive.KEEP_SESSIONS, history: int = archive.SCREEN_HISTORY) -> list:
    """20 sessions: the last week in full (every non-ST stock); older ones shrunk to the stocks with a 3- or 5-day
    main-fund net inflow - every stock either version can pick at the default thresholds - with the session's
    leading themes recorded first."""
    def shrink(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[(frame["main_net3"] > 0) | (frame["main_net5"] > 0)]

    def remember_hot(meta: dict, frame: pd.DataFrame) -> None:
        if not meta.get("hot"):
            meta["hot"] = hot_ranking(frame)

    return archive.keep_screen_window(days(), _base, shrink, before_shrink=remember_hot, keep=keep, history=history)
