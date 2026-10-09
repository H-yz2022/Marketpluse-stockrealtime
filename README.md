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
| **尾盘选股 · Late-session picks** *(front page)* | The late-session query, answered: non-ST, +3–5%, volume ratio > 1, turnover 3–8%, MA5 crossing above MA10, rising MAs, volume and price up, main funds in, beating the CSI 300, float cap ¥5–20B. It shows the latest session **as it stood at 14:00, 14:30 and the 15:00 close** (rebuilt from minute data, or from a real capture when one exists) and gives one ranked list. A checkpoint that hasn't come yet says why and when, e.g. "Not yet - the market closes at 15:00 Beijing (in 41 min)". On a holiday it names the holiday and the reopening day. **Tier** comes first, for reliability (A = every rule at every checkpoint), then a 0–100 **signal score**. A confidence flag and a hindsight "14:00 → close" column sit alongside. The page is **never empty**: the newest saved session shows instantly, and a checkpoint that still needs computing is rebuilt by a background job, then swapped in automatically. **Past sessions** are kept in a collapsed section that loads on demand. Before the open, a **pre-open auction (集合竞价)** panel shows indicative prices and gaps. After the open, **Today's opening auction** (collapsed) offers an overview, **Volume ≥ N× yesterday** (your 5× query, whole market, against the previous session's closing volumes) and a **竞价选股 auction screen**: gap 2–7%, not a limit-up open, auction value ≥ ¥10M, auction volume ≥ 5% of yesterday's, auction turnover ≥ 0.3%, float cap ¥3–50B, plus optional yesterday-up, yesterday limit-up (接力) and gap-holding filters, each with the reason it matters. |
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

Finished sessions are archived as static files in `results/`: `<date>.md` (a readable page that GitHub
renders, listed in [results/README.md](results/README.md)), `<date>.csv` (the ranked list), and `<date>.parquet` +
`.json` (what the app loads, about 150 KB each). `results/cn/volumes/<date>.parquet` holds each session's closing
volumes, which serve as "yesterday" for the opening-auction queries. They are saved automatically from any
whole-market snapshot taken after the close; to backfill a missed session, run
`python scripts/build_volume_book.py --date YYYY-MM-DD`. These files ship with the code. A fresh or restarted free-tier server, whose disk starts empty, therefore opens on real past
sessions immediately. New sessions are archived automatically wherever they are computed. Commit them
(`git add results`) and push to keep the deployed site's history complete.

The site is built to stay small on a free 512 MB instance:

- The front page finds the session from the official calendar and shows a saved result when one exists.
  On holidays, overnight and on revisits it downloads nothing. The whole-market download happens only
  the first time a new checkpoint is computed.
- Other pages, and the chart and analytics code, load only when someone clicks them. Measured: about
  150 MB for the front page alone, about 250 MB after visiting four more pages.
- Caches are capped, and a closed browser tab's session is freed after 60 s.

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

The 52 tests run offline: parsers are checked against recorded real responses, the adjustment
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
  sentiment.py        rule-based sentiment
  etfs.py             ETF scorecard
  sample.py           offline replay of every provider from sample_data/
  export.py, query.py, prompts.py, capture.py, selftest.py
scripts/              download_daily.py, capture_snapshot.py, build_sample.py
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
