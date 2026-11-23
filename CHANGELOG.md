# Change log · 更新记录

Every change to the website and the desktop app, newest first (from the project's history; regenerate with `python scripts/changelog.py`).

## 2026-11-23

- **Past sessions on every front-page screen: the last 20, older ones shrunk**
  - 主力暗中吸筹, 低位吸筹, 长线, 资金选股 and 热点题材 each get a session picker. Each keeps 20 sessions (about four weeks): the last week in full, older ones shrunk once to what their page shows (the stocks that met or nearly met the conditions, plus the closest others) at single precision. 资金选股 keeps the stocks with a 3- or 5-day inflow and records the session's leading themes before shrinking. On today's results the five screens take about 8 MB; only the session opened is loaded.
- **资金选股 and 热点题材: two money-flow screens, updated daily; concept members weekly**
  - 资金选股 · basic (5-day main-fund net inflow, no ST, above MA20) with the advanced conditions on top (3-day main-fund inflow, large-order net share above 5%, free float CNY 5-30 bn, up less than 20% over 20 sessions). 热点题材 · in a chosen concept (AI 算力 by default), 3-day main-fund inflow, volume up 50%+ over 3 sessions, above MA20; quick picks for AI 算力 and 机器人, today's leading themes, and the list of companies in the chosen concepts.
  - Money flow, volume, price and market value change every session, so both screens are recomputed after each close (from 15:08, also in the daily GitHub job). Concept membership changes slowly: East Money's concept map (436 themes, about 46,000 company-theme pairs, index / holder / style lists left out) is downloaded once a week from its data centre in about 11 seconds. Every threshold can be edited or switched off on the page.
- **Language: first chosen from the visitor's computer; switch at the top right of every page**
  - Without a language in the address, the first one follows the browser's (or Windows') language, then the time zone (China, Hong Kong, Macau, Taiwan: 中文; elsewhere: English); nothing is looked up online. The desktop app does the same on its first start and then remembers the choice. The 中文 / English switch moved from the sidebar to the top right of every page, where it is seen first, also on phones.

## 2026-11-22

- **低位吸筹: new condition, change today below 5%**
  - A stock already up 5% or more today has had its rebound; it no longer qualifies. Like the other conditions it can be changed or switched off on the page, and it applies to saved results at once.
- **Desktop app: quiet updates, and the evening start asks what to do**
  - Updates no longer close and reopen the window or pop up a "Reload now?" question: a newer launcher or program is downloaded in the background and used from the next start.
  - Started by the evening schedule (ChinaHKMarkets.exe --scheduled), the app first shows a small window saying what it is, without taking the keyboard: open the app, run in the background (minimised, still computing), or not tonight. With no answer in 60 seconds it runs in the background.
  - The window says where to change or turn off the evening start (Task Scheduler).
- **Pre-open auction: show the index's opening gap from 09:25**
  - The CSI 300 tile stayed blank until 09:30 because the check looked for a bid price, which indices never have. It now waits only until the index has traded (09:25 match).
- **Quicker background jobs; stock list refreshed in the background; fresher pre-open auction**
  - One worker process now stays running and takes jobs one at a time, so a job no longer loads the code (~4.5 s) or opens new connections to the data sources (1.5-4 s each from the US). Measured on the 收盘选股 job for 20 Nov: 22-28 s per run before, 11-15 s after the first. A fresh worker replaces it after 25 jobs, if it dies, or if a job runs past 15 minutes.
  - The list of listed stocks is refreshed in the background every 12 hours; the stale list is used meanwhile. Before, the refresh added ~7 s to whichever market snapshot found it out of date.
  - Pre-open auction panel: its snapshot is kept 15 s instead of 60 s, so the 20-second refresh shows new prices (it showed the same ones three times in four).
  - The desktop program's worker takes its jobs over pipes; the build's startup test now checks two jobs run in one process.

## 2026-11-18

- **低位吸筹 money flow: keep East Money's answers, send only the gaps to Sina**
  - Money flow is fetched from East Money in batches of 100, six at a time; a refused batch no longer sends every stock to Sina, only the stocks East Money didn't answer for.
  - The Sina fallback for 低位吸筹 now uses the job's 24 parallel requests instead of 8.
  - On 19 Nov 2026, with East Money refusing all requests from abroad, the whole screen took 78 s (188-222 s earlier that session).
  - The job log and the screen's notes now say which source answered, e.g. "money flow: eastmoney 3,700 · sina 77".

## 2026-11-17

