"""History panel: the last 30 sessions of daily bars for every A-share, in one small file.

The live rebuilds (尾盘选股 checkpoints, the morning accumulation screen, the 长线 screen) need each
candidate's recent history - moving averages, yesterday's volume, the 5-session average. Fetching it per
stock doubled the requests of every live update. The daily job saves this panel after the close
(results/cn/panel.parquet, replaced each day), so during the next session the history is read from disk
and only intraday data is downloaded.

Prices are forward-adjusted as of the panel's last session. If a stock goes ex-dividend today, its
previous close no longer matches the panel's last close; that stock falls back to a fresh download.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd

from . import archive, http, sample
from .sources import tencent

SESSIONS = 30
COLUMNS = ["code", "date", "open", "close", "high", "low", "volume", "amount", "turnover_rate"]
_lock = threading.Lock()
_cache: dict = {"key": None, "by_code": {}}


def path():
    return archive.RESULTS_DIR / "cn" / "panel.parquet"


def build(codes: list[str], end: date, progress: Callable[[float, str], None] | None = None,
          workers: int = 16) -> pd.DataFrame:
    """Daily bars (forward-adjusted) of the last SESSIONS sessions up to `end`, for every code."""
    start = end - timedelta(days=SESSIONS * 2 + 20)

    def one(code: str) -> pd.DataFrame:
        b = tencent.daily(code, start=start, end=end, fq="qfq")
        return b.tail(SESSIONS).assign(code=code) if not b.empty else b

    parts = []
    for i in range(0, len(codes), 200):
        for r in http.pmap(one, codes[i:i + 200], workers=workers):
            if isinstance(r, pd.DataFrame) and not r.empty:
                parts.append(r)
        if progress:
            progress(min(i + 200, len(codes)) / max(len(codes), 1), f"History panel {min(i + 200, len(codes)):,}")
    df = pd.concat(parts, ignore_index=True).reindex(columns=COLUMNS) if parts else pd.DataFrame(columns=COLUMNS)
    for c in ("volume", "amount", "turnover_rate"):
        df[c] = df[c].astype("float32")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def save(df: pd.DataFrame) -> object:
    if sample.is_sample() or df.empty:
        return None
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False, compression="zstd")
    return p


def last_day() -> date | None:
    p = path()
    if not p.exists():
        return None
    return max(pd.read_parquet(p, columns=["date"])["date"])


def _by_code() -> dict[str, pd.DataFrame]:
    p = path()
    key = (str(p), p.stat().st_mtime) if p.exists() else None
    with _lock:
        if _cache["key"] != key:
            if key is None:
                _cache.update(key=None, by_code={})
            else:
                df = pd.read_parquet(p)
                df["date"] = pd.to_datetime(df["date"])
                _cache.update(key=key, by_code={c: g.reset_index(drop=True) for c, g in df.groupby("code")})
        return _cache["by_code"]


def history(code: str, day: date, prev_close: float | None = None, min_rows: int = 10) -> pd.DataFrame | None:
    """Daily bars before `day` from the panel, shaped like tencent.daily(); None when the panel can't stand in
    (missing, not up to the previous session, too short, or an ex-date since: prev_close disagrees)."""
    from . import tradingdays

    g = _by_code().get(code)
    if g is None or len(g) < min_rows:
        return None
    h = g[g["date"] < pd.Timestamp(day)]
    if h.empty:
        return None
    prev = tradingdays.previous_trading_day("CN", day)
    if prev is not None and h["date"].iloc[-1].date() < prev:
        return None
    if prev_close and abs(float(h["close"].iloc[-1]) / prev_close - 1) > 0.002:
        return None                                   # ex-dividend (or bad data): download instead
    return h.assign(event="").drop(columns="code")
