"""Data sources: live health of every upstream endpoint, the field map, and cache controls."""

import shutil
import time

import pandas as pd
import streamlit as st

from stockrt import http, realtime, selftest
from stockrt.config import CACHE_DIR, SNAPSHOT_DIR

st.markdown("China market data has no official free API. This app reads the JSON / text endpoints behind "
            "Tencent, Sina and East Money's own web pages, converts their units, and falls back between them. "
            "This page shows what is reachable from **your** network right now.")

with st.container(horizontal=True):
    run = st.button("Run self-test", type="primary", icon=":material/speed:",
                    help="One small request to each endpoint (≈ 5 seconds).")
    if st.button("Reset paused sources", icon=":material/refresh:",
                 help="Sources that fail repeatedly are paused for a few minutes; this un-pauses them."):
        http.reset_breakers()
        st.toast("Circuit breakers reset")
if run:
    with st.spinner("Testing every endpoint…"):
        st.session_state.selftest = (pd.DataFrame(selftest.run_all()), time.strftime("%H:%M:%S"))

if "selftest" in st.session_state:
    df, when = st.session_state.selftest
    ok = int(df["ok"].sum())
    st.markdown(f"**Self-test at {when}** · {ok} of {len(df)} endpoints working")
    df = df.assign(status=df["ok"].map({True: "✅ working", False: "❌ failing"}))
    st.dataframe(df[["status", "source", "endpoint", "latency_ms", "sample", "used_for", "fallback", "error"]],
                 hide_index=True, column_config={
                     "status": st.column_config.TextColumn("Status", width="small"),
                     "latency_ms": st.column_config.NumberColumn("Latency", format="%d ms"),
                     "sample": st.column_config.TextColumn("Sample", width="medium"),
                     "used_for": st.column_config.TextColumn("Used for"),
                     "fallback": st.column_config.TextColumn("If it fails"),
                     "error": st.column_config.TextColumn("Error", width="medium")})
    if not df["ok"].all():
        st.caption("East Money's quote hosts often drop connections from outside mainland China. Every feature that "
                   "uses them has a fallback (see the last columns), so failing rows there are expected.")

st.markdown("**Live host health** · every request the app has made since it started")
h = http.health()
if not h:
    st.caption("No requests yet.")
else:
    now = time.time()
    st.dataframe(pd.DataFrame([{
        "host": x.host, "requests ok": x.ok, "failed": x.failed,
        "avg latency (ms)": round(x.avg_latency_ms) if x.avg_latency_ms else None,
        "last success": f"{now - x.last_ok:,.0f} s ago" if x.last_ok else "never",
        "state": f"paused {x.paused_until - now:,.0f} s" if x.paused_until > now else "active",
        "last error": x.last_error or "",
    } for x in h]), hide_index=True)

st.markdown("**Where each field comes from**")
st.dataframe(pd.DataFrame([
    ["Real-time price, change, OHLC, volume, value", "Tencent qt.gtimg.cn", "-", "~300 symbols per request; full A-share market in ~3 s"],
    ["Turnover rate, volume ratio (量比), float cap", "Tencent qt.gtimg.cn", "Sina list (no volume ratio)", "A-share lots ×100 → shares, except STAR Market (already shares)"],
    ["Active buy / sell volume (外盘 / 内盘)", "Tencent qt.gtimg.cn", "-", "Mainland only"],
    ["1-minute series (today, last 5 sessions)", "Tencent minute/query, day/query", "-", "Cumulative volume → per-minute"],
    ["Minute OHLC bars", "Tencent kline/mkline", "1-minute series", "Mainland only; 800 bars"],
    ["Daily bars incl. amount & turnover", "Tencent newfqkline", "disk cache", "2,000 bars per request; paged for long histories"],
    ["Adjustment factors", "Sina qfq.js / hfq.js", "dividend text in Tencent bars", "Rebuilt with the ratio method (providers publish additive)"],
    ["Main-fund net inflow (主力)", "East Money ulist (batch)", "Sina per stock", "Large + extra-large orders"],
    ["Stock universe (all listed codes)", "Sina Market_Center", "cached list", "HK pages hold 60 rows and the count is understated - paged until empty"],
    ["ETF expense ratio / net assets", "East Money fund pages", "cached 7 days", "HK ETFs: maintained in stockrt/etfs.py"],
    ["Index P/E (trailing)", "Tencent index quote", "-", "Mainland indices only"],
    ["Announcements + links", "East Money np-anotice-stock", "-", "Title → East Money page; PDF → pdf.dfcfw.com"],
], columns=["Field", "Primary source", "Fallback", "Notes"]), hide_index=True)

st.markdown("**Storage**")


def folder_size(path) -> tuple[int, float]:
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files) / 1e6


with st.container(horizontal=True):
    n_cache, mb_cache = folder_size(CACHE_DIR)
    n_snap, mb_snap = folder_size(SNAPSHOT_DIR)
    st.metric("Cache files", f"{n_cache:,}", f"{mb_cache:,.1f} MB", delta_color="off", border=True)
    st.metric("Saved captures", f"{n_snap:,}", f"{mb_snap:,.1f} MB", delta_color="off", border=True)
with st.container(horizontal=True):
    if st.button("Clear in-memory caches", icon=":material/refresh:",
                 help="Forces every page to refetch (quotes, snapshots, history)."):
        st.cache_data.clear()
        st.toast("Caches cleared")
    if st.button("Refresh stock lists", icon=":material/sync:", help="Re-download every A-share and HK code (≈ 20 s)."):
        with st.spinner("Downloading stock lists…"):
            n_cn = len(realtime.universe("CN", refresh=True))
            n_hk = len(realtime.universe("HK", refresh=True))
        st.cache_data.clear()
        st.toast(f"{n_cn:,} A-shares, {n_hk:,} HK stocks")
    if st.button("Delete cached daily history", icon=":material/delete:",
                 help="Daily bars are re-downloaded on next use. Saved captures are not touched."):
        shutil.rmtree(CACHE_DIR / "daily", ignore_errors=True)
        st.cache_data.clear()
        st.toast("Daily cache deleted")
st.caption(f"Cache: {CACHE_DIR} · Captures: {SNAPSHOT_DIR}")
