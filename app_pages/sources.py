"""Data sources: live health of every upstream endpoint, the field map, and cache controls."""

import shutil
import time

import pandas as pd
import streamlit as st

from stockrt import http, realtime, sample, selftest
from stockrt.calendar import now_bj
from stockrt.config import CACHE_DIR, SNAPSHOT_DIR
from ui.i18n import t

st.markdown(t("A股数据没有官方的免费接口。本程序读取腾讯、新浪和东方财富网页背后的 JSON / 文本接口，换算单位，并在它们之间互为备用。"
              "本页显示 **你的** 网络此刻能访问哪些数据源。",
              "China market data has no official free API. This app reads the JSON / text endpoints behind "
              "Tencent, Sina and East Money's own web pages, converts their units, and falls back between them. "
              "This page shows what is reachable from **your** network right now."))

if sample.is_sample():
    st.info(t("程序正在使用内置示例数据，网络已关闭，自检会把每个接口报告为不可用。请先在侧边栏打开 **实时数据**。",
              "The app is on the built-in sample dataset, so the network is switched off and the self-test "
              "will report every endpoint as unavailable. Turn on **Live data** in the sidebar first."),
            icon=":material/inventory_2:")
with st.container(horizontal=True):
    run = st.button(t("运行自检", "Run self-test"), type="primary", icon=":material/speed:",
                    help=t("向每个接口发送一个小请求（约 5 秒）。", "One small request to each endpoint (≈ 5 seconds)."))
    if st.button(t("恢复暂停的数据源", "Reset paused sources"), icon=":material/refresh:",
                 help=t("连续失败的数据源会暂停几分钟；这里立即恢复。",
                        "Sources that fail repeatedly are paused for a few minutes; this un-pauses them.")):
        http.reset_breakers()
        st.toast(t("已重置熔断器", "Circuit breakers reset"))
if run:
    with st.spinner(t("正在测试每个接口…", "Testing every endpoint…")):
        st.session_state.selftest = (pd.DataFrame(selftest.run_all()), now_bj().strftime("%H:%M:%S"))

if "selftest" in st.session_state:
    df, when = st.session_state.selftest
    ok = int(df["ok"].sum())
    st.markdown(t(f"**自检（北京时间 {when}）** · {len(df)} 个接口中 {ok} 个正常",
                  f"**Self-test at {when} Beijing** · {ok} of {len(df)} endpoints working"))
    df = df.assign(status=df["ok"].map({True: t("✅ 正常", "✅ working"), False: t("❌ 失败", "❌ failing")}))
    st.dataframe(df[["status", "source", "endpoint", "latency_ms", "sample", "used_for", "fallback", "error"]],
                 hide_index=True, column_config={
                     "status": st.column_config.TextColumn(t("状态", "Status"), width="small"),
                     "source": st.column_config.TextColumn(t("来源", "Source")),
                     "endpoint": st.column_config.TextColumn(t("接口", "Endpoint")),
                     "latency_ms": st.column_config.NumberColumn(t("延迟", "Latency"), format="%d ms"),
                     "sample": st.column_config.TextColumn(t("样例", "Sample"), width="medium"),
                     "used_for": st.column_config.TextColumn(t("用途", "Used for")),
                     "fallback": st.column_config.TextColumn(t("失败时", "If it fails")),
                     "error": st.column_config.TextColumn(t("错误", "Error"), width="medium")})
    if not df["ok"].all():
        st.caption(t("东方财富的行情服务器经常断开来自内地以外的连接。使用它们的功能都有备用来源（见最后几列），所以那里出现失败是正常的。",
                     "East Money's quote hosts often drop connections from outside mainland China. Every feature that "
                     "uses them has a fallback (see the last columns), so failing rows there are expected."))

st.markdown(t("**数据源实时状态** · 程序启动以来的所有请求", "**Live host health** · every request the app has made since it started"))
h = http.health()
if not h:
    st.caption(t("还没有请求。", "No requests yet."))
