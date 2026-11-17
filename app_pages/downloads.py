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
from ui.i18n import desktop, group_name, index_name, t

names = {**cache.name_map(), **{c: index_name(n) for c, n in INDEX_NAMES.items()}}
fmt = lambda c: f"{c[2:]} · {names.get(c, c)}"  # noqa: E731
CHUNK = 40  # symbols per progress step
SOURCES = {"watchlist": ("自选股", "Watchlist"), "groups": ("主题组", "Theme groups"),
           "etfs": ("指数ETF与指数", "Index ETFs & indices"), "screen": ("上次选股结果", "Last screen results"),
           "paste": ("粘贴代码", "Paste codes"), "all_cn": ("全部A股", "All A-shares"),
           "all_hk": ("全部港股", "All Hong Kong stocks")}


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
    sources = ["watchlist", "groups", "etfs", "screen", "paste"] + (["all_cn", "all_hk"] if allow_bulk else [])
    src = st.segmented_control(t("股票范围", "Symbols"), sources, default="watchlist", key=f"{prefix}_src_v2",
                               required=True, format_func=lambda s: t(*SOURCES[s]))
    if src == "watchlist":
        codes = st.multiselect(t("自选股", "Watchlist symbols"), st.session_state.watchlist,
                               default=st.session_state.watchlist, format_func=fmt, key=f"{prefix}_wl")
    elif src == "groups":
        groups = st.pills(t("主题组", "Groups"), list(STARTER_GROUPS), selection_mode="multi",
                          default=list(STARTER_GROUPS)[:3], key=f"{prefix}_groups", format_func=group_name)
        codes = list(dict.fromkeys(c for g in groups or [] for c in STARTER_GROUPS[g]))
        st.caption(", ".join(fmt(c) for c in codes))
    elif src == "etfs":
        codes = [e["code"] for e in ETFS] + list(INDEX_NAMES)
        st.caption(t(f"{len(codes)} 只：指数ETF页面上的全部ETF及其指数。",
                     f"{len(codes)} symbols: every ETF on the Index ETFs page plus their indices."))
    elif src == "screen":
        res = st.session_state.get("scr_result")
        if not res:
            st.info(t("请先在“选股器”页面运行一次选股。", "Run a screen on the Screener page first."))
            codes = []
        else:
            r = res["res"]
            incl = st.toggle(t("包含差一条的股票", "Include near misses"), value=True, key=f"{prefix}_near")
            codes = r.passed["code"].tolist() + (r.near_misses["code"].tolist() if incl else [])
            st.caption(t(f"来自 {res['as_of']} 选股的 {len(codes)} 只股票。",
                         f"{len(codes)} symbols from the screen at {res['as_of']}."))
    elif src == "paste":
        text = st.text_area(t("代码——任意格式，用空格、逗号或换行分隔", "Codes - any format, separated by spaces, commas or new lines"),
                            "600519 000858 0700.HK 510300.SH 300750", key=f"{prefix}_paste", height=90)
        codes, bad = parse_codes(text)
        if bad:
            st.warning(t("无法识别：", "Not recognised: ") + ", ".join(bad))
        st.caption(t(f"{len(codes)} 只：", f"{len(codes)} symbols: ") + ", ".join(fmt(c) for c in codes[:20])
                   + (" …" if len(codes) > 20 else ""))
    elif src == "all_cn":
        codes = cache.universe("CN")["code"].tolist()
        st.caption(t(f"{len(codes):,} 只A股（上海、深圳、北京）。", f"{len(codes):,} A-shares (Shanghai, Shenzhen, Beijing)."))
    else:
        codes = cache.universe("HK")["code"].tolist()
        st.caption(t(f"{len(codes):,} 只港股。", f"{len(codes):,} Hong Kong stocks."))
    return codes


tab_names = [t("日线数据集", "Daily datasets"), t("分时数据集", "Intraday datasets"), t("全市场快照", "Market snapshots")]
if desktop():           # the command-line tab is for a developer's copy of the code
    tab_daily, tab_intra, tab_snap = st.tabs(tab_names)
    tab_cli = None
