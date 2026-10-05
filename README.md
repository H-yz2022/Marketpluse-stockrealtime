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
| **尾盘选股 · Picks at 14:00 / 14:30** *(front page)* | The late-session query, answered: non-ST, +3–5%, volume ratio > 1, turnover 3–8%, MA5 crossing above MA10, rising MAs, volume and price up, main funds in, beating the CSI 300, float cap ¥5–20B. It replays the latest session **as it stood at 14:00 and 14:30** (rebuilt from minute data, or from a real capture when one exists) and gives one ranked list. **Tier** comes first, for reliability (A = every rule at both times), then a 0–100 **signal score** (trend, volume, main funds, intraday pattern, relative strength, liquidity). A confidence flag and a hindsight "→ close" column sit alongside. |
| **Live dashboard** | One stock: latest price, daily / 1-month / period return, volatility, max drawdown, P/E, rule-based sentiment. Intraday chart with VWAP and the lunch break removed, session statistics (morning vs afternoon, VWAP, volume ratio, turnover, main-fund flow, active buying), daily chart, and announcements with links to the page and PDF. Auto-refreshes every 30 s while trading. |
| **Intraday at 14:00** | Watchlist statistics and an overlay chart up to a cut-off (11:30 / 13:30 / 14:00 / 14:30 / close). One-click **capture of the whole market** at that moment, plus auto-capture at chosen Beijing times while the app runs. |
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
`render.yaml` starts the app on the free plan in sample mode. Set `STOCKRT_LOCK_MODE=1` to stop
visitors switching it to live. Streamlit Community Cloud also works: point it at
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
(official calendars) unless you pass `--force`. Schedule it for **14:00 and 14:30 Beijing** on
weekdays (from New York that is 02:00 / 02:30 EDT, or 01:00 / 01:30 EST) with Windows Task
Scheduler. The exact commands are on the *Data downloads → Bulk & scheduled* tab.

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