else:
    now = time.time()
    st.dataframe(pd.DataFrame([{
        t("服务器", "host"): x.host, t("成功", "requests ok"): x.ok, t("失败", "failed"): x.failed,
        t("平均延迟（毫秒）", "avg latency (ms)"): round(x.avg_latency_ms) if x.avg_latency_ms else None,
        t("最近成功", "last success"): (t(f"{now - x.last_ok:,.0f} 秒前", f"{now - x.last_ok:,.0f} s ago") if x.last_ok
                                       else t("从未", "never")),
        t("状态", "state"): (t(f"暂停 {x.paused_until - now:,.0f} 秒", f"paused {x.paused_until - now:,.0f} s")
                             if x.paused_until > now else t("正常", "active")),
        t("最近错误", "last error"): x.last_error or "",
    } for x in h]), hide_index=True)

st.markdown(t("**每个字段的来源**", "**Where each field comes from**"))
FIELDS = [
    (("实时价格、涨跌、开高低收、成交量、成交额", "Real-time price, change, OHLC, volume, value"), "Tencent qt.gtimg.cn", "-",
     ("每次请求约 300 只；全部A股约 3 秒", "~300 symbols per request; full A-share market in ~3 s")),
    (("换手率、量比、流通市值", "Turnover rate, volume ratio (量比), float cap"), "Tencent qt.gtimg.cn",
     ("新浪列表（无量比）", "Sina list (no volume ratio)"),
     ("A股成交量手 ×100 → 股，科创板除外（已是股）", "A-share lots ×100 → shares, except STAR Market (already shares)")),
    (("主动买 / 卖量（外盘 / 内盘）", "Active buy / sell volume (外盘 / 内盘)"), "Tencent qt.gtimg.cn", "-",
     ("仅内地", "Mainland only")),
    (("1 分钟序列（今日、最近 5 个交易日）", "1-minute series (today, last 5 sessions)"), "Tencent minute/query, day/query",
     "-", ("累计成交量 → 每分钟", "Cumulative volume → per-minute")),
    (("分钟K线", "Minute OHLC bars"), "Tencent kline/mkline", ("1 分钟序列", "1-minute series"),
     ("仅内地；800 根", "Mainland only; 800 bars")),
    (("含成交额和换手率的日线", "Daily bars incl. amount & turnover"), "Tencent newfqkline", ("磁盘缓存", "disk cache"),
     ("每次请求 2,000 根；长历史分页读取", "2,000 bars per request; paged for long histories")),
    (("复权因子", "Adjustment factors"), "Sina qfq.js / hfq.js", ("腾讯日线中的分红文本", "dividend text in Tencent bars"),
     ("用比例法重建（数据商发布的是加法复权）", "Rebuilt with the ratio method (providers publish additive)")),
    (("主力资金净流入", "Main-fund net inflow (主力)"), "East Money ulist (batch)", ("新浪逐只", "Sina per stock"),
     ("大单 + 特大单", "Large + extra-large orders")),
    (("股票列表（全部上市代码）", "Stock universe (all listed codes)"), "Sina Market_Center", ("缓存列表", "cached list"),
     ("港股每页 60 行且总数偏少——分页读到空为止", "HK pages hold 60 rows and the count is understated - paged until empty")),
    (("ETF 费率 / 净资产", "ETF expense ratio / net assets"), "East Money fund pages", ("缓存 7 天", "cached 7 days"),
     ("港股ETF：在 stockrt/etfs.py 中维护", "HK ETFs: maintained in stockrt/etfs.py")),
    (("指数市盈率（滚动）", "Index P/E (trailing)"), "Tencent index quote", "-", ("仅内地指数", "Mainland indices only")),
    (("公告及链接", "Announcements + links"), "East Money np-anotice-stock", "-",
     ("标题 → 东方财富页面；PDF → pdf.dfcfw.com", "Title → East Money page; PDF → pdf.dfcfw.com")),
]
st.dataframe(pd.DataFrame([[t(*f), src, fb if isinstance(fb, str) else t(*fb), t(*nt)] for f, src, fb, nt in FIELDS],
                          columns=[t("字段", "Field"), t("主要来源", "Primary source"), t("备用", "Fallback"),
                                   t("说明", "Notes")]), hide_index=True)