- **Change log: CHANGELOG.md lists every change, newest first**
  - scripts/changelog.py rebuilds it from the git history (each commit's title and details, grouped by day); README links to it.

## 2026-11-16

- **Faster live screens: 130-session history, weekly report cache, results computed ahead**
  - History panel: 130 sessions instead of 30 (enough for MA120), prices as float32: 12.8 MB for 5,571 stocks. 低位吸筹 reads its moving averages from it instead of downloading 200 days per stock.
  - 低位吸筹 shortlist in one pass over the panel (16 s -> 3 s; identical on all 5,559 stocks).
  - Company reports (扣非净利润) kept for the week and fetched six requests at a time (75 s -> 0-30 s). Measured on 17 Nov 2026, 2,061 stocks: 193 s -> 57 s, then 30 s later in the week.
  - The clock computes live screens ahead while the server runs: 主力暗中吸筹 after 9:45/10:15/10:45, 尾盘选股 at 14:01 and 14:31, 低位吸筹 every half hour. On time only, never caught up, not listed in the sidebar.
- **Larger, clearer text; text-size setting; light / dark theme in the app**
  - Default text 16 px (was 14) on the site and in the app; Settings -> 字号 offers 标准 / 大 / 特大 (16 / 18 / 20 px), kept in the address (fs) and remembered by the app.
  - Grey help text has more contrast.
  - Desktop app: Settings -> 外观 (follow Windows / light / dark). The launcher reloads the window with Streamlit's embed_options when the choice changes, and remembers it; Ctrl + / Ctrl - zoom everything. The website keeps Streamlit's own theme setting (top-right menu).
  - A missing or odd language value falls back to Chinese instead of breaking the page.
- **Added Dev Container Folder**
- **Desktop app runs on its own: the program on the user's PC, no website**
  - The launcher (ChinaHKMarkets.exe) no longer shows the hosted site. It downloads the program (ChinaHKMarketsCore.zip: the whole site packaged with PyInstaller, 84 MB) once, runs it as a local server only that PC can reach, and shows it in its window. Data comes straight from the market sources; results are computed and kept in %LOCALAPPDATA%\ChinaHKMarkets.
  - desktop/core.py: --server / --worker / --selftest; exits with its parent; system fonts.
  - desktop/app.py: first-run download with progress, SHA-256 checks, background program updates with an offer to reload, launcher self-update, one instance, log file, exits when the window closes (the old launcher lingered).
  - desktop/build.py + workflow: build, start and check the program before publishing.
  - stockrt: packaged worker mode, results folder from STOCKRT_RESULTS_DIR, rolling-window pruning on the desktop; downloads page drops the command-line tab in the app.
  - Checked locally: all pages in the packaged program, first run, second start, clean shutdown.

## 2026-11-15

- **Tool pages in Chinese too: Screener, Intraday, Live dashboard, Compare, ETFs, downloads, prompts, sources**
  - Every page now follows the language switch (中文 by default). Shared quote-table columns, index and theme-group names, board names, market mood, sentiment labels, the screener's funnel steps and notes are translated in ui/i18n.py and ui/fmt.quote_columns(). Widgets whose options were English labels now use fixed keys with translated captions. The AI prompt builder keeps its own prompt language, defaulting to the interface language. Self-test endpoint names and raw error messages stay as they are.
- **中文 by default, English on request; desktop app updates itself; data stops growing**
  - Language (ui/i18n.py, ui/lab_zh.py): one language at a time, Chinese unless ?lang=en or the sidebar switch says otherwise. The front page, its six views, the sidebar, the clock and the progress card are translated, including text the data layer writes in English (market status, session notes, rule names, run notes, strategy rules and verdicts). Conditions tables keep fixed column names, so switching language keeps the values entered.
  - Desktop app: opens the site's inner page in embed mode (no Fork / GitHub / Streamlit badge) with app=desktop, which hides website-only parts (download link, sample-data switch); shows Streamlit's wake-up page when the site sleeps, then switches; remembers the language; updates itself from the release's version.json (SHA-256 checked, any failure keeps the current version); caps its browser cache at 32 MB. The workflow publishes version.json with each build.
  - Website: a link to the latest Windows app on the front page.
  - Data: Past sessions keep 60 sessions (about 12 weeks) instead of 250; server caches expire (per-day results after 7 days, daily bars after 30, job progress files after a day), checked once a day; Data sources shows what is stored.
- **Desktop app: fix the freeze; open at half the screen**
  - The window stopped responding about 16 s after it opened (CPU idle: a deadlock). The retry button's api object held the window in a public attribute, and pywebview walks the api object's public attributes from the window's own thread. The window is now a private attribute.
  - The window opens at once on a 'connecting' page; the connection is checked in the background (before, nothing showed for about 12 s).
  - Half the screen's width and 85% of its height, centred (640 x 680 on a 1280 x 800 screen).
  - Checked on the built exe: window after ~4 s, responding at every check for 90 s, site loaded.
- **Windows desktop app: the site in its own window**
  - desktop/app.py opens https://china-hk-markets.streamlit.app/ through Windows' built-in Edge engine (pywebview): app icon and title, taskbar and Start menu, no browser tabs. Downloads (CSV) work, outside links open in the browser, cookies persist between runs, and without a connection it shows a retry screen. Any HTTP reply counts as online (Streamlit's cookie redirect loops without a jar).
  - Built with PyInstaller as one 10 MB exe. The "Desktop app" workflow rebuilds it on GitHub when desktop/ changes and attaches it to the repository's "desktop" release.
- **低位吸筹 · Buy the dip, avoid the falling knife: new front-page screen**
  - 近5个交易日跌幅超过10%，今日主力资金净流入超过3000万元，现价站上60日均线，近12个月扣非净利润为正。
  - Shortlist from data on disk (history panel, volume books): a 5-session fall of 5%+, non-ST, trading today. Then adjusted daily bars (exact fall, MA20/60/120), East Money's main-fund flow in one batch (Sina fallback), and 扣非净利润 TTM from East Money F10 reports (latest YTD + last annual - same period a year earlier), cached per day.
  - Live during the session (recomputed when opened, at most every 15 min), fixed after the close: the clock runs it at 15:03 (deadline 15:30), the GitHub job publishes it, 5 sessions kept.
  - Every threshold editable on the page; the closest stocks shown when nothing passes.
  - Worker: retry the progress-file swap that Windows refuses while it is being read.
  - Fri 13 Nov 2026: 722 stocks checked in 83 s, none met all four (8 met three).

## 2026-11-13

- **Ready for Streamlit Community Cloud: no settings needed**
  - Render's free hours ran out (750 a month, shared by every free service in the account), so the site moves to Streamlit Community Cloud: free, more memory and CPU, and it sleeps only after 12 hours without visitors.
  - Defaults cover what render.yaml used to set: 24 parallel requests, and this repository for the published results when the host gives no variable and no .git folder.
  - data/ falls back to the system temp folder if the app's own folder isn't writable.
  - README: deploy steps for Community Cloud; Render kept as the paid fallback.
  - Checked on a clean copy with no .git and no settings: results downloaded, worker ran, no errors.

## 2026-11-03

- **Visitors go ahead of the clock's catch-up work; keep a worker's error on Windows**
  - Render's logs (2 Nov) show no crash: the free server slept after 15 idle minutes and, on each of three wake-ups, the clock recomputed the whole unpublished day (about 11 minutes, 7 for 1,804 stocks in 主力暗中吸筹) while visitors waited in the queue behind it.
  - A page's request goes to the front of the queue, and a clock job a visitor asks for moves up too.
  - Re-read the progress file briefly after the worker exits, so its error isn't lost to a read that landed during the file swap (seen as "worker exited with code 1" on Windows).

## 2026-10-30

- **Jobs no longer restart on failure; no-result days are saved; Past sessions by week, kept for a year**
  - The GitHub job and the pages treated "no stock passed the first screen" as a crash (Hong Kong, 30 Oct): the job reported failure and an open page resubmitted every 18 s, restarting from zero and hiding the reason. Any real network error took the same path.
  - No stock qualifying is a saved result. A failed job keeps its reason on the page and retries after 1, 3 and 10 minutes.
  - 尾盘选股 saves each batch, so a retry continues where it stopped. Stocks that don't answer get a second try; over 3% missing fails the run instead of ranking part of the market.
  - Time left comes from measured speed or this server's real run times; the card shows queue position and says when a job is slower than usual.
  - Site job log on Data sources and in Render's Logs tab. Fix a race that lost a worker's error.
  - Past sessions: browse by week and Mon-Fri. Newest 5 sessions in full, older ones compacted, up to 250 sessions. 31 sessions back to 30 Sep recovered from earlier publications.
  - A restarted server fetches published results before showing or computing anything, so it never shows the old copy or recomputes what is already published.
- **Site stays up: heavy jobs in a worker process; clock with deadlines; faster screens**
  - Render restarted the site when its health check took over 5 s, which happened while screens were computed inside the web server (1-10 s measured). Every heavy job now runs in a separate low-priority process, one at a time, with progress and results on disk; the web server only reads files (health 2-4 ms locally while a job runs).
  - The site's clock queues each daily result at its time, shows waiting / running / done / late / failed in the sidebar, and retries a missing one every 10 minutes.
  - The front page shows one screen at a time through a selector.
  - 30-session history panel and a pre-check cut 尾盘选股 from ~356 to ~131 requests.
  - 主力暗中吸筹 reads each stock's minute money flow once, with lighter parsing.
  - Catch-up looks back only over the kept sessions (no more rebuilding 23 Oct each run).

## 2026-10-20

- **主力暗中吸筹 and 长线 screens; results on time after the close; faster live updates**
  - 主力暗中吸筹 replaces the 4-day volume section: yesterday's volume above the 4 sessions before it, then at 9:45 / 10:15 / 10:45 / 11:15 a main-fund inflow surge vs the previous checkpoint with -1% < change < 1% (Sina minute flow, one request per stock); thresholds editable in the conditions table
  - 长线 (after the close): P/E < 20, ROE > 5% (P/B / P/E), 5 < price < 10, turnover > 4%, institutions holding, 10-day main inflow, MA5/10/20 converged within 3 sessions; every threshold editable and switchable in its table
  - The site stops waiting for the GitHub job (it starts 5-8 h late): a site clock (ui/scheduler.py) computes the morning screen, the close, 长线 and 收盘选股 on time while awake, and visitors trigger the same at once
  - History panel (30 sessions, every stock, published daily) replaces the per-stock history downloads in the front-page rebuild: half the requests, identical values
  - One bilingual progress card with a countdown for every background job

## 2026-10-18

- **Strategy lab moves to the bottom of the front page; your own parameters; 4-day volume section**
  - Strategy lab is the last section of the A-share front page, loaded only when opened and run as a fragment; each view loads only what it needs, and the scorecards are precomputed (scorecards.parquet) instead of re-scoring ~80k trades on every open. The separate page is gone.
  - Try your own parameters (自定义参数): every strategy's thresholds on sliders, re-scored instantly from compact candidate pools (research/lab_pools/, 9.5 MB, built by `strategy_lab.py pools`), next to the official result, with a data-mining warning; defaults reproduce the official picks
  - 成交量大于前4日: its own front-page section, the latest close from the saved volume books (instant, fixed) or today so far while trading

## 2026-10-15

- **收盘选股: after-close screen on the front page, computed daily from 16:00**
  - 请列出成交量翻倍，突破压力位，换手率＞3%，站上牛熊线的非ST股票: volume >= 2x the previous session, close above the previous 20 (or 60) sessions' high, turnover > 3%, close above MA250 (or MA120), non-ST.
  - stockrt/closescreen.py: shortlist from the end-of-day volume book, then forward-adjusted daily history for resistance and moving averages
  - Daily job computes it from 16:00 Beijing (extra 16:02 run), catches up any recent session without a list, keeps the newest 4 (cn/screens/)
  - Front page section with fixed figures, session picker and the two readings switchable; the site computes it itself if nothing is published by 17:10
  - job_log.json records every daily-update step (shown on Data sources); an empty front-page rebuild now counts as a failure instead of passing silently

## 2026-10-12

- **Automatic daily cycle: fixed figures after the close, rolling 4-session window**
  - Daily job publishes results to the `results` branch as one commit that replaces the previous one (no history growth; main is never touched); the website reads it (stockrt/published.py) instead of recomputing per visit
  - Job runs at 15:05, 15:40, 16:15, 17:30 Beijing; every step is idempotent
  - After the close the front page and the Strategy lab wait for the published session (computing themselves only if it is over an hour late)
  - Keep the newest 4 sessions and 5 volume books; manifest.json
  - Catch-up redoes sessions rebuilt with Sina's flow once East Money answers
  - Download retries failed stocks and refuses to record a session from less than 97% of the market (a partial download skewed one run)
  - Mon 12 Oct saved: front page (A-shares, HK), volume book, Strategy lab day 1

## 2026-10-11

- **Backfill Fri 9 Oct 2026 into past sessions**
  - Rebuilt from the 5-session minute series and the 9 Oct volume book; main-fund flow from Sina's daily history (East Money's refused this machine), noted on the page.