else:
    tab_daily, tab_intra, tab_snap, tab_cli = st.tabs(tab_names + [t("批量与定时（命令行）", "Bulk & scheduled (command line)")])

# ---------------------------------------------------------------------------------------------- daily
with tab_daily:
    st.markdown(t("每只股票每个交易日一行：开高低收、成交量（股）、成交额、换手率、日总收益和除权除息说明。可选择任意日期范围。",
                  "One row per symbol per trading day: OHLC, volume (shares), value traded, turnover rate, "
                  "daily total return and corporate-action notes. Choose any date range."))
    codes = symbol_picker("dl", allow_bulk=True)
    today = now_bj().date()
    with st.container(horizontal=True, vertical_alignment="bottom"):
        preset = st.segmented_control(t("日期范围", "Date range"), ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "Max", "Custom"],
                                      default="1Y", key="dl_range", required=True,
                                      format_func=lambda p: {"YTD": t("今年以来", "YTD"), "Max": t("全部", "Max"),
                                                             "Custom": t("自定义", "Custom")}.get(p, p))
        adjusted = st.segmented_control(t("价格", "Prices"), ["adj", "raw"], default="adj", key="dl_adj_v2", required=True,
                                        format_func=lambda a: t("比例复权", "Ratio-adjusted") if a == "adj"
                                        else t("不复权（实际成交价）", "Raw (as traded)"),
                                        help=t("比例复权：分红和拆股再投资（用于计算收益）。不复权：实际成交的价格。两种文件都包含 close_raw 和 ret。",
                                               "Ratio-adjusted: dividends and splits reinvested (use for returns). "
                                               "Raw: the prices actually printed. Both files include close_raw and ret."))
    months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60}
    if preset == "Custom":
        rng = st.date_input(t("从 - 到", "From - to"), value=(today - timedelta(days=365), today), max_value=today,
                            key="dl_dates")
        start, end = (rng if isinstance(rng, tuple) and len(rng) == 2 else (rng[0], today))
    elif preset == "Max":
        start, end = None, None
    elif preset == "YTD":
        start, end = date(today.year, 1, 1), None
    else:
        start, end = (pd.Timestamp(today) - pd.DateOffset(months=months[preset])).date(), None
    bulk = len(codes) > 60
    est = len(codes) * (0.06 if not bulk else 0.05)
    st.caption(t(f"{len(codes):,} 只 · {start or '上市首日'} → {end or '最新'}"
                 + (f" · 批量模式，约 {est / 60:.0f}–{est / 30:.0f} 分钟" if bulk else ""),
                 f"{len(codes):,} symbols · {start or 'first listing'} → {end or 'latest'}"
                 + (f" · bulk mode, roughly {est / 60:.0f}-{est / 30:.0f} min" if bulk else "")))
    if bulk and preset == "Max":
        st.warning(t("几百只股票的全部历史数据量很大（每只约 5,000 行）。可以缩短范围，或使用最后一个标签页的命令行脚本。",
                     "Full history for hundreds of symbols is large (≈ 5,000 rows each). Consider a shorter range, "
                     "or the command-line script on the last tab."), icon=":material/warning:")

    if st.button(t("准备日线数据集", "Prepare daily dataset"), type="primary", icon=":material/table_view:", disabled=not codes):
        frames, errors = [], []
        bar = st.progress(0.0, text=t("正在开始…", "Starting…"))
        for i in range(0, len(codes), CHUNK):
            chunk = codes[i:i + CHUNK]
            d, e = export.daily_dataset(chunk, names, start, end, adjusted=adjusted == "adj", bulk=bulk, workers=8)
            frames.append(d)
            errors += e
            done = min(i + CHUNK, len(codes))
            bar.progress(done / len(codes), text=t(f"{done:,} / {len(codes):,} 只", f"{done:,} / {len(codes):,} symbols"))
        bar.empty()
        data = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
        st.session_state.dl_daily = {"data": data, "errors": errors, "range": (start, end), "adjusted": adjusted}

    res = st.session_state.get("dl_daily")
    if res:
        data = res["data"]
        if res["errors"]:
            with st.expander(t(f"{len(res['errors'])} 只失败", f"{len(res['errors'])} symbols failed"), icon=":material/error:"):
                st.write(res["errors"])
        if data.empty:
            st.warning(t("没有返回数据。", "No rows returned."))
        else:
            n_sym = data["code"].nunique()
            kind = t("比例复权", "ratio-adjusted") if res["adjusted"] == "adj" else t("不复权", "raw")
            st.success(t(f"{len(data):,} 行 · {n_sym:,} 只 · {data['date'].min()} → {data['date'].max()} · {kind}价格",
                         f"{len(data):,} rows · {n_sym:,} symbols · {data['date'].min()} → {data['date'].max()} · "
                         f"{kind} prices"))
            st.dataframe(data.head(500), hide_index=True, height=300,
                         column_config={"ret": st.column_config.NumberColumn("ret", format="%.4f"),
                                        "amount": st.column_config.NumberColumn("amount", format="compact"),
                                        "volume": st.column_config.NumberColumn("volume", format="compact")})
            st.caption(t("预览显示前 500 行；下载包含全部。", "Preview shows the first 500 rows; downloads contain everything."))
            tag = f"{stamp()}"
            long_csv = to_csv_bytes(data)
            wide_csv = to_csv_bytes(export.wide_closes(data))
            with st.container(horizontal=True):
                st.download_button(t("长格式（CSV）", "Long format (CSV)"), long_csv, file_name=f"daily_long_{tag}.csv",
                                   mime="text/csv", on_click="ignore", type="primary", icon=":material/download:",
                                   help=t("每只股票每天一行——最适合 pandas、Excel 数据透视表、SQL。",
                                          "One row per symbol per day - best for pandas, Excel pivot tables, SQL."))
                st.download_button(t("收盘价矩阵（CSV）", "Close-price matrix (CSV)"), wide_csv,
                                   file_name=f"daily_closes_wide_{tag}.csv", mime="text/csv", on_click="ignore",
                                   icon=":material/download:",
                                   help=t("日期为行、股票为列——最适合计算相关系数和收益。",
                                          "Dates down, symbols across - best for correlations and returns."))
                per_symbol = {f"by_symbol/{c}.csv": to_csv_bytes(g) for c, g in data.groupby("code")} \
                    if n_sym <= 300 else {}
                st.download_button(t("ZIP 打包（含说明）", "ZIP bundle (+ README)"), zip_bytes({
                    "daily_long.csv": long_csv, "daily_closes_wide.csv": wide_csv,
                    "README.txt": export.DAILY_README.encode("utf-8"), **per_symbol}),
                    file_name=f"daily_bundle_{tag}.zip", mime="application/zip", on_click="ignore",
                    icon=":material/folder_open:",
                    help=t("长格式 + 宽格式文件、字段说明，以及每只股票一个 CSV（最多 300 只）。",
                           "Long + wide files, a column guide, and one CSV per symbol (up to 300 symbols)."))