st.markdown(t("**存储**", "**Storage**"))


def folder_size(path) -> tuple[int, float]:
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files) / 1e6


with st.container(horizontal=True):
    n_cache, mb_cache = folder_size(CACHE_DIR)
    n_snap, mb_snap = folder_size(SNAPSHOT_DIR)
    st.metric(t("缓存文件", "Cache files"), f"{n_cache:,}", f"{mb_cache:,.1f} MB", delta_color="off", border=True)
    st.metric(t("已保存的抓取", "Saved captures"), f"{n_snap:,}", f"{mb_snap:,.1f} MB", delta_color="off", border=True)
with st.container(horizontal=True):
    if st.button(t("清除内存缓存", "Clear in-memory caches"), icon=":material/refresh:",
                 help=t("让每个页面重新读取（行情、快照、历史）。", "Forces every page to refetch (quotes, snapshots, history).")):
        st.cache_data.clear()
        st.toast(t("缓存已清除", "Caches cleared"))
    if st.button(t("刷新股票列表", "Refresh stock lists"), icon=":material/sync:",
                 help=t("重新下载全部A股和港股代码（约 20 秒）。", "Re-download every A-share and HK code (≈ 20 s).")):
        with st.spinner(t("正在下载股票列表…", "Downloading stock lists…")):
            n_cn = len(realtime.universe("CN", refresh=True))
            n_hk = len(realtime.universe("HK", refresh=True))
        st.cache_data.clear()
        st.toast(t(f"{n_cn:,} 只A股，{n_hk:,} 只港股", f"{n_cn:,} A-shares, {n_hk:,} HK stocks"))
    if st.button(t("删除缓存的日线历史", "Delete cached daily history"), icon=":material/delete:",
                 help=t("下次使用时重新下载日线。已保存的抓取不受影响。",
                        "Daily bars are re-downloaded on next use. Saved captures are not touched.")):
        shutil.rmtree(CACHE_DIR / "daily", ignore_errors=True)
        st.cache_data.clear()
        st.toast(t("日线缓存已删除", "Daily cache deleted"))
st.caption(t(f"缓存：{CACHE_DIR} · 抓取：{SNAPSHOT_DIR}", f"Cache: {CACHE_DIR} · Captures: {SNAPSHOT_DIR}"))

from stockrt import archive, published  # noqa: E402

pub = published.use_current()
man = published.manifest()
st.markdown(t("**已发布的结果** · 每次收盘后由每日更新（GitHub Actions）计算一次",
              "**Published results** · computed once after each close by the daily update (GitHub Actions)"))
sessions = ", ".join(man.get("sessions", {}).get("CN", [])) or "—"
st.caption(t(f"仓库：{published.repo() or '—'} · 分支 `{published.BRANCH}` · 使用：{pub.get('source')} · "
             f"发布于 {man.get('updated', '—')} · 交易日 {sessions}" + (f" · 上次检查：{pub['error']}" if pub.get("error") else "")
             + f" · 文件夹：{archive.RESULTS_DIR}",
             f"Repository: {published.repo() or '—'} · branch `{published.BRANCH}` · in use: {pub.get('source')} · "
             f"published {man.get('updated', '—')} · sessions {sessions}"
             + (f" · last check: {pub['error']}" if pub.get("error") else "") + f" · folder: {archive.RESULTS_DIR}"))
from stockrt import storage  # noqa: E402
from stockrt.config import DATA_DIR  # noqa: E402

