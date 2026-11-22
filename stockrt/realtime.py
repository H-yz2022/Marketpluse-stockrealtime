"""Real-time quotes, whole-market snapshots, money flow and market breadth."""

from __future__ import annotations

import logging
import threading

import numpy as np
import pandas as pd

from . import http, storage
from .sources import eastmoney, sina, tencent
from .symbols import board, is_st, market

log = logging.getLogger(__name__)

UNIVERSE_MAX_AGE = 12 * 3600


def quotes(codes: list[str]) -> pd.DataFrame:
    df = tencent.quotes(codes)
    if df.empty:
        return df
    df["board"] = df["code"].map(board)
    df["is_st"] = df["name"].fillna("").map(is_st)
    return df


_refreshing: set[str] = set()


def _refresh_universe_later(mkt: str) -> None:
    """Re-download a stale stock list in the background (once at a time per market)."""
    if mkt in _refreshing:
        return
    _refreshing.add(mkt)

    def run() -> None:
        try:
            universe(mkt, refresh=True)
        except Exception as e:  # noqa: BLE001 - the stale list stays in use
            log.info("background stock-list refresh failed: %s", e)
        finally:
            _refreshing.discard(mkt)

    threading.Thread(target=run, daemon=True, name=f"universe-{mkt}").start()


def universe(mkt: str = "CN", refresh: bool = False) -> pd.DataFrame:
    """Every listed stock in a market (code, name). Cached on disk; refreshed every 12 hours in the background,
    so no request waits for it (the refresh used to add ~7 s to whichever snapshot found the list stale)."""
    key = f"universe_{mkt.lower()}"
    age = storage.age_seconds("universe", key)
    cached = storage.load_frame("universe", key)
    if cached is not None and not refresh and age is not None:
        if age >= UNIVERSE_MAX_AGE:
            _refresh_universe_later(mkt)
        return cached
    if cached is None and not refresh:
        # Cold start (e.g. a fresh free-tier server): the list bundled with the app saves ~100 slow page
        # requests. "Refresh stock lists" on the Data sources page re-downloads it.
        from . import sample

        bundled = sample.SAMPLE_DIR / "universe.parquet"
        if bundled.exists():
            u = pd.read_parquet(bundled)
            df = u[u["market"] == mkt][["code", "name"]].reset_index(drop=True)
            if not df.empty:
                storage.save_frame("universe", key, df)
                return df
    try:
        df = sina.universe_cn() if mkt == "CN" else sina.universe_hk()
        df = df[["code", "name"]]
        storage.save_frame("universe", key, df)
        return df
    except Exception as e:  # noqa: BLE001
        if cached is not None:
            log.warning("universe refresh failed, using stale list: %s", e)
            return cached
        raise


def market_snapshot(mkt: str = "CN") -> pd.DataFrame:
    """Real-time quote for every stock in the market (~5,500 A-shares in ~5 seconds)."""
    codes = universe(mkt)["code"].tolist()
    df = quotes(codes)
    if df.empty:
        return df
    df["suspended"] = (df["volume"].fillna(0) == 0) & (df["price"].fillna(0) == df["prev_close"].fillna(-1))
    return df


FLOW_CHUNK = 100                # symbols per East Money batch request


def main_flow(codes: list[str], workers: int = 8) -> pd.DataFrame:
    """Today's main-fund (large + extra-large order) net inflow per symbol.

    East Money's batch endpoint is tried first, 100 symbols per request and several requests at once (covers HK).
    Whatever it answers is kept; only the A-shares it didn't answer for (a refused or empty request) go to Sina,
    one request per symbol. Before 19 Nov 2026 one refused batch sent every symbol to Sina (3,800 requests, about
    2.5 minutes for 低位吸筹). The `source` column says which one answered each row.
    """
    cols = ["code", "main_net", "main_net_ratio", "xl_net", "l_net", "retail_net", "source"]
    if not codes:
        return pd.DataFrame(columns=cols)
    chunks = [codes[i:i + FLOW_CHUNK] for i in range(0, len(codes), FLOW_CHUNK)]
    parts = []
    for res in http.pmap(eastmoney.main_flow_batch, chunks, workers=6):
        if isinstance(res, pd.DataFrame) and not res.empty and res["main_net"].notna().any():
            parts.append(res.reindex(columns=cols))
        elif isinstance(res, Exception):
            log.info("eastmoney flow batch unavailable (%s)", res)
    have = {c for p in parts for c in p["code"]}
    missing = [c for c in codes if c not in have and market(c) == "CN"]
    if missing:
        rows = [r for r in http.pmap(sina.money_flow, missing, workers=workers) if isinstance(r, dict)]
        if rows:
            parts.append(pd.DataFrame(rows).reindex(columns=cols))
    if not parts:
        return pd.DataFrame(columns=cols)
    return pd.concat(parts, ignore_index=True).drop_duplicates("code").reindex(columns=cols)


def sources(df: pd.DataFrame) -> str:
    """'eastmoney 3,700 · sina 77': which source answered how many rows (for logs and notes)."""
    if df is None or df.empty or "source" not in df:
        return "none"
    return " · ".join(f"{s} {n:,}" for s, n in df["source"].fillna("?").value_counts().items())


def breadth(snap: pd.DataFrame) -> dict:
    """Market breadth / mood from a whole-market snapshot (rule based, no model)."""
    live = snap[~snap.get("suspended", False)] if "suspended" in snap else snap
    pct = live["pct_change"].dropna()
    if pct.empty:
        return {}
    up, down = int((pct > 0).sum()), int((pct < 0).sum())
    lu = live["limit_up"].notna() & (live["price"] >= live["limit_up"] - 1e-6)
    ld = live["limit_down"].notna() & (live["price"] <= live["limit_down"] + 1e-6)
    above_vwap = (live["price"] > live["vwap"]).sum() / max(live["vwap"].notna().sum(), 1)
    out = {
        "stocks": int(len(live)),
        "advancers": up,
        "decliners": down,
        "unchanged": int((pct == 0).sum()),
        "advance_ratio": up / max(up + down, 1),
        "limit_up": int(lu.sum()),
        "limit_down": int(ld.sum()),
        "median_change": float(pct.median()),
        "up_over_5": int((pct >= 5).sum()),
        "down_over_5": int((pct <= -5).sum()),
        "total_amount": float(live["amount"].sum(skipna=True)),
        "share_above_vwap": float(above_vwap),
    }
    # Mood: advance ratio and median move dominate; limit-up/down skew adds conviction.
    skew = (out["limit_up"] - out["limit_down"]) / max(out["limit_up"] + out["limit_down"], 1)
    score = 100 * (0.5 * (2 * out["advance_ratio"] - 1) + 0.3 * np.tanh(out["median_change"] / 1.5) + 0.2 * skew)
    out["mood_score"] = float(round(score, 1))
    out["mood"] = mood_label(score)
    return out


def mood_label(score: float) -> str:
    if score >= 40:
        return "Strongly bullish"
    if score >= 15:
        return "Bullish"
    if score > -15:
        return "Neutral"
    if score > -40:
        return "Bearish"
    return "Strongly bearish"