# ---------------------------------------------------------------------------------------------- intraday
with tab_intra:
    st.markdown(t("分钟级数据：**上午盘加下午截至某个时间**（14:00 视角）或全天。价格序列覆盖A股和港股；分钟K线仅限内地。",
                  "Minute-level data for the **morning session plus the afternoon up to a cut-off** - the 14:00 view - "
                  "or the full day. Price series cover A-shares and HK; OHLC bars are mainland only."))
    icodes = symbol_picker("di", allow_bulk=False)
    kind = st.segmented_control(t("数据", "Data"), ["price", "ohlc"], default="price", key="di_kind_v2", required=True,
                                format_func=lambda k: t("1 分钟价格与成交量", "1-minute price & volume") if k == "price"
                                else t("分钟K线（A股）", "Minute OHLC bars (A-shares)"))
    with st.container(horizontal=True, vertical_alignment="bottom"):
        if kind == "price":
            sessions = st.segmented_control(t("交易日", "Sessions"), ["latest", "last5"], default="latest",
                                            key="di_sess_v2", required=True,
                                            format_func=lambda s: t("最新", "Latest") if s == "latest" else t("最近 5 天", "Last 5"))
            cut = st.segmented_control(t("截至（北京时间，每天）", "Up to (Beijing time, each day)"),
                                       ["11:30", "13:30", "14:00", "14:30", "full"], default="14:00", key="di_cut_v2",
                                       required=True, format_func=lambda c: t("全天", "Full day") if c == "full" else c)
        else:
            period = st.segmented_control(t("K线周期", "Bar size"), list(MKLINE_PERIODS), default="1 min", key="di_period",
                                          required=True)
            st.caption(t("每只股票最多 800 根：1 分钟约 3 个交易日，5 分钟约 16 个，60 分钟约 10 个月。",
                         "Up to 800 bars per symbol: ~3 sessions of 1-min, ~16 of 5-min, ~10 months of 60-min."))
    if st.button(t("准备分时数据集", "Prepare intraday dataset"), type="primary", icon=":material/table_view:",
                 disabled=not icodes):
        with st.spinner(t(f"正在读取 {len(icodes)} 只股票…", f"Fetching {len(icodes)} symbols…")):
            if kind == "price":
                cut_v = None if cut == "full" else cut
                data, errors = export.intraday_dataset(icodes, names, sessions, cut_v, workers=8)
                label = f"1min_{'latest' if sessions == 'latest' else '5d'}_to_{(cut_v or 'close').replace(':', '')}"
            else:
                data, errors = export.ohlc_dataset(icodes, names, MKLINE_PERIODS[period], workers=8)
                label = f"ohlc_{MKLINE_PERIODS[period]}"
        st.session_state.dl_intra = {"data": data, "errors": errors, "label": label}
    res = st.session_state.get("dl_intra")
    if res:
        if res["errors"]:
            with st.expander(t(f"跳过 {len(res['errors'])} 只", f"{len(res['errors'])} symbols skipped"),
                             icon=":material/error:"):
                st.write(res["errors"])
        data = res["data"]
        if data.empty:
            st.warning(t("没有返回数据。", "No rows returned."))
        else:
            dates = pd.to_datetime(data["datetime"]).dt.date
            st.success(t(f"{len(data):,} 行 · {data['code'].nunique()} 只 · {dates.min()} → {dates.max()}",
                         f"{len(data):,} rows · {data['code'].nunique()} symbols · {dates.min()} → {dates.max()}"))
            st.dataframe(data.head(500), hide_index=True, height=300)
            st.download_button(t("分时数据集（CSV）", "Intraday dataset (CSV)"), to_csv_bytes(data),
                               file_name=f"intraday_{res['label']}_{stamp()}.csv", mime="text/csv", on_click="ignore",
                               type="primary", icon=":material/download:")

