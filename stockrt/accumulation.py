"""主力暗中吸筹 - quiet accumulation by main funds (the morning screen).

    主力暗中吸筹筛选条件：请列出昨日（昨日数据）：成交量大于前4日成交量，今日（实时数据）：主力资金流向
    环比增长率突增，-1%<涨幅<1%的股票（上午9:45，10:15，10:45，11:15）。

How it is read:

    昨日 成交量大于前4日   yesterday's volume above each of the 4 sessions before it (switchable: their total
                         or average), from the saved end-of-day volume books
    主力资金流向          the main-fund net inflow, cumulative from the open, minute by minute: Sina's
                         extra-large orders (特大单). One request per stock, reachable from abroad, and it
                         carries the price too. (East Money's 主力 - extra-large + large - blocks bulk minute
                         requests, so it can't serve a whole-market screen reliably.)
    环比增长率突增        at each checkpoint, the inflow grew by at least 100% since the previous checkpoint
                         (09:31 - the opening auction's minute - for 09:45), by at least ¥3M, and is a net
                         inflow (all adjustable)
    -1% < 涨幅 < 1%      the price change at that checkpoint
    时点                  09:45, 10:15, 10:45, 11:15 Beijing

Each stock's minute series is downloaded once a checkpoint has passed; the result keeps
every candidate's measures, so the thresholds can be changed on the page without downloading again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime

import numpy as np
import pandas as pd

from . import archive, http, opening, sample
from .symbols import is_st

QUERY_ZH = ("主力暗中吸筹筛选条件：请列出昨日（昨日数据）：成交量大于前4日成交量，"
            "今日（实时数据）：主力资金流向环比增长率突增，-1%<涨幅<1%的股票（上午9:45，10:15，10:45，11:15）。")
QUERY_EN = ("Quiet accumulation: stocks whose volume yesterday beat the previous 4 sessions, and whose main-fund net "
            "inflow today surges from one checkpoint to the next while the price stays within ±1% (9:45, 10:15, "
            "10:45, 11:15 Beijing).")
CHECKPOINTS = ("09:45", "10:15", "10:45", "11:15")
PREVIOUS = {"09:45": "09:31", "10:15": "09:45", "10:45": "10:15", "11:15": "10:45"}
GRACE = timedelta(minutes=1)


@dataclass
class Params:
    surge_pct: float = 100.0        # growth of the main inflow since the previous checkpoint, %
    min_increase_m: float = 3.0     # and at least this much more inflow, CNY millions
    need_inflow: bool = True        # and a net inflow (not just less outflow)
    pct_lo: float = -1.0            # -1% < change < 1% at the checkpoint (strict)
    pct_hi: float = 1.0


def reached(day: date, now: datetime) -> tuple[str, ...]:
    if day < now.date():
        return CHECKPOINTS
    return tuple(c for c in CHECKPOINTS if (now - GRACE).time() >= dtime(*map(int, c.split(":"))))


def yesterday_list(day: date, mode: str = "each", min_amount: float = 20e6) -> tuple[date | None, pd.DataFrame]:
    """昨日：成交量大于前4日成交量 - the previous session against the 4 before it (volume books)."""
    books = archive.load_volume_history("CN", before=day, n=5)
    if len(books) < 5:
        return (books[0][0] if books else None), pd.DataFrame()
    yday, b = books[0]
    b = b.rename(columns={"close": "price"}).copy()
    b["is_st"] = b["name"].fillna("").map(is_st)
    d = opening.volume_vs_prior(b, books[1:5], mode, 1.0, min_amount, True)
    return yday, d.rename(columns={"price": "yday_close", "pct_change": "yday_pct", "volume": "yday_volume",
                                   "amount": "yday_amount", "vs_ref": "yday_vs_ref", "vs_avg": "yday_vs_avg"})


def _hhmm(t) -> str:
    return pd.Timestamp(t).strftime("%H:%M")


def measures(code: str, day: date, cps: tuple[str, ...]) -> dict | None:
    """The main-fund inflow (cumulative from the open) and the price change at each checkpoint and at 09:31,
    from Sina's minute money-flow series (one request; price included)."""
    from .sources import sina

    try:
        session, at = sina.minute_flow_at(code, ("09:31", *cps))
    except Exception:  # noqa: BLE001 - nothing for this stock
        return None
    if not at or session != day.isoformat():
        return None
    out = {"code": code, "flow_09:31": at.get("09:31", (np.nan, np.nan))[0]}
    for c in cps:
        f, pct = at.get(c, (np.nan, np.nan))
        out[f"flow_{c}"], out[f"pct_{c}"] = f, pct
    return out


