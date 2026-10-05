"""Data downloads: daily datasets for any date range, intraday data, market snapshots, saved captures."""

import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from stockrt import capture, export
from stockrt.calendar import now_bj
from stockrt.config import INDEX_NAMES, STARTER_GROUPS
from stockrt.etfs import ETFS
from stockrt.export import stamp, to_csv_bytes, zip_bytes
from stockrt.sources.tencent import MKLINE_PERIODS
from stockrt.symbols import normalize
from ui import cache

names = {**cache.name_map(), **INDEX_NAMES}
fmt = lambda c: f"{c[2:]} · {names.get(c, c)}"  # noqa: E731
CHUNK = 40  # symbols per progress step


def parse_codes(text: str) -> tuple[list[str], list[str]]:
    good, bad = [], []
    for tok in re.split(r"[\s,;，；]+", text.strip()):
        if not tok:
            continue
        try:
            good.append(normalize(tok))
        except ValueError:
            bad.append(tok)
    return list(dict.fromkeys(good)), bad


def symbol_picker(prefix: str, allow_bulk: bool) -> list[str]:
    sources = ["Watchlist", "Theme groups", "Index ETFs & indices", "Last screen results", "Paste codes"]
    if allow_bulk:
        sources += ["All A-shares", "All Hong Kong stocks"]
    src = st.segmented_control("Symbols", sources, default="Watchlist", key=f"{prefix}_src", required=True)
    if src == "Watchlist":
        codes = st.multiselect("Watchlist symbols", st.session_state.watchlist, default=st.session_state.watchlist,
                               format_func=fmt, key=f"{prefix}_wl")
    elif src == "Theme groups":
        groups = st.pills("Groups", list(STARTER_GROUPS), selection_mode="multi", default=list(STARTER_GROUPS)[:3],
                          key=f"{prefix}_groups")
        codes = list(dict.fromkeys(c for g in groups or [] for c in STARTER_GROUPS[g]))
        st.caption(", ".join(fmt(c) for c in codes))
    elif src == "Index ETFs & indices":
        codes = [e["code"] for e in ETFS] + list(INDEX_NAMES)
        st.caption(f"{len(codes)} symbols: every ETF on the Index ETFs page plus their indices.")
    elif src == "Last screen results":
        res = st.session_state.get("scr_result")
        if not res:
            st.info("Run a screen on the Screener page first.")
            codes = []
        else:
            r = res["res"]
            incl = st.toggle("Include near misses", value=True, key=f"{prefix}_near")
            codes = r.passed["code"].tolist() + (r.near_misses["code"].tolist() if incl else [])
            st.caption(f"{len(codes)} symbols from the screen at {res['as_of']}.")
    elif src == "Paste codes":
        text = st.text_area("Codes - any format, separated by spaces, commas or new lines",
                            "600519 000858 0700.HK 510300.SH 300750", key=f"{prefix}_paste", height=90)
        codes, bad = parse_codes(text)
        if bad:
            st.warning("Not recognised: " + ", ".join(bad))
        st.caption(f"{len(codes)} symbols: " + ", ".join(fmt(c) for c in codes[:20]) + (" …" if len(codes) > 20 else ""))
    elif src == "All A-shares":
        codes = cache.universe("CN")["code"].tolist()
        st.caption(f"{len(codes):,} A-shares (Shanghai, Shenzhen, Beijing).")
    else:
        codes = cache.universe("HK")["code"].tolist()
        st.caption(f"{len(codes):,} Hong Kong stocks.")
    return codes


tab_daily, tab_intra, tab_snap, tab_cli = st.tabs(
    ["Daily datasets", "Intraday datasets", "Market snapshots", "Bulk & scheduled (command line)"])