# ---------------------------------------------------------------------------------------------- snapshots
with tab_snap:
    st.markdown(t("**此刻**——每只上市股票一行，含价格、涨跌、成交量、成交额、换手率、量比、估值、流通市值以及主动买卖量。",
                  "**Right now** - one row per listed stock with price, change, volume, value traded, turnover, "
                  "volume ratio, valuation, float market cap and active buy/sell volume."))
    markets = (("CN", t("A股", "A-shares")), ("HK", t("港股", "Hong Kong")))
    with st.container(horizontal=True):
        for mkt, label in markets:
            if st.button(t(f"载入{label}快照", f"Load {label} snapshot"), key=f"snap_{mkt}", icon=":material/bolt:"):
                with st.spinner(t(f"正在读取全部{label}行情…", f"Fetching every {label} quote…")):
                    st.session_state[f"snap_{mkt}_data"] = cache.snapshot(mkt)
    for mkt, label in markets:
        got = st.session_state.get(f"snap_{mkt}_data")
        if got:
            df, ts = got
            st.download_button(t(f"{label}快照 · {len(df):,} 行 · {ts}（CSV）", f"{label} snapshot · {len(df):,} rows · {ts} (CSV)"),
                               to_csv_bytes(df), file_name=f"{mkt.lower()}_snapshot_{stamp()}.csv", mime="text/csv",
                               on_click="ignore", type="primary", icon=":material/download:", key=f"snap_dl_{mkt}")
    st.divider()
    st.markdown(t("**已保存的抓取**——来自 *立即抓取*、自动抓取（“14:00 盘中”页）和定时脚本的时点文件。",
                  "**Saved captures** - point-in-time files from *Capture now* or auto-capture "
                  "(Intraday at 14:00 page) and the scheduled script."))
    files = capture.list_snapshots()
    if files.empty:
        st.caption(t("还没有抓取。在“14:00 盘中”页使用 **立即抓取**，或设置定时脚本。",
                     "No captures yet. Use **Capture now** on the Intraday at 14:00 page, or schedule the script."))
    else:
        st.dataframe(files[["date", "file", "size_kb", "modified"]], hide_index=True, height=240)
        pick = st.selectbox(t("文件", "File"), files["path"].tolist(),
                            format_func=lambda p: f"{Path(p).parent.name} / {Path(p).name}")
        if pick:
            st.download_button(t("下载所选文件", "Download selected capture"), Path(pick).read_bytes(),
                               file_name=Path(pick).name, mime="text/csv", on_click="ignore", icon=":material/download:")

