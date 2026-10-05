"""Built-in sample dataset (offline mode).

The repo ships a small, fixed dataset in sample_data/ (one trading session of
quotes, minute bars and money flow; ten years of daily bars for the starter
symbols; ~60 sessions for every other A-share). In sample mode every provider
function is answered from those files and the network is switched off, so the
app opens instantly, costs nothing to host and stays small in memory - the
default on a free hosting tier.

Mode: environment variable STOCKRT_DATA_MODE=sample|live (default live), or
set_mode() at runtime (the sidebar switch). Build or refresh the dataset with
scripts/build_sample.py.
"""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pandas as pd

SAMPLE_DIR = Path(os.environ.get("STOCKRT_SAMPLE_DIR", Path(__file__).resolve().parent.parent / "sample_data"))

_mode = os.environ.get("STOCKRT_DATA_MODE", "live").strip().lower()
_REPLAY: dict[str, Callable] = {}


def available() -> bool:
    return (SAMPLE_DIR / "meta.json").exists()


def is_sample() -> bool:
    return _mode == "sample" and available()


def set_mode(mode: str) -> None:
    global _mode
    _mode = mode


def meta() -> dict:
    if not available():
        return {}
    return json.loads((SAMPLE_DIR / "meta.json").read_text(encoding="utf-8"))


