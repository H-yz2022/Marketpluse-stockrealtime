"""Strategy lab - try your own parameters.

Each strategy keeps a compact "candidate pool": every stock-session of the backtest year that lies inside
generous bounds around the strategy's rules, with the measures the rules read and the returns that
followed. Choosing parameters is then a filter on the pool - the year re-scores in a fraction of a second,
without the 150 MB of raw bars behind the backtest. With the official parameters the pool reproduces the
official backtest exactly (tested).

Pools are research artifacts built once from the backtest download (`python scripts/strategy_lab.py
pools`) and kept with the code in research/lab_pools/. To stay small, returns are kept in whole basis
points and measures rounded to 4 decimals; so the official parameters are always shown from the official
backtest itself, and the pool is used once a parameter changes.

A caution shown next to every result: trying many settings on the same year finds some that look good
by chance. A setting chosen here has to prove itself on sessions it hasn't seen - the live record.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import labdata
from .config import ROOT
from .labdata import EXITS
from .strategies import STRATEGIES, fillable

POOL_DIR = ROOT / "research" / "lab_pools"


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str                 # "range" | "min" | "max" | "toggle" | "choice"
    default: object
    lo: float = 0.0           # widget bounds = the pool's bounds
    hi: float = 0.0
    step: float = 1.0
    help: str = ""
    options: tuple = ()


@dataclass(frozen=True)
class PoolSpec:
    time: str
    params: tuple[Param, ...]
    keep: Callable[[pd.DataFrame, pd.DataFrame], pd.Series]          # (state, frame) -> rows to store
    columns: Callable[[pd.DataFrame, pd.DataFrame], dict]             # (state, frame) -> stored measures
    select: Callable[[pd.DataFrame, dict], pd.Series]                 # (pool, params) -> picks


def _in(s, lo, hi):
    return (s >= lo) & (s <= hi)


def _cross_days(st: pd.DataFrame, f: pd.DataFrame) -> pd.Series:
    """Sessions since MA5 crossed above MA10 (0 = today); 9 = longer ago; NaN = MA5 not above MA10."""
    up = (st["ma5"] - st["ma10"]) > 0
    d = np.where(f["sp1"] <= 0, 0, np.where(f["sp2"] <= 0, 1, np.where(f["sp3"] <= 0, 2, 9)))
    return pd.Series(np.where(up, d, np.nan), index=st.index)


S1_PARAMS = (
    Param("pct", "涨幅 · Change today (%)", "range", (3.0, 5.0), 2.0, 7.0, 0.5),
    Param("vr", "量比 · Volume ratio above", "min", 1.0, 0.7, 3.0, 0.1),
    Param("turnover", "换手率 · Turnover (%)", "range", (3.0, 8.0), 2.0, 12.0, 0.5),
    Param("cap", "流通市值 · Float cap (¥ bn)", "range", (5.0, 20.0), 3.0, 40.0, 1.0),
    Param("cross_within", "MA5 crossed above MA10 within", "choice", 3, options=(0, 1, 2, 3),
          help="Sessions. 0 = rule off; 1 = the cross happened today."),
    Param("ma_up", "MA5 and MA10 rising", "toggle", True),
    Param("vol_price", "Volume and price up (量价齐升)", "toggle", True),
    Param("beats", "Beats the CSI 300", "toggle", True),
)


def _s1_keep(s, f):
    return s["eligible"] & fillable(STRATEGIES["S1"], s).fillna(False) & _in(s["pct_change"], 2, 7) & \
        (s["volume_ratio"] >= 0.7) & _in(s["turnover_rate"], 2, 12) & _in(s["float_mcap"], 3e9, 40e9)


def _s1_cols(s, f):
    return {"pct": s["pct_change"], "vr": s["volume_ratio"], "turnover": s["turnover_rate"],
            "cap": s["float_mcap"] / 1e9, "cross_days": _cross_days(s, f), "ma_up": s["ma_rising"].fillna(False),
            "vol_price": s["vol_price_up"].fillna(False), "beats": s["pct_change"] > s["csi300_pct"]}


def _s1_select(p, v):
    m = _in(p["pct"], *v["pct"]) & (p["vr"] > v["vr"]) & _in(p["turnover"], *v["turnover"]) & _in(p["cap"], *v["cap"])
    if v["cross_within"]:
        m &= p["cross_days"] < v["cross_within"]          # 99 = MA5 not above MA10
    for k in ("ma_up", "vol_price", "beats"):
        if v[k]:
            m &= p[k].astype(bool)
    return m


GAP_PARAMS = (
    Param("gap", "高开 · Opening gap (%)", "range", (2.0, 7.0), 1.0, 9.0, 0.5),
    Param("cap", "流通市值 · Float cap (¥ bn)", "range", (3.0, 50.0), 2.0, 80.0, 1.0),
    Param("yday_up", "Yesterday closed up", "toggle", False),
    Param("yday_lu", "Yesterday limit-up (接力)", "toggle", False),
)


def _gap_keep(s, f):
    return s["eligible"] & (s["open"] < s["limit_up"] - 0.005) & _in(s["gap_pct"], 1.0, 9.0) & \
        _in(s["float_mcap"], 2e9, 80e9)


def _gap_cols(s, f):
    return {"gap": s["gap_pct"], "cap": s["float_mcap"] / 1e9, "yday_up": s["yday_pct"] > 0,
            "yday_lu": s["yday_limit_up"].fillna(False)}


def _gap_select(p, v):
    m = _in(p["gap"], *v["gap"]) & _in(p["cap"], *v["cap"])
    if v["yday_up"]:
        m &= p["yday_up"].astype(bool)
    if v["yday_lu"]:
        m &= p["yday_lu"].astype(bool)
    return m


S3_PARAMS = GAP_PARAMS + (
    Param("holding", "Price at 10:00 ≥ the open (高开不破)", "toggle", True),
    Param("unfilled", "Gap never filled by 10:00", "toggle", False,
          help="The low of the first half hour stayed above yesterday's close."),
)


def _s3_keep(s, f):
    return _gap_keep(s, f) & fillable(STRATEGIES["S3"], s).fillna(False)


def _s3_cols(s, f):
    return _gap_cols(s, f) | {"holding": s["price"] >= s["open"], "unfilled": s["low"] > s["prev_close"]}


def _s3_select(p, v):
    m = _gap_select(p, v)
    if v["holding"]:
        m &= p["holding"].astype(bool)
    if v["unfilled"]:
        m &= p["unfilled"].astype(bool)
    return m


S4_PARAMS = (
    Param("first", "First board only (yesterday not limit-up)", "toggle", True),
    Param("fill", "成交假设 · Fill", "choice", "assume", options=("assume", "seal_breaks"),
          help="assume = every order at the limit price fills (optimistic); seal_breaks = only when the stock "
               "falls back below the limit before the close (pessimistic)."),
    Param("cap", "流通市值 · Float cap (¥ bn)", "range", (0.0, 200.0), 0.0, 200.0, 5.0,
          help="200 = no upper limit."),
)


def _s4_keep(s, f):
    return s["eligible"] & s["at_limit"].fillna(False) & ~s["sealed"].fillna(True)


def _s4_cols(s, f):
    return {"first": ~s["yday_limit_up"].fillna(False), "seal_breaks": s["close"] < s["limit_up"] - 0.005,
            "cap": s["float_mcap"] / 1e9}


def _s4_select(p, v):
    lo, hi = v["cap"]
    m = (p["cap"] >= lo) & ((p["cap"] <= hi) | (hi >= 200))      # 200 = no upper limit
    if v["first"]:
        m &= p["first"].astype(bool)
    if v["fill"] == "seal_breaks":
        m &= p["seal_breaks"].astype(bool)
    return m


S5_PARAMS = (
    Param("pct", "涨幅 · Change at 10:30 (%)", "range", (3.0, 7.0), 2.0, 8.0, 0.5),
    Param("vr", "量比 · Volume ratio above", "min", 2.0, 1.5, 5.0, 0.1),
    Param("cap", "流通市值 · Float cap (¥ bn)", "range", (5.0, 50.0), 3.0, 80.0, 1.0),
    Param("breakout", "Above the 20-session high (突破)", "toggle", True),
)


def _s5_keep(s, f):
    return s["eligible"] & fillable(STRATEGIES["S5"], s).fillna(False) & _in(s["pct_change"], 2, 8) & \
        (s["volume_ratio"] >= 1.5) & _in(s["float_mcap"], 3e9, 80e9)


def _s5_cols(s, f):
    return {"pct": s["pct_change"], "vr": s["volume_ratio"], "cap": s["float_mcap"] / 1e9,
            "breakout": s["above_high20"].fillna(False)}


def _s5_select(p, v):
    m = _in(p["pct"], *v["pct"]) & (p["vr"] > v["vr"]) & _in(p["cap"], *v["cap"])
    if v["breakout"]:
        m &= p["breakout"].astype(bool)
    return m


S6_PARAMS = (
    Param("oversold", "超跌 · 5-session return in the bottom (%)", "max", 5, 1, 15, 1,
          help="Percent of the market with the worst 5-session return up to yesterday's close."),
    Param("pct", "涨幅 · Change at 14:30 (%)", "range", (-7.0, -2.0), -9.0, 0.0, 0.5),
    Param("cap", "流通市值 · Float cap at least (¥ bn)", "min", 3.0, 2.0, 50.0, 1.0),
)


def _s6_keep(s, f):
    return s["eligible"] & fillable(STRATEGIES["S6"], s).fillna(False) & (s["ret5_rank"] <= 0.17) & \
        _in(s["pct_change"], -9, 0) & (s["float_mcap"] >= 2e9)


def _s6_cols(s, f):
    return {"ret5": s["ret5"], "pct": s["pct_change"], "cap": s["float_mcap"] / 1e9}


def _s6_select(p, v):
    # The same cut-off as the backtest: each session's quantile of the 5-session return (eligible stocks).
    cut = p["day"].map(p.attrs["cuts"][f"q{int(v['oversold'])}"])
    return (p["ret5"] <= cut) & _in(p["pct"], *v["pct"]) & (p["cap"] >= v["cap"])


SPECS: dict[str, PoolSpec] = {
    "S1": PoolSpec("14:30", S1_PARAMS, _s1_keep, _s1_cols, _s1_select),
    "S1b": PoolSpec("14:00", S1_PARAMS, _s1_keep, _s1_cols, _s1_select),
    "S2": PoolSpec("open", GAP_PARAMS, _gap_keep, _gap_cols, _gap_select),
    "S3": PoolSpec("10:00", S3_PARAMS, _s3_keep, _s3_cols, _s3_select),
    "S4": PoolSpec("14:30", S4_PARAMS, _s4_keep, _s4_cols, _s4_select),
    "S5": PoolSpec("10:30", S5_PARAMS, _s5_keep, _s5_cols, _s5_select),
    "S6": PoolSpec("14:30", S6_PARAMS, _s6_keep, _s6_cols, _s6_select),
}


def defaults(sid: str) -> dict:
    return {p.key: p.default for p in SPECS[sid].params}


# --------------------------------------------------------------------------- building (once, offline)

def build(frame: pd.DataFrame, idx: dict[str, pd.DataFrame], days: list[pd.Timestamp],
          chunk: int = 20) -> dict[str, pd.DataFrame]:
    """Every strategy's pool over `days` (the backtest's sessions)."""
    parts: dict[str, list[pd.DataFrame]] = {sid: [] for sid in SPECS}
    cuts: list[pd.DataFrame] = []
    days = sorted(pd.Timestamp(d) for d in days)
    for i in range(0, len(days), chunk):
        block = frame[frame["date"].isin(days[i:i + chunk])]
        for t in sorted({s.time for s in SPECS.values()}):
            st = labdata.state(block, t, idx)
            e = st["eligible"]
            st["ret5_rank"] = st["ret5"].where(e).groupby(st["day"]).rank(pct=True)
            if t == SPECS["S6"].time:
                q = st[e].groupby("day")["ret5"].quantile([k / 100 for k in range(1, 16)]).unstack()
                q.columns = [f"q{k}" for k in range(1, 16)]
                cuts.append(q)
            for sid, spec in SPECS.items():
                if spec.time != t:
                    continue
                keep = spec.keep(st, block).fillna(False)
                s, f = st[keep], block.loc[keep[keep].index]
                cols = spec.columns(s, f)
                out = pd.DataFrame({"day": s["day"], "code": s["code"], **cols,
                                    **{f"ret_{x}": s[f"ret_{x}"] for x in EXITS}})
                parts[sid].append(out)
    pools = {sid: _compact(pd.concat(p, ignore_index=True)) for sid, p in parts.items()}
    pools["S6"].attrs["cuts"] = pd.concat(cuts)
    return pools


def _compact(d: pd.DataFrame) -> pd.DataFrame:
    """Small on disk: returns in whole basis points (int16), measures rounded to 4 decimals (float32),
    rule flags as booleans. Rounding only moves a value that sits within 0.00005 of a boundary."""
    for c in d.columns:
        if c in ("day", "code"):
            continue
        if c.startswith("ret_"):
            d[c] = (d[c] * 1e4).round().clip(-32000, 32000).astype("Int16")
        elif d[c].dtype == "object" or d[c].dtype == "bool":
            d[c] = d[c].astype(bool)
        elif c == "cross_days":
            d[c] = d[c].fillna(99).astype("int8")
        elif d[c].dtype.kind == "f":
            d[c] = d[c].round(4).astype("float32")
    d["code"] = d["code"].astype("category")
    return d.sort_values(["day", "code"]).reset_index(drop=True)


def save(pools: dict[str, pd.DataFrame]) -> list:
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for sid, p in pools.items():
        path = POOL_DIR / f"{sid}.parquet"
        cuts = p.attrs.get("cuts")
        plain = p.copy()
        plain.attrs = {}                       # pandas would try to write attrs into the parquet metadata
        plain.to_parquet(path, index=False, compression="zstd")
        paths.append(path)
        if cuts is not None:
            cuts.reset_index().to_parquet(POOL_DIR / f"{sid}_cuts.parquet", index=False)
    return paths


def load(sid: str) -> pd.DataFrame:
    path = POOL_DIR / f"{sid}.parquet"
    if not path.exists():
        return pd.DataFrame()
    pool = pd.read_parquet(path)
    for x in EXITS:
        pool[f"ret_{x}"] = pool[f"ret_{x}"].astype("float32") / 1e4
    cuts = POOL_DIR / f"{sid}_cuts.parquet"
    if cuts.exists():
        pool.attrs["cuts"] = pd.read_parquet(cuts).set_index("day")
    return pool


def picks(sid: str, pool: pd.DataFrame, values: dict) -> pd.DataFrame:
    """The pool rows the parameters select, shaped like backtest trades (for lab.metrics / lab.daily)."""
    m = SPECS[sid].select(pool, values).fillna(False).astype(bool)
    out = pool[m.to_numpy()].assign(strategy=sid, all=True)
    out.attrs = {}
    return out