# ---------------------------------------------------------------------------------------------- CLI
if tab_cli is not None:
    with tab_cli:
        st.markdown(t("""
    超大量下载，或在你睡觉时于北京时间 14:00 抓取数据，请使用 `scripts/` 中的脚本（它们与本程序共用数据层和缓存）。

    **一段日期范围的日线数据**（自选股、主题组、代码或全市场）：
    """, """
    For very large downloads, or to capture data at 14:00 Beijing time while you're asleep in New York,
    use the scripts in `scripts/` (they share this app's data layer and cache).

    **Daily data for a date range** (watchlist, a group, codes, or the whole market):
    """))
        st.code("python scripts/download_daily.py --codes 600519 000858 0700.HK --start 2024-01-01\n"
                "python scripts/download_daily.py --watchlist --start 2025-01-01 --zip\n"
                "python scripts/download_daily.py --all-cn --start 2026-01-01 --out data/exports", language="bash")
        st.markdown(t("**时点抓取**（全市场 + 自选股分钟K线）——现在运行一次：",
                      "**Point-in-time capture** (whole market + watchlist minute bars) - run it once now:"))
        st.code("python scripts/capture_snapshot.py", language="bash")
        st.markdown(t("**用 Windows 任务计划在工作日北京时间 14:00、14:30 和 15:00 收盘时运行。** 首页随后会使用这些时刻的真实数据并立即打开。"
                      "在纽约，夏令时（EDT）为 02:00、02:30 和 03:00，冬令时（EST）为 01:00、01:30 和 02:00；下面的命令按 EDT。"
                      "收盘抓取晚 2 分钟运行，以确保收盘集合竞价已出价。节假日会自动跳过。",
                      "**Schedule it for 14:00, 14:30 and the 15:00 close (Beijing) on weekdays** with Windows Task "
                      "Scheduler. The front page then uses the real values at those minutes and opens instantly. In "
                      "New York that is 02:00, 02:30 and 03:00 during daylight saving (EDT), or 01:00, 01:30 and 02:00 "
                      "in winter (EST); these commands use EDT. The close capture runs 2 minutes late so the "
                      "closing-auction prints are in. Holidays are skipped automatically."))
        script = Path(__file__).resolve().parent.parent / "scripts" / "capture_snapshot.py"
        for name, at in (("1400", "02:00"), ("1430", "02:30"), ("1500", "03:02")):
            st.code(f'schtasks /Create /TN "StockRT {name} capture" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST {at} '
                    f'/TR "\\"{sys.executable}\\" \\"{script}\\" --label {name}"', language="bash")
        st.caption(t("文件保存在 data/snapshots/<日期>/，并显示在“已保存的抓取”中。",
                     "Files land in data/snapshots/<date>/ and appear under Saved captures."))