def replay(name: str):
    """Decorator for provider functions: answered from sample_data/ when sample mode is on."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if is_sample():
                return _REPLAY[name](*args, **kwargs)
            return fn(*args, **kwargs)

        return wrapper

    return deco


def _register(name: str):
    def deco(fn):
        _REPLAY[name] = fn
        return fn

    return deco


@functools.lru_cache(maxsize=None)
def _table(name: str) -> pd.DataFrame:
    return pd.read_parquet(SAMPLE_DIR / f"{name}.parquet")


@functools.lru_cache(maxsize=None)
def _json(name: str) -> dict:
    return json.loads((SAMPLE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _daily_for(code: str) -> pd.DataFrame:
    # Predicate push-down: only this symbol's row groups are read, keeping memory low.
    return pd.read_parquet(SAMPLE_DIR / "daily.parquet", filters=[("code", "==", code)])


# --------------------------------------------------------------------------- Tencent

@_register("tencent.quotes")
def _quotes(codes: list[str], workers: int = 6) -> pd.DataFrame:
    from .sources.tencent import QUOTE_COLUMNS

    q = _table("quotes")
    out = q[q["code"].isin(list(dict.fromkeys(codes)))]
    return out.reindex(columns=QUOTE_COLUMNS).reset_index(drop=True)


def _minutes_for(code: str) -> pd.DataFrame:
    m = _table("minutes")
    return m[m["code"] == code]


@_register("tencent.minute_5day")
def _minute_5day(code: str) -> pd.DataFrame:
    return _minutes_for(code).drop(columns=["code"]).sort_values("datetime").reset_index(drop=True)


@_register("tencent.minute_today")
def _minute_today(code: str) -> tuple[date, float | None, pd.DataFrame]:
    m = _minutes_for(code)
    if m.empty:
        day = date.fromisoformat(meta().get("as_of", "2000-01-01"))
        cols = ["datetime", "price", "volume", "amount", "cum_volume", "cum_amount", "session"]
        return day, None, pd.DataFrame(columns=cols)
    day = m["date"].max()
    today = m[m["date"] == day]
    pc = today["prev_close"].iloc[0]
    bars = today.drop(columns=["code", "date", "prev_close"]).sort_values("datetime").reset_index(drop=True)
    return day, (float(pc) if pd.notna(pc) else None), bars


@_register("tencent.daily")
def _daily(code: str, start: date | None = None, end: date | None = None, fq: str = "") -> pd.DataFrame:
    from .sources.tencent import DAILY_COLUMNS

    df = _daily_for(code).reindex(columns=DAILY_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]
    return df.sort_values("date").reset_index(drop=True)


@_register("tencent.minute_ohlc")
def _minute_ohlc(code: str, period: str = "m1", count: int = 800) -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])


# --------------------------------------------------------------------------- Sina

@_register("sina.universe_cn")
def _universe_cn() -> pd.DataFrame:
    u = _table("universe")
    return u[u["market"] == "CN"][["code", "name"]].reset_index(drop=True)


@_register("sina.universe_hk")
def _universe_hk() -> pd.DataFrame:
    u = _table("universe")
    return u[u["market"] == "HK"][["code", "name"]].reset_index(drop=True)


@_register("sina.factor_segments")
def _factor_segments(code: str) -> list[tuple[date, float, float]]:
    return [(date.fromisoformat(d), a, b) for d, a, b in _json("factors").get(code, [])]


def _flow_row(code: str) -> dict | None:
    f = _table("flow")
    r = f[f["code"] == code]
    return None if r.empty else r.iloc[0].to_dict()


@_register("sina.money_flow")
def _money_flow(code: str) -> dict:
    r = _flow_row(code)
    if r is None:
        raise ValueError(f"no money-flow data for {code} in the sample")
    return {"code": code, "main_net": r["main_net"], "main_in": None, "main_out": None, "xl_net": None,
            "l_net": None, "retail_net": r.get("retail_net"), "main_net_ratio": r.get("main_net_ratio"),
            "source": "sample"}


@_register("sina.money_flow_rank")
def _money_flow_rank(workers: int = 4) -> pd.DataFrame:
    return _table("flow").copy()


# --------------------------------------------------------------------------- East Money

@_register("eastmoney.main_flow_batch")
def _main_flow_batch(codes: list[str]) -> pd.DataFrame:
    f = _table("flow")
    out = f[f["code"].isin(codes)][["code", "main_net", "main_net_ratio", "retail_net"]].copy()
    out["xl_net"], out["l_net"], out["source"] = None, None, "sample"
    return out


@_register("eastmoney.intraday_flow")
def _intraday_flow(code: str) -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime", "main_net", "small_net", "medium_net", "large_net", "xl_net"])


@_register("eastmoney.fund_fees")
def _fund_fees(code: str) -> dict:
    fees = _json("fees").get(code)
    if fees is None:
        raise ValueError(f"no fee data for {code} in the sample")
    return fees


@_register("eastmoney.fund_profile")
def _fund_profile(code: str) -> dict:
    return {}


@_register("eastmoney.announcements")
def _announcements(code: str, n: int = 30) -> pd.DataFrame:
    a = _table("announcements")
    return a[a["code"] == code].drop(columns=["code"]).head(n).reset_index(drop=True)


def picks() -> dict | None:
    """Precomputed front-page result (14:00 / 14:30 replay) saved with the sample, if present."""
    f = SAMPLE_DIR / "picks.json"
    if not f.exists():
        return None
    meta = json.loads(f.read_text(encoding="utf-8"))
    rows = _table("picks")
    frames = {cp: rows[rows["checkpoint"] == cp].reset_index(drop=True) for cp in meta["checkpoints"]}
    return {"day": date.fromisoformat(meta["day"]), "frames": frames, "index_pct": meta["index_pct"],
            "pending": [], "notes": meta["notes"], "candidates": meta["candidates"], "params": meta["params"]}


def describe() -> str:
    m = meta()
    if not m:
        return "No sample dataset found."
    built = datetime.fromisoformat(m["built_at"]).strftime("%Y-%m-%d")
    return (f"Built-in sample data: A-shares as of {m['as_of']} (HK {m.get('as_of_hk', m['as_of'])}), "
            f"built {built}. {m['n_quotes']:,} quotes; daily history for {m['n_full_history']} symbols "
            f"({m['history_years']} years) and the last {m['recent_sessions']} sessions for every other A-share.")