# ---------------------------------------------------------------------------------------------- daily
with tab_daily:
    st.markdown("One row per symbol per trading day: OHLC, volume (shares), value traded, turnover rate, "
                "daily total return and corporate-action notes. Choose any date range.")
    codes = symbol_picker("dl", allow_bulk=True)
    today = now_bj().date()
    with st.container(horizontal=True, vertical_alignment="bottom"):
        preset = st.segmented_control("Date range", ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "Max", "Custom"],
                                      default="1Y", key="dl_range", required=True)
        adjusted = st.segmented_control("Prices", ["Ratio-adjusted", "Raw (as traded)"], default="Ratio-adjusted",
                                        key="dl_adj", required=True,
                                        help="Ratio-adjusted: dividends and splits reinvested (use for returns). "
                                             "Raw: the prices actually printed. Both files include close_raw and ret.")
    months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60}
    if preset == "Custom":
        rng = st.date_input("From - to", value=(today - timedelta(days=365), today), max_value=today, key="dl_dates")
        start, end = (rng if isinstance(rng, tuple) and len(rng) == 2 else (rng[0], today))
    elif preset == "Max":
        start, end = None, None
    elif preset == "YTD":
        start, end = date(today.year, 1, 1), None
    else:
        start, end = (pd.Timestamp(today) - pd.DateOffset(months=months[preset])).date(), None
    bulk = len(codes) > 60
    est = len(codes) * (0.06 if not bulk else 0.05)
    st.caption(f"{len(codes):,} symbols · {start or 'first listing'} → {end or 'latest'}"
               + (f" · bulk mode, roughly {est / 60:.0f}-{est / 30:.0f} min" if bulk else ""))
    if bulk and preset == "Max":
        st.warning("Full history for hundreds of symbols is large (≈ 5,000 rows each). Consider a shorter range, "
                   "or the command-line script on the last tab.", icon=":material/warning:")

    if st.button("Prepare daily dataset", type="primary", icon=":material/table_view:", disabled=not codes):
        frames, errors = [], []
        bar = st.progress(0.0, text="Starting…")
        for i in range(0, len(codes), CHUNK):
            chunk = codes[i:i + CHUNK]
            d, e = export.daily_dataset(chunk, names, start, end, adjusted=adjusted == "Ratio-adjusted",
                                        bulk=bulk, workers=8)
            frames.append(d)
            errors += e
            done = min(i + CHUNK, len(codes))
            bar.progress(done / len(codes), text=f"{done:,} / {len(codes):,} symbols")
        bar.empty()
        data = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
        st.session_state.dl_daily = {"data": data, "errors": errors, "range": (start, end), "adjusted": adjusted}

    res = st.session_state.get("dl_daily")
    if res:
        data = res["data"]
        if res["errors"]:
            with st.expander(f"{len(res['errors'])} symbols failed", icon=":material/error:"):
                st.write(res["errors"])
        if data.empty:
            st.warning("No rows returned.")
        else:
            n_sym = data["code"].nunique()
            st.success(f"{len(data):,} rows · {n_sym:,} symbols · {data['date'].min()} → {data['date'].max()} · "
                       f"{res['adjusted'].lower()} prices")
            st.dataframe(data.head(500), hide_index=True, height=300,
                         column_config={"ret": st.column_config.NumberColumn("ret", format="%.4f"),
                                        "amount": st.column_config.NumberColumn("amount", format="compact"),
                                        "volume": st.column_config.NumberColumn("volume", format="compact")})
            st.caption("Preview shows the first 500 rows; downloads contain everything.")
            tag = f"{stamp()}"
            long_csv = to_csv_bytes(data)
            wide_csv = to_csv_bytes(export.wide_closes(data))
            with st.container(horizontal=True):
                st.download_button("Long format (CSV)", long_csv, file_name=f"daily_long_{tag}.csv", mime="text/csv",
                                   on_click="ignore", type="primary", icon=":material/download:",
                                   help="One row per symbol per day - best for pandas, Excel pivot tables, SQL.")
                st.download_button("Close-price matrix (CSV)", wide_csv, file_name=f"daily_closes_wide_{tag}.csv",
                                   mime="text/csv", on_click="ignore", icon=":material/download:",
                                   help="Dates down, symbols across - best for correlations and returns.")
                per_symbol = {f"by_symbol/{c}.csv": to_csv_bytes(g) for c, g in data.groupby("code")} \
                    if n_sym <= 300 else {}
                st.download_button("ZIP bundle (+ README)", zip_bytes({
                    "daily_long.csv": long_csv, "daily_closes_wide.csv": wide_csv,
                    "README.txt": export.DAILY_README.encode("utf-8"), **per_symbol}),
                    file_name=f"daily_bundle_{tag}.zip", mime="application/zip", on_click="ignore",
                    icon=":material/folder_open:",
                    help="Long + wide files, a column guide, and one CSV per symbol (up to 300 symbols).")