sizes = [(t("已发布结果", "published results"), archive.RESULTS_DIR), (t("缓存", "caches"), CACHE_DIR),
         (t("任务日志", "job log"), DATA_DIR / "jobs")]
ages = ", ".join(t(f"{k} {v} 天", f"{k} {v} days") for k, v in storage.MAX_AGE_DAYS.items())
st.caption(t("本服务器上的存储：", "Stored on this server: ")
           + " · ".join(f"{name} {storage.size(path) / 1e6:.1f} MB" for name, path in sizes)
           + t(f"。历史交易日保留最近 {archive.KEEP_HISTORY} 个；缓存文件过期时间：{ages}。",
               f". Past sessions keep the newest {archive.KEEP_HISTORY}; cached files expire after {ages}."))
job_log = archive.RESULTS_DIR / "job_log.json"
if job_log.exists():
    import json

    runs = json.loads(job_log.read_text(encoding="utf-8"))
    with st.expander(t(f"每日更新记录（{len(runs)} 次，最新在前）", f"Daily update runs ({len(runs)}, newest first)"),
                     icon=":material/history:"):
        for r in runs[:6]:
            bad = [x for x in r["steps"] if not x["ok"]]
            status = t("全部步骤正常", "all steps ok") if not bad else \
                f":red[{t(f'{len(bad)} 步失败', f'{len(bad)} failed')}: {', '.join(x['step'] for x in bad)}]"
            st.markdown(t(f"**北京时间 {r['at']}** · ", f"**{r['at']} Beijing** · ") + status)
            for x in r["steps"]:
                st.caption(f"{'✓' if x['ok'] else '✗'} {x['step']} ({x['seconds']}s): "
                           f"{x.get('result') or x.get('error', '')}")

if not sample.is_sample():
    from ui.picks_view import job_runner

    st.markdown(t("**网站后台任务日志** · 本服务器上的每次后台运行，最新在前",
                  "**Site job log** · every background run on this server, newest first"))
    entries = job_runner().history(60)
    starts = [e["at"] for e in entries if e.get("event") == "server_start"]
    st.caption((t(f"本服务器最近一次启动于北京时间 {starts[0]}，日志从那时开始（免费服务器重启时磁盘会被清空——主机日志保留同样的内容，"
                  "每行以 `[job]` 开头：在 Streamlit Community Cloud 的 **Manage app** 中）。",
                  f"This server last started at {starts[0]} Beijing; the log begins there (a free server's disk is "
                  "wiped when it restarts - the host's log keeps the same lines, each starting `[job]`: on Streamlit "
                  "Community Cloud under **Manage app**). ") if starts else "")
               + t("GitHub 任务自己的日志在上面的“每日更新记录”中。", "The GitHub job's own log is under *Daily update runs* above."))
    runs = [e for e in entries if e.get("task")]
    if runs:
        st.dataframe(pd.DataFrame(runs).reindex(columns=["at", "task", "mkt", "day", "state", "seconds", "attempt",
                                                         "error", "result", "where"]),
                     hide_index=True, column_config={
                         "at": st.column_config.TextColumn(t("完成时间（北京）", "Finished (Beijing)")),
                         "task": st.column_config.TextColumn(t("任务", "Job")),
                         "mkt": st.column_config.TextColumn(t("市场", "Market")),
                         "day": st.column_config.TextColumn(t("交易日", "Session")),
                         "state": st.column_config.TextColumn(t("结果", "Result")),
                         "seconds": st.column_config.NumberColumn(t("秒", "Seconds")),
                         "attempt": st.column_config.NumberColumn(t("第几次", "Attempt")),
                         "error": st.column_config.TextColumn(t("错误", "Error"), width="large"),
                         "result": st.column_config.TextColumn(t("输出", "Outcome")),
                         "where": st.column_config.TextColumn(t("位置", "Where"))})
    else:
        st.caption(t("本服务器启动以来还没有完成的后台任务。", "No background job has finished since this server started."))
