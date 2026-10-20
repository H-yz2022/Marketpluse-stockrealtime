# China & HK real-time markets (prototype)

A real-time data platform for **China A-shares and Hong Kong stocks**, built around the
afternoon workflow: at about 14:00 Beijing time, see the morning session plus the first part
of the afternoon, screen the whole market, and download the data to analyse yourself.
It is a companion to MarketPulse Copilot (US markets).

**No AI runs inside the app.** The *AI prompt builder* produces paste-ready prompts that carry
the live data and exact column definitions, for use with whichever assistant you like.

## Pages

| Page | What it does |
|---|---|
| **尾盘选股 · Late-session picks** *(front page)* | The late-session query, answered: non-ST, +3–5%, volume ratio > 1, turnover 3–8%, MA5 crossing above MA10, rising MAs, volume and price up, main funds in, beating the CSI 300, float cap ¥5–20B. It shows the latest session **as it stood at 14:00, 14:30 and the 15:00 close** (rebuilt from minute data, or from a real capture when one exists) and gives one ranked list. A checkpoint that hasn't come yet says why and when, e.g. "Not yet - the market closes at 15:00 Beijing (in 41 min)". On a holiday it names the holiday and the reopening day. **Tier** comes first, for reliability (A = every rule at every checkpoint), then a 0–100 **signal score**. A confidence flag and a hindsight "14:00 → close" column sit alongside. The page is **never empty**: the newest saved session shows instantly, and a checkpoint that still needs computing is rebuilt by a background job, then swapped in automatically. **Past sessions** are kept in a collapsed section that loads on demand. Before the open, a **pre-open auction (集合竞价)** panel shows indicative prices and gaps. After the open, **Opening auction & volume queries** (collapsed; on weekends and holidays it shows the latest session) offers an overview, **Volume ≥ N× yesterday** (your 5× query, whole market, against the previous session's closing volumes) and a **竞价选股 auction screen**: gap 2–7%, not a limit-up open, auction value ≥ ¥10M, auction volume ≥ 5% of yesterday's, auction turnover ≥ 0.3%, float cap ¥3–50B, plus optional yesterday-up, yesterday limit-up (接力) and gap-holding filters, each with the reason it matters. Below the ranked list, **收盘选股 · after-close screen (16:00)**: 请列出成交量翻倍，突破压力位，换手率＞3%，站上牛熊线的非ST股票 - volume at least 2× the previous session's, close above the previous 20 sessions' high (or 60), turnover above 3%, close above the 250-day MA (年线; or the 120-day), non-ST. It is computed once a day from 16:00 Beijing on the closing data and shown as fixed figures, with the last 4 sessions selectable. Above it, **主力暗中吸筹 · quiet accumulation (9:45 – 11:15)**: 昨日成交量大于前4日成交量, and today 主力资金流向环比增长率突增 with −1% < 涨幅 < 1% at 9:45, 10:15, 10:45 and 11:15 (main-fund inflow from each stock's minute money flow, compared with the previous checkpoint; thresholds editable in the conditions table). Below it, **长线 · 收盘数据，给明天建仓做参考**: 市盈率 < 20, ROE > 5% (trailing, P/B ÷ P/E), 5 < 股价 < 10, 换手率 > 4%, institutions holding the stock, 10-day main-fund inflow, and MA5/10/20 converged on one of the last 3 sessions - computed after the close, every threshold editable in its table. At the very bottom, the **Strategy lab · 策略实验室**, which loads only when opened (see below). |
| ↳ **Strategy lab · 策略实验室** *(last section of the front page)* | Eight A-share strategy variants with rules fixed before testing: 尾盘选股 at 14:30 and 14:00, 竞价选股 (opening gap), 高开不破 (gap holding at 10:00), 首板 (first limit-up, with an optimistic and a pessimistic fill), 早盘放量突破 (morning breakout) and 超跌反弹 (oversold bounce). Each is judged by its **excess return after costs over buying every stock at the same moment** (equal-weight market; CSI 300 and CSI 1000 also shown), its t-statistic, how many random pickers it beats, and its consistency over time. One year of **backtest** rebuilt from 30-minute bars, a **live record** from 12 Oct 2026 written after each close, and **today's signals in real time** as each signal time passes. **Try your own parameters · 自定义参数**: pick a strategy, move its thresholds (change %, volume ratio, turnover, float cap, MA cross, gap, fill model, oversold percentile…) and the whole year re-scores instantly against the official rules, from a compact candidate pool per strategy (`research/lab_pools/`, built with `python scripts/strategy_lab.py pools`). Also: China's overnight-vs-daytime return, CSI 300 intraday momentum and 短线情绪 (yesterday's limit-ups, 炸板率). Static copy: `lab/README.md` on the repository's `results` branch; method: [docs/STRATEGY_RESEARCH.md](docs/STRATEGY_RESEARCH.md). |
| **Live dashboard** | One stock: latest price, daily / 1-month / period return, volatility, max drawdown, P/E, rule-based sentiment. Intraday chart with VWAP and the lunch break removed, session statistics (morning vs afternoon, VWAP, volume ratio, turnover, main-fund flow, active buying), daily chart, and announcements with links to the page and PDF. Auto-refreshes every 30 s while trading. |
| **Intraday at 14:00** | Watchlist statistics and an overlay chart up to a cut-off (11:30 / 13:30 / 14:00 / 14:30 / close). One-click **capture of the whole market** at that moment, plus auto-capture at chosen Beijing times while the app runs. |
| **尾盘选股 · Hong Kong (live test)** | The same screen run live on Hong Kong stocks at **15:00, 15:30 and the 16:00 close** (the same last-hour pattern; HK closes an hour later), against the Hang Seng Index, with market value in HKD. It exercises the whole pipeline on live data and doubles as a pre-test before the A-share checkpoints. A switch offers **HK-adapted turnover (0.5–8%)**, because HK turnover rates are far below mainland ones. HK main-fund flow comes only from East Money; when that is unreachable the rule is shown as unchecked (confidence Low). |
| **Screener & query** | Rule-based screen of all ~5,500 A-shares. The default preset is the late-session momentum screen (non-ST, +3–5%, volume ratio > 1, turnover 3–8%, MA5 crossing above MA10, rising MAs, volume and price up, main-fund inflow, beating the CSI 300, float cap ¥5–20B). Shows a funnel, near misses and market mood, and has a **SQL tab** for querying the live snapshot. |
| **Compare & watchlist** | Returns over 1M / 3M / 6M / YTD / 1Y / 3Y / 5Y / Max; volatility, Sharpe, Sortino, max drawdown, VaR / CVaR, beta, correlation, alpha, capture ratios; rebased chart, risk/return scatter, correlation heatmap, yearly returns, drawdowns. A single-company view splits **market-driven vs company-specific** risk. |
| **Index ETFs** | 14 mainland and HK index ETFs: tracks, period return, volatility, Sharpe, max drawdown, VaR 95%, beta, correlation, expense ratio, fund assets, P/E, dividend yield. |
| **Data downloads** | Daily data for **any date range** (watchlist, theme groups, pasted codes, screen results, or the whole market) as long CSV, a close-price matrix or a ZIP with a README. Also 1-minute data up to a cut-off (latest or last 5 sessions), minute OHLC bars, full-market snapshots and saved captures. |
| **AI prompt builder** | Seven templates filled with live data (screen review, your screen in plain words, 14:00 review, comparison, stock brief, market mood, any question plus any dataset), in English or 中文, plus a saved prompt library. |
| **Data sources** | Live self-test of every endpoint, per-host health, the field-to-source map, and cache controls. |

## Quick start

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

```bash
streamlit run streamlit_app.py
```

The app opens at http://localhost:8501 with **live data**. The first load downloads the stock lists (~20 s, then cached).

## Built-in sample data (offline mode)

`sample_data/` (~16 MB) is a fixed snapshot of one trading session, built with `scripts/build_sample.py`. It holds:

- every A-share and HK quote, with main-fund flow;
- 10 years of daily bars for the 57 starter symbols, ETFs and indices, and ~60 sessions for every other A-share;
- 5 sessions of 1-minute data, plus adjustment factors, ETF fees and announcements.

In sample mode every page works with **no network at all**, and the front page's 14:00 / 14:30
result is stored precomputed (`picks.parquet`), so it opens instantly. The site opens instantly, scrapes
nothing and stays small in memory, which suits a free hosting tier. Turn on **Live data** in the
sidebar for real-time prices, or start the app in sample mode:

```bash
STOCKRT_DATA_MODE=sample streamlit run streamlit_app.py
```

(On Windows PowerShell: `$env:STOCKRT_DATA_MODE="sample"; streamlit run streamlit_app.py`.) To refresh
the sample with a newer session, run `python scripts/build_sample.py` (~6 minutes).

## Deploy (free)

Push to GitHub, then on [Render](https://render.com) choose **New → Blueprint** and pick the repo.
`render.yaml` starts the app on the free plan with **live data**, so the front page is real time while
the market trades.

**A trading day runs by itself** (Beijing time):

| When | What happens | Who does it |
|---|---|---|
| Before 09:15 | The front page and the Strategy lab show the last session's saved results | website, nothing computed |
| 09:15–09:30 | Pre-open auction (集合竞价): indicative prices, gaps and the auction screen, live | website |
| 09:30–15:00 | Real time: the auction and volume queries, the Strategy lab's signals as each signal time passes, the 尾盘选股 checkpoints at 14:00 and 14:30 | website, computed in the background |
| 15:05 (16:15 for Hong Kong) | The session is computed **once**: front page at 14:00 / 14:30 / 15:00, volume book, Strategy lab live record | GitHub Actions (`.github/workflows/daily.yml`) |
| 9:45 – 11:15 | 主力暗中吸筹 at each checkpoint; fixed after 11:15 | website |
| From 15:05 | 长线 on the closing data, computed once | website (and GitHub Actions) |
| From 16:00 | 收盘选股, the after-close screen (成交量翻倍，突破压力位，换手率＞3%，站上牛熊线), computed once | website (and GitHub Actions) |
| After that | Fixed figures, shown instantly; nothing is recomputed | website |
| Weekends and holidays | The last session's saved results | website |

**Where the results live.** The daily job publishes `results/` to the repository's **`results` branch** as a
single commit that replaces the previous one. The repository's history doesn't grow, and `main` (the code) is
never touched. The website reads that branch through `stockrt/published.py`: it checks the branch's
`manifest.json` at most once a minute and downloads it when it changes. A free-tier server that restarts
with an empty disk is back on the published results in a second, without redeploying. Only the newest **4
sessions** (A-shares and Hong Kong) and **5 volume books** are kept; the daily job drops older ones. The
Strategy lab's live log is kept whole: it is the test itself, and grows by a few MB a year. Readable
copies: `README.md`, `cn/<date>.md` and `lab/README.md` on the `results` branch.

**If GitHub is late.** The job runs at 15:05, 15:40, 16:02, 16:15 and 17:30 Beijing, because GitHub sometimes skips or delays a
scheduled run. Whichever run comes first does the work; the others find it done and finish in a minute. In practice GitHub has been starting them
5–8 hours late, so the website doesn't wait for it: it has its own clock (`ui/scheduler.py`) that computes the
morning screen after 11:15, the close at 15:03, 长线 at 15:06, 收盘选股 at 16:01 and Hong Kong at 16:12 whenever
the server is awake, and a visitor who arrives first triggers the same computation (with a progress card and a
countdown). The job's late run then publishes the same figures durably. A free Render server sleeps after 15
idle minutes; to keep it awake for the clock, point a free uptime monitor (for example UptimeRobot, every 10
minutes) at the site's address - optional, no account details needed by the site. Each run writes
`job_log.json` (what every step did, including errors) next to the results; the **Data sources** page shows it. Missed sessions
are caught up: any of the last 4 sessions missing from the front page is rebuilt from the 5-session minute
series. A session rebuilt with Sina's money flow (East Money refused) is redone automatically once East Money
answers again. By hand, if ever needed: the **Actions** tab → *Daily update* → *Run workflow*, or
`python scripts/daily_update.py` (`--backfill YYYY-MM-DD` rebuilds one session).

The site is built to stay small on a free 512 MB instance:

- The front page finds the session from the official calendar and shows a saved result when one exists.
  On holidays, overnight and on revisits it downloads nothing. The whole-market download happens only
  the first time a new checkpoint is computed.
- Other pages, and the chart and analytics code, load only when someone clicks them. Measured: about
  150 MB for the front page alone, about 250 MB after visiting four more pages.
- Caches are capped, and a closed browser tab's session is freed after 60 s.

The daily job needs permission to push its branch: Settings → Actions → General → Workflow permissions →
*Read and write* (if your account defaults to read-only).

`STOCKRT_WORKERS` sets how many requests run in parallel (24 on Render, where each request to the mainland
sources is slow). Set `STOCKRT_DATA_MODE=sample` for a fully offline demo, and `STOCKRT_LOCK_MODE=1` to hide the
live/sample switch from visitors. Streamlit Community Cloud also works: point it at
`streamlit_app.py` and add `STOCKRT_DATA_MODE = "sample"` under its secrets / environment settings.

## Data, in one paragraph

Real-time quotes, minute series and daily bars come from Tencent. Stock lists, adjustment factors
and money flow come from Sina. East Money supplies fund fees and announcements, plus batch money
flow when it is reachable. Every unit is normalised (lots → shares, 10k → units), long-run prices
are **ratio-adjusted** (total return, not the providers' additive method), and failing sources
fall back automatically. The full map is in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Command line

```bash
python scripts/download_daily.py --watchlist --start 2025-01-01 --zip
```

```bash
python scripts/download_daily.py --all-cn --start 2026-01-01
```

```bash
python scripts/capture_snapshot.py
```

```bash
python scripts/strategy_lab.py fetch
```

```bash
python scripts/strategy_lab.py backtest
```

`strategy_lab.py fetch` downloads one year of 30-minute and daily bars for every Shanghai / Shenzhen stock
(~20 minutes, kept in `data/lab/`); `backtest` scores every strategy and writes `results/lab/`; `books` writes
end-of-day volume books for recent sessions; `validate --date YYYY-MM-DD` compares the 14:30 rebuild with an
archived front-page session; `update` records new live sessions.

`capture_snapshot.py` saves every A-share quote (with main-fund flow), every HK quote and the
watchlist's 1-minute bars to `data/snapshots/<date>/`. From 14:00 onwards it also pre-computes the
front page from those real values, so the site opens instantly. It skips weekends and holidays
(official calendars) unless you pass `--force`. Schedule it for **14:00, 14:30 and the 15:00 close (Beijing)** on
weekdays with Windows Task Scheduler. From New York that is 02:00 / 02:30 / 03:00 EDT, or
01:00 / 01:30 / 02:00 EST. The close capture runs at :02 with `--label 1500`, so the closing
auction has printed. The exact commands are on the *Data downloads → Bulk & scheduled* tab.

## Tests

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

```bash
python -m ruff check .
```

The tests run offline: parsers are checked against recorded real responses, the adjustment
math against known ex-dividend days, and every page is rendered against the sample dataset.
GitHub Actions runs them on each push.

## Layout

```
streamlit_app.py      navigation, Beijing clock, settings
app_pages/            one file per page
ui/                   Streamlit caching, formatting, Altair charts
stockrt/              UI-agnostic data layer (reusable behind FastAPI later)
  sources/            tencent.py, sina.py, eastmoney.py - raw endpoints and units
  http.py             retries, circuit breaker, health
  adjust.py           ratio-based price adjustment
  history.py          cached daily bars + adjustment + live bar
  realtime.py         quotes, whole-market snapshot, money flow, breadth
  intraday.py         minute data and session statistics
  analytics.py        returns and risk metrics
  screener.py         rule engine and presets
  labdata.py          Strategy lab: per-stock sessions at each signal time, from 30-minute bars
  strategies.py       the lab's strategies (fixed rules) and short-term sentiment
  lab.py              backtest, live record, scorecards, today's signals
  sentiment.py        rule-based sentiment
  etfs.py             ETF scorecard
  sample.py           offline replay of every provider from sample_data/
  export.py, query.py, prompts.py, capture.py, selftest.py
scripts/              download_daily.py, capture_snapshot.py, build_sample.py, strategy_lab.py,
                      daily_update.py, build_volume_book.py
sample_data/          built-in offline dataset (see sample_data/README.md)
tests/                offline tests + recorded fixtures
docs/DATA_SOURCES.md  endpoint map, quirks, verification
```

## Known limits (prototype)

- **Market hours** come from the official SZSE calendar and the HK government holiday list (bundled
  copy for offline use). Unscheduled closures, such as a typhoon before the open, show up only in
  the data.
- **HK quotes** from these free sources may be delayed (often 15 minutes) for non-subscribers,
  and HK money flow depends on East Money being reachable.
- **HK ETF expense ratios and HK index P/E** have no free real-time source. Fill expense ratios in
  `stockrt/etfs.py` (`HK_EXPENSE_RATIOS`).
- **Sentiment** is rule-based: order flow, main funds, relative strength, close location and
  announcement keywords. Every component is shown.
- These endpoints are unofficial and can change without notice; the Data sources page shows when
  one breaks.

*Research tooling, not investment advice.* Code under the [MIT License](LICENSE). Market data in
`sample_data/` comes from Tencent, Sina and East Money public endpoints, belongs to them, and is included only
as a small demonstration sample.