# ---------------------------------------------------------------------------------------------- intraday
with tab_intra:
    st.markdown("Minute-level data for the **morning session plus the afternoon up to a cut-off** - the 14:00 view - "
                "or the full day. Price series cover A-shares and HK; OHLC bars are mainland only.")
    icodes = symbol_picker("di", allow_bulk=False)
    kind = st.segmented_control("Data", ["1-minute price & volume", "Minute OHLC bars (A-shares)"],
                                default="1-minute price & volume", key="di_kind", required=True)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        if kind == "1-minute price & volume":
            sessions = st.segmented_control("Sessions", ["Latest", "Last 5"], default="Latest", key="di_sess",
                                            required=True)
            cut_label = st.segmented_control("Up to (Beijing time, each day)",
                                             ["11:30", "13:30", "14:00", "14:30", "Full day"],
                                             default="14:00", key="di_cut", required=True)
        else:
            period = st.segmented_control("Bar size", list(MKLINE_PERIODS), default="1 min", key="di_period",
                                          required=True)
            st.caption("Up to 800 bars per symbol: ~3 sessions of 1-min, ~16 of 5-min, ~10 months of 60-min.")
    if st.button("Prepare intraday dataset", type="primary", icon=":material/table_view:", disabled=not icodes):
        with st.spinner(f"Fetching {len(icodes)} symbols…"):
            if kind == "1-minute price & volume":
                cut = None if cut_label == "Full day" else cut_label
                data, errors = export.intraday_dataset(icodes, names, "latest" if sessions == "Latest" else "last5",
                                                       cut, workers=8)
                label = f"1min_{'latest' if sessions == 'Latest' else '5d'}_to_{(cut or 'close').replace(':', '')}"
            else:
                data, errors = export.ohlc_dataset(icodes, names, MKLINE_PERIODS[period], workers=8)
                label = f"ohlc_{MKLINE_PERIODS[period]}"
        st.session_state.dl_intra = {"data": data, "errors": errors, "label": label}
    res = st.session_state.get("dl_intra")
    if res:
        if res["errors"]:
            with st.expander(f"{len(res['errors'])} symbols skipped", icon=":material/error:"):
                st.write(res["errors"])
        data = res["data"]
        if data.empty:
            st.warning("No rows returned.")
        else:
            dates = pd.to_datetime(data["datetime"]).dt.date
            st.success(f"{len(data):,} rows · {data['code'].nunique()} symbols · {dates.min()} → {dates.max()}")
            st.dataframe(data.head(500), hide_index=True, height=300)
            st.download_button("Intraday dataset (CSV)", to_csv_bytes(data), file_name=f"intraday_{res['label']}_{stamp()}.csv",
                               mime="text/csv", on_click="ignore", type="primary", icon=":material/download:")

# ---------------------------------------------------------------------------------------------- snapshots
with tab_snap:
    st.markdown("**Right now** - one row per listed stock with price, change, volume, value traded, turnover, "
                "volume ratio, valuation, float market cap and active buy/sell volume.")
    with st.container(horizontal=True):
        for mkt, label in (("CN", "A-shares"), ("HK", "Hong Kong")):
            if st.button(f"Load {label} snapshot", key=f"snap_{mkt}", icon=":material/bolt:"):
                with st.spinner(f"Fetching every {label} quote…"):
                    st.session_state[f"snap_{mkt}_data"] = cache.snapshot(mkt)
    for mkt, label in (("CN", "A-shares"), ("HK", "Hong Kong")):
        got = st.session_state.get(f"snap_{mkt}_data")
        if got:
            df, t = got
            st.download_button(f"{label} snapshot · {len(df):,} rows · {t} (CSV)", to_csv_bytes(df),
                               file_name=f"{mkt.lower()}_snapshot_{stamp()}.csv", mime="text/csv", on_click="ignore",
                               type="primary", icon=":material/download:", key=f"snap_dl_{mkt}")
    st.divider()
    st.markdown("**Saved captures** - point-in-time files from *Capture now* or auto-capture "
                "(Intraday at 14:00 page) and the scheduled script.")
    files = capture.list_snapshots()
    if files.empty:
        st.caption("No captures yet. Use **Capture now** on the Intraday at 14:00 page, or schedule the script.")
    else:
        st.dataframe(files[["date", "file", "size_kb", "modified"]], hide_index=True, height=240)
        pick = st.selectbox("File", files["path"].tolist(), format_func=lambda p: f"{Path(p).parent.name} / {Path(p).name}")
        if pick:
            st.download_button("Download selected capture", Path(pick).read_bytes(), file_name=Path(pick).name,
                               mime="text/csv", on_click="ignore", icon=":material/download:")

# ---------------------------------------------------------------------------------------------- CLI
with tab_cli:
    st.markdown("""
For very large downloads, or to capture data at 14:00 Beijing time while you're asleep in New York,
use the scripts in `scripts/` (they share this app's data layer and cache).

**Daily data for a date range** (watchlist, a group, codes, or the whole market):
""")
    st.code("python scripts/download_daily.py --codes 600519 000858 0700.HK --start 2024-01-01\n"
            "python scripts/download_daily.py --watchlist --start 2025-01-01 --zip\n"
            "python scripts/download_daily.py --all-cn --start 2026-01-01 --out data/exports", language="bash")
    st.markdown("**Point-in-time capture** (whole market + watchlist minute bars) - run it once now:")
    st.code("python scripts/capture_snapshot.py", language="bash")
    st.markdown("**Schedule it for 14:00 Beijing on weekdays** with Windows Task Scheduler. 14:00 Beijing is "
                "02:00 New York time during daylight saving (EDT) and 01:00 in winter (EST); this example "
                "uses EDT:")
    script = Path(__file__).resolve().parent.parent / "scripts" / "capture_snapshot.py"
    st.code(f'schtasks /Create /TN "StockRT 1400 capture" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 02:00 '
            f'/TR "\\"{sys.executable}\\" \\"{script}\\""', language="bash")
    st.caption("Files land in data/snapshots/<date>/ and appear under Saved captures.")