- **Backfill missed front-page sessions; daily job catches up automatically**
  - checkpoint.backfill(day): rebuild a finished A-share session after the feed has moved on, from the 5-session minute series, that day's volume book and the daily main-fund flow history (East Money, Sina as a labelled fallback)
  - checkpoint.catch_up(): the daily job rebuilds any of the last 4 sessions missing from Past sessions (building the volume book if needed)
  - daily_update.py --backfill YYYY-MM-DD for a one-off rebuild
- **Strategy lab: 8 A-share strategies backtested and tracked live; 4-day volume query**
  - Strategy lab page (策略实验室): rules fixed before testing, judged against the same-moment equal-weight market (plus CSI 300 / CSI 1000) after 0.2% costs, with t-stat, random-pick placebo and sub-period checks
  - One-year backtest rebuilt from Tencent 30-minute bars (validated against the front page's 1-minute replays), live record from 12 Oct 2026, today's signals in real time
  - Daily GitHub Actions job: volume book, front-page archive, live record
  - Front page: 成交量大于前4日 query; auction/volume queries open on the latest session (weekends too); volume books for the last 30 sessions

## 2026-10-08

- **Opening-auction research (volume surge + 竞价选股 screen), static past sessions**
  - "Today's opening auction · 集合竞价" (collapsed, loaded on demand) with three views: overview; "Volume ≥ N× yesterday" across the whole market (default 5x, min value, exclude ST); and a 竞价选股 auction screen - gap 2-7%, not a limit-up open, auction value >= 10M, auction volume >= 5% of yesterday's, auction turnover >= 0.3%, float cap 3-50 bn, optional yesterday-up / yesterday limit-up (接力) / gap-holding - each condition explained
  - Auction data model measured live on 9 Oct: indicative price = best bid = best ask with best-bid volume as matched volume (09:15-09:25), matched open/volume 09:25-09:30, the 09:30 minute bar afterwards (read for the shortlist only, cached per day); index gap shown as "—" before 09:25
  - End-of-day volume book (results/cn/volumes/<date>.parquet) saved automatically after the close; 8 Oct backfilled (5,556 stocks) with scripts/build_volume_book.py
  - Past sessions list every archived session (incl. the one shown on top); each session also kept as a static page (<date>.md, renders on GitHub) and CSV, indexed in results/README.md
  - Quote parser: best bid/ask volume; 75 tests
- **Front page never empty: saved sessions instantly, live updates in the background, past sessions, pre-open auction**
  - Found on the live Render site: the first rebuild of a session took ~10 min there (every request to the mainland sources is slow from Render's US servers; the self-test from Render shows 10/11 sources working) and the page showed nothing meanwhile.
  - Results archive (results/<mkt>/<date>): finished sessions ship with the code, so a fresh or restarted free-tier server opens on real sessions at once; seeded with A-shares 30 Sep + 8 Oct and HK 8 Oct
  - The front page shows the newest saved result instantly; a checkpoint not yet computed is rebuilt by a background job (one at a time, low memory) with a self-refreshing progress line and swapped in when done; failures keep the saved result on screen with a warning
  - "Past sessions" section, collapsed and loaded only when opened
  - Pre-open auction (集合竞价) panel before 09:30: indicative prices, gaps, breadth, watchlist and biggest movers; after the open, the auction result in a collapsed section
  - Cold start uses the bundled stock list; STOCKRT_WORKERS (24 on Render); self-test time shown in Beijing time
  - 70 tests
- **尾盘选股 Hong Kong page: the same screen live on HK stocks**
  - Checkpoint engine is market-aware (MarketSpec): A-shares 14:00 / 14:30 / 15:00 vs CSI 300; Hong Kong 15:00 / 15:30 / 16:00 vs Hang Seng Index, HKD
  - New page "尾盘选股 · Hong Kong (live test)" sharing one view with the A-share front page; switch for HK-adapted turnover (0.5-8%) because HK turnover runs far below mainland levels
  - HK closing auction (prints ~16:08) now belongs to the session, so the 16:00 checkpoint is the official close (verified on 8 Oct: 411.40, volume identical); HK checkpoints always rebuilt from minute data (free HK quotes lag ~15 min)
  - Missing money-flow source -> rule shown as unchecked, confidence Low, instead of failing every stock; live screener pacing per market
  - Verified live on 8 Oct 2026: A-shares 先惠技术 met all 10 rules at 14:00, 14:30 and 15:00 (tier A); HK pipeline rebuilt all three checkpoints
  - 65 tests

## 2026-10-06

- **尾盘选股: add the 15:00 close checkpoint, explain pending ones, lighter front page**
  - Checkpoints 14:00, 14:30 and 15:00 (the close; shown from 15:01 after the closing auction prints, end-of-day flow counts as the checkpoint value). Each checkpoint card says "Shown" or why not yet ("the market closes at 15:00 Beijing (in 41 min)"); on holidays the page names the holiday and the reopening day and shows the latest session (from the official calendar)
  - Tiers generalised to any number of checkpoints (A = every rule at every checkpoint); per-checkpoint ✓ columns; hindsight measured from 14:00
  - Front page light for a free 512 MB instance: session found from the calendar, saved / bundled results shown without downloading the market; whole results cached; other pages and chart/analytics code load only on click (measured ~150 MB front page vs ~250 MB after four more pages); smaller caches; closed sessions freed after 60 s
  - Website (render.yaml) now runs on live data so it is real time while the market trades; sample mode stays available
  - 15:00 capture (runs 15:02, saved as 1500) in auto-capture and scheduler
  - 62 tests

## 2026-10-05

- **Official trading calendars, instant 尾盘选股 front page**
  - Market status from the exchanges' official calendars: SZSE trading-day API (all mainland exchanges) and HK government public holidays (HKEX), incl. HK half days; bundled copy for offline/sample use. The sidebar now says e.g. "Closed · National Day holiday · reopens Thu 08 Oct" and flags delayed quotes (free HK feed ~15 min behind). Clock arithmetic split from the calendar-aware status so it never touches the network
  - Front page titled 尾盘选股; sample mode ships the 14:00/14:30 result precomputed (opens in ~0.2 s after start-up) with a plain explanation and no live-only buttons; live results cached as soon as a checkpoint passes, and the 14:00/14:30 capture pre-computes them from real captured values
  - Scheduler guidance for both 14:00 and 14:30 captures
  - 59 tests (calendar tests run offline against the bundled calendar)

## 2026-10-04

- **Front page: late-session picks replayed at 14:00 and 14:30, ranked**
  - New default page answering the 10-rule late-session query for the latest session as it stood at 14:00 and 14:30 (dashboard moved lower in the menu)
  - stockrt/checkpoint.py rebuilds each candidate from minute data: volume ratio, turnover, float cap, MA cross with the checkpoint price, volume pace, beat-the-index; validated against end-of-day quotes (volume ratio and turnover within ~1%). End-of-day pre-filter keeps it to ~240 stocks; finished sessions cached; uses real captures when present
  - Ranking: reliability tier (A = all rules at both times ... D = one rule narrowly missed) then a transparent 0-100 signal score; confidence flag and hindsight move to the close; CSV, rule matrix and AI prompt
  - Resilience: Tencent minute/query began returning HTTP 501 - fall back to day/query; circuit breaker now per endpoint instead of per host; front page never crashes on a source failure; sidebar detects market holidays
  - Sample data includes the replay candidates' minutes (works offline)
  - 57 tests
- **Add built-in sample dataset (offline mode), MIT license and Render config**
  - sample_data/ (~16 MB): one session of quotes for every A-share and HK stock with main-fund flow, 10 years of daily bars for the starter universe, ~60 sessions for every other A-share, 5 sessions of minutes, factors, ETF fees and announcements; rebuilt by scripts/build_sample.py
  - Sample mode replays every provider from those files with the network off: instant start, nothing scraped, small memory footprint for free hosting. Sidebar "Live data" switch; STOCKRT_DATA_MODE / STOCKRT_LOCK_MODE
  - render.yaml for a free Render deploy (defaults to sample mode)
  - Smaller in-memory caches to fit a 512 MB instance
  - requirements-dev.txt split from runtime requirements
  - Tests: network blocked in sample mode, offline screener, and every page rendered against the sample (52 tests)
- **China A-share & Hong Kong real-time market platform (prototype)**
  - Streamlit app plus a UI-agnostic data layer (stockrt/) for mainland China and Hong Kong equities, built around the 14:00 Beijing pre-close workflow.
  - Live dashboard, intraday-at-14:00 view, whole-market rule screener with SQL, compare & watchlist, index ETF scorecard, data downloads, AI prompt builder (no in-app AI), and a data-source health page
  - Tencent / Sina / East Money endpoints with unit normalisation, retries, per-host circuit breaker and fallbacks (see docs/DATA_SOURCES.md)
  - Ratio-based (total-return) price adjustment, verified on known ex-dates
  - CLI scripts for scheduled market capture and bulk daily downloads
  - 48 offline tests with recorded fixtures; CI runs ruff + pytest
