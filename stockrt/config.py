"""Paths, defaults and the starter universe."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCKRT_DATA_DIR", ROOT / "data"))
CACHE_DIR = DATA_DIR / "cache"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"

for _d in (DATA_DIR, CACHE_DIR, SNAPSHOT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Annual risk-free rate used by Sharpe ratios (decimal). Roughly the 1-year
# China government bond yield; editable in the app sidebar.
DEFAULT_RISK_FREE = 0.015

# Benchmarks used for beta / correlation / "outperforming the market".
BENCHMARKS = {
    "CN": "sh000300",  # CSI 300
    "HK": "hkHSI",     # Hang Seng Index
}

INDEX_NAMES = {
    "sh000001": "SSE Composite",
    "sz399001": "SZSE Component",
    "sh000300": "CSI 300",
    "sh000016": "SSE 50",
    "sh000905": "CSI 500",
    "sh000852": "CSI 1000",
    "sz399006": "ChiNext",
    "sh000688": "STAR 50",
    "hkHSI": "Hang Seng Index",
    "hkHSCEI": "HS China Enterprises",
    "hkHSTECH": "Hang Seng TECH",
}

# Starter universe: well-known names grouped by theme. The screener and the
# market snapshot cover the whole market; these groups seed the watchlist.
STARTER_GROUPS: dict[str, list[str]] = {
    "Baijiu (white liquor)": ["sh600519", "sz000858", "sz000568", "sh600809", "sz002304", "sh603369"],
    "Telecom": ["sh600941", "sh601728", "sh600050", "sz000063", "hk00941"],
    "Banks": ["sh601398", "sh601939", "sh601288", "sh600036", "sz000001", "sh601166", "hk00005"],
    "HK tech & platforms": ["hk00700", "hk09988", "hk03690", "hk01810", "hk09618"],
    "HK financials": ["hk00005", "hk01299", "hk00388", "hk02318"],
    "New energy": ["sz300750", "sz002594", "sh601012"],
}

DEFAULT_WATCHLIST = [
    "sh600519", "sz000858", "sz000568", "sh600809",
    "sh600941", "sh601728", "sz000063",
    "sh601398", "sh600036",
    "hk00700", "hk09988", "hk00005",
]