def compute(day: date, snap: pd.DataFrame | None, cps: tuple[str, ...],
            progress: Callable[[float, str], None] | None = None, workers: int = 12) -> dict:
    """Candidates (yesterday's list, today's range touching ±1%) and their measures at the reached checkpoints."""
    yday, cands = yesterday_list(day)
    notes = []
    if cands.empty:
        return {"day": day, "yesterday": yday, "frame": cands, "checkpoints": cps, "candidates": 0,
                "notes": ["The previous 5 sessions' volume books aren't all saved yet."]}
    if snap is not None and not snap.empty:
        today = snap[pd.to_datetime(snap["time"]).dt.date == day]
        if not today.empty:
            lo = 100 * (today["low"] / today["prev_close"] - 1)
            hi = 100 * (today["high"] / today["prev_close"] - 1)
            ok = today[(lo < 1) & (hi > -1)]["code"]          # some minute inside (-1%, +1%)
            cands = cands[cands["code"].isin(ok)]
    rows: list[dict] = []
    codes = cands["code"].tolist()
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        for r in http.pmap(lambda c: measures(c, day, cps), chunk, workers=workers):
            if isinstance(r, dict):
                rows.append(r)
        if progress:
            progress(min(i + 40, len(codes)) / max(len(codes), 1),
                     f"主力资金分时 · Main-fund minute flow {min(i + 40, len(codes))}/{len(codes)}")
    m = pd.DataFrame(rows)
    frame = cands.merge(m, on="code", how="inner") if not m.empty else cands.iloc[0:0]
    if len(m) < len(codes):
        notes.append(f"Minute flow unavailable for {len(codes) - len(m)} of {len(codes)} stocks.")
    return {"day": day, "yesterday": yday, "frame": frame, "checkpoints": cps, "candidates": len(codes),
            "notes": notes}


def apply(frame: pd.DataFrame, cps: tuple[str, ...], p: Params | None = None) -> pd.DataFrame:
    """Per checkpoint: inflow growth, surge and price rule; `hits` = checkpoints where every rule held."""
    p = p or Params()
    d = frame.copy()
    hit_cols = []
    for c in cps:
        prev = d[f"flow_{PREVIOUS[c]}"]
        cur = d[f"flow_{c}"]
        inc = cur - prev
        growth = np.where(prev.abs() > 0, 100 * inc / prev.abs(), np.where(inc > 0, np.inf, 0))
        d[f"growth_{c}"] = growth
        surge = (inc >= p.min_increase_m * 1e6) & (d[f"growth_{c}"] >= p.surge_pct)
        if p.need_inflow:
            surge &= cur > 0
        price_ok = (d[f"pct_{c}"] > p.pct_lo) & (d[f"pct_{c}"] < p.pct_hi)
        d[f"hit_{c}"] = surge & price_ok
        hit_cols.append(f"hit_{c}")
    d["hits"] = d[hit_cols].sum(axis=1) if hit_cols else 0
    d["first_hit"] = [next((c for c in cps if r[f"hit_{c}"]), "") for _, r in d.iterrows()] if hit_cols else ""
    return d.sort_values(["hits", "yday_vs_ref"], ascending=False).reset_index(drop=True)


def _partial_key(day: date, cps: tuple[str, ...]) -> str:
    return f"acc_{day.isoformat()}_{'-'.join(c.replace(':', '') for c in cps)}"


def save_partial(res: dict) -> None:
    """Checkpoints reached so far (shared cache; the finished morning goes to the archive)."""
    from . import storage

    key = _partial_key(res["day"], tuple(res["checkpoints"]))
    storage.save_frame("accumulation", key, res["frame"])
    storage.save_json("accumulation", key, {"yesterday": str(res["yesterday"]), "candidates": res["candidates"],
                                            "notes": res["notes"]})


def load_partial(day: date, cps: tuple[str, ...]) -> dict | None:
    from . import storage

    key = _partial_key(day, cps)
    frame, meta = storage.load_frame("accumulation", key), storage.load_json("accumulation", key)
    if frame is None or meta is None:
        return None
    return {"day": day, "frame": frame, "checkpoints": cps, **meta}


def save(res: dict) -> object:
    if sample.is_sample() or res["frame"] is None:
        return None
    base = archive.RESULTS_DIR / "cn" / "accumulation" / res["day"].isoformat()
    base.parent.mkdir(parents=True, exist_ok=True)
    res["frame"].to_parquet(base.with_suffix(".parquet"), index=False, compression="zstd")
    base.with_suffix(".json").write_text(pd.Series({
        "day": res["day"].isoformat(), "yesterday": str(res["yesterday"]), "checkpoints": list(res["checkpoints"]),
        "candidates": res["candidates"], "notes": res["notes"]}).to_json(force_ascii=False), encoding="utf-8")
    return base


def load(day: date) -> dict | None:
    import json

    base = archive.RESULTS_DIR / "cn" / "accumulation" / day.isoformat()
    if not base.with_suffix(".parquet").exists():
        return None
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    return {"day": day, "frame": pd.read_parquet(base.with_suffix(".parquet")), "checkpoints":
            tuple(meta["checkpoints"]), "candidates": meta["candidates"], "notes": meta["notes"],
            "yesterday": meta.get("yesterday")}


def days() -> list[date]:
    d = archive.RESULTS_DIR / "cn" / "accumulation"
    return sorted((date.fromisoformat(f.stem) for f in d.glob("*.parquet")), reverse=True) if d.exists() else []


def prune(keep: int = archive.KEEP_SESSIONS) -> list:
    removed = []
    for d in days()[keep:]:
        for ext in (".parquet", ".json"):
            f = archive.RESULTS_DIR / "cn" / "accumulation" / f"{d.isoformat()}{ext}"
            if f.exists():
                f.unlink()
                removed.append(f)
    return removed
