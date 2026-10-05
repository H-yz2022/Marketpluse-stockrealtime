# Data sources: the endpoint map

There is no official free API for mainland China or Hong Kong market data. Tencent, Sina and
East Money serve their own web pages from undocumented JSON/text endpoints. This prototype reads
those endpoints directly, without AkShare or another wrapper, so every unit conversion and
fallback is visible and testable. Everything below was verified against live responses in
October 2026. Use the **Data sources** page (or `stockrt.selftest.run_all()`) to see what is
reachable from your network today.

## Strategy

| Role | Provider | Why |
|---|---|---|
| Real-time backbone | **Tencent** (`gtimg.cn`) | Fast and lenient: about 300 symbols per quote request, the whole A-share market in ~3 s. Reachable from outside China. |
| Universe, factors, money flow | **Sina** (`sina.com.cn`) | Complete stock lists, ratio adjustment factors, money flow by order size. Needs a `Referer` header and bans aggressive clients. |
| Enhancers | **East Money** | Has the richest fields, but its `push2*` quote hosts reset connections or return 502/302 from outside mainland China. Used only where a fallback exists. |

All requests go through `stockrt/http.py`. It provides per-thread pooled sessions, retries with
backoff, a **per-endpoint circuit breaker** (a failing endpoint is paused instead of stalling every
page, and healthy endpoints on the same host keep working), and live health stats. The optional East Money hosts are called in fast-fail mode: no retries,
and a 10-minute pause after the first failure.

## Endpoints

### Tencent

| Data | URL | Notes |
|---|---|---|
| Real-time quotes | `https://qt.gtimg.cn/q=sh600519,sz000858,hk00700,...` | GBK text, one `v_<code>="f0~f1~..."` record per symbol, `~`-separated. Unknown symbols return `v_pv_none_match`. 250 symbols per call is safe. |
| Today's minutes | `https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh600519` | `"HHMM price cum_volume cum_amount"`. Volume and amount are **cumulative**, so take differences. Includes 15:05–15:30 after-hours prints (`session = post`). On 2026-10-05 this endpoint started returning **HTTP 501 for every symbol** while `day/query` kept working. The adapter then falls back to the latest day of `day/query`, which holds the same data. |
| Last 5 sessions | `https://web.ifzq.gtimg.cn/appstock/app/day/query?code=hk00700` | Same row format, one block per day, with the previous close (`prec`). Works for HK too. |
| Daily bars | `https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param=<code>,day,<start>,<end>,2000,<fq>` | Row: `[date, open, close, high, low, volume, {events}, turnover%, amount(10k)]`. Up to **2,000 bars per call**, being the *last* 2,000 in the range, so page backwards for long histories. `fq` = `''` raw, `qfq`, `hfq`. Event dicts carry dividend text such as `10派280.242元` or `中期息0.19港元`. |
| Minute OHLC | `https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=<code>,m1,,800` | `m1/m5/m15/m30/m60`, 800 bars max. Mainland only (HK returns nothing). Note the host is `ifzq.gtimg.cn`, since `web.ifzq` returns 301 here. |

**Quote field map** (index in the `~`-split record):

| Field | A-share / ETF / index | Hong Kong |
|---|---|---|
| name, code, price, prev close, open | 1, 2, 3, 4, 5 | same |
| volume | 6 (**100-share lots**, but **shares for STAR Market 688/689**) | 6 (shares) |
| active buy / sell volume (外盘 / 内盘) | 7 / 8 | n/a |
| bid1 / ask1 and depth | 9–18 / 19–28 | 9 / 19 |
| time | 30 `YYYYMMDDHHMMSS` | 30 `YYYY/MM/DD HH:MM:SS` |
| change, change % | 31, 32 | 31, 32 |
| high, low | 33, 34 | 33, 34 |
| amount | 37 (**10k CNY**) | 37 (HKD; **10k HKD for indices**, with volume repeated in field 6) |
| turnover rate % | 38 | 59 |
| P/E (TTM) | 39 (index P/E for indices) | 39 |
| amplitude % | 43 | 43 |
| float / total market cap | 44 / 45 (**100M**) | 44 / 45 (100M HKD) |
| P/B | 46 | 58 |
| limit up / down | 47 / 48 (−1 for indices) | n/a |
| volume ratio (量比) | 49 | 50 |
| VWAP | 51 | 73 |
| dividend yield % (TTM) | 64 | 47 |
| 52-week high / low | 67 / 68 | 48 / 49 |
| float / total shares | 72 / 73 | 69 / 70 |
| security type | 61 (`GP-A`, `ETF`, `ZS`) | 63 (`GP`, `GP-FUND`, `ZS`) |

Volume units were verified by checking that amount ÷ VWAP equals shares (see `tests/test_parsers.py`).
Tencent writes `0` for "not applicable" valuation fields, and the parser turns those into blanks.

### Sina

| Data | URL | Notes |
|---|---|---|
| A-share list | `.../json_v2.php/Market_Center.getHQNodeData?page=N&num=100&sort=symbol&asc=1&node=hs_a` | 5,571 stocks (SH + SZ + BJ). Count: `getHQNodeStockCount?node=hs_a`. Prices in CNY, caps in 10k CNY. |
| HK list | `.../Market_Center.getHKStockData?page=N&num=60&node=qbgg_hk` | **Silently caps pages at 60 rows**, and the advertised count (~2,500) is low. Page until a page comes back empty (~2,800 stocks). 8xxxx RMB-counter duplicates are dropped. |
| A-share factors | `https://finance.sina.com.cn/realstock/company/sh600519/qfq.js` | `{d, f}`: adjusted = raw ÷ f from date d (multiplicative). Funds: `{d, f, s, u}`: adjusted = (raw − u) ÷ s (additive cash). |
| HK factors | `https://finance.sina.com.cn/stock/hkstock/00700/hfq.js` | `{d, f, c}`: adjusted = raw × f + c (split factor + cumulative cash). HK ETFs return 404, so use Tencent dividend text. |
| Money flow, one stock | `.../MoneyFlow.ssi_ssfx_flzjtj?daima=sh600519` | `r0..r3` in/out = extra-large / large / medium / small orders. Main funds (主力) = r0 + r1 net. |
| Money flow, whole market | `.../MoneyFlow.ssl_bkzj_ssggzj?page=N&num=100&sort=r0_net` | About 65 pages (stocks + funds). Used by the scheduled capture. |

All Sina calls need `Referer: https://finance.sina.com.cn`.

### East Money

| Data | URL | Status from outside China |
|---|---|---|
| Batch quotes and money flow | `https://82.push2.eastmoney.com/api/qt/ulist.np/get?secids=1.600519,116.00700&fields=f62,f184,...` | Intermittent. `f62` = main net inflow, `f184` = its % of turnover. Covers HK. |
| Intraday money flow | `.../api/qt/stock/fflow/kline/get?klt=1&secid=...` | Intermittent |
| Whole-market list | `.../api/qt/clist/get?fs=m:0+t:6,...` | Mostly blocked (connection reset / 502), so it is **not used** |
| Daily klines | `https://push2his.eastmoney.com/api/qt/stock/kline/get` | Mostly blocked, so it is **not used** (Tencent instead) |
| Fund fees | `https://fundf10.eastmoney.com/jjfl_510300.html` | Works. 管理费率 / 托管费率 / 销售服务费率 parsed from HTML. |
| Fund profile | `https://fundmobapi.eastmoney.com/FundMApi/FundBaseTypeInformation.ashx?FCODE=510300&...` | Works (mainland funds only) |
| Announcements | `https://np-anotice-stock.eastmoney.com/api/security/ann?stock_list=600519&ann_type=A` | Works. `ann_type=H` for HK. The page link is `data.eastmoney.com/notices/detail/<code>/<art_code>.html` and the PDF is `pdf.dfcfw.com/pdf/H2_<art_code>_1.pdf`. |

Symbol ids ("secid"): `1.` Shanghai, `0.` Shenzhen/Beijing, `116.` HK stocks, `100.` HK indices.

### Trading calendars

| Market | Source | Notes |
|---|---|---|
| Mainland (SSE, SZSE, BSE) | `https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=2026-10` | The Shenzhen Stock Exchange's own calendar. `jybz` = 1 for a trading day, 0 for closed. All mainland exchanges share it. Next year's months appear once published (usually December). |
| Hong Kong | `https://www.1823.gov.hk/common/ical/en.ics` | HK government public holidays (iCalendar), on which HKEX closes. HKEX trades **morning only** on Christmas Eve, New Year's Eve and Lunar New Year's Eve. |

The sidebar, auto-refresh and volume pacing all use these, so a holiday reads "Closed · National Day
holiday · reopens Thu 08 Oct" rather than being guessed from the clock. A copy for the current and
next year ships with the app (`stockrt/trading_calendar.json`), so it also works offline and in
sample mode. Rebuild the copy with `python -c "from stockrt import tradingdays; tradingdays.refresh_bundle((2026, 2027))"`.

HK quotes from these free feeds run about **15 minutes behind** during trading (measured: HSI quote
at 13:33 at 13:48). The sidebar shows the lag whenever it is 5 minutes or more.

## Price adjustment: why the app computes its own

The providers' forward-adjusted (前复权) series are **additive**: they subtract dividends as fixed
amounts. Over long histories this distorts returns, and Tencent's forward-adjusted Kweichow Moutai
is **negative before 2018**. `stockrt/adjust.py` rebuilds a ratio (total-return) series instead:

1. Daily return on an ex-date = close ÷ reference price − 1, where reference price =
   (previous close − cash) ÷ (1 + bonus shares). This is the exchange's own ex-rights price.
2. Compound those returns backwards from today's real price.

Any provider scheme that is affine per segment (adjusted = a × raw + b) yields the exact return
r = (adj_t − b_t) ÷ (adj_{t−1} − b_t) − 1, so Sina's three formats feed the same code.

Verified against independent figures:

| Case | Expected | Computed |
|---|---|---|
| Moutai ex-date 2026-06-26 (¥28.0242 cash) | −1.3045% | −1.3045% |
| Tencent ex-date 2026-05-15 (HK$5.30 cash) | +0.3297% | +0.3297% |
| Tencent 1:5 split 2014-05-15 | 108.8 × 5 ÷ 514 − 1 | identical |
| CSI 300 ETF ex-date 2026-01-19 (¥0.123) | +0.0422% | +0.0422% |
| CSI 300 ETF 1-year return vs East Money NAV return | −4.16% (NAV) | −4.09% (price) |
| Moutai earliest adjusted close (2001) | positive | ¥4.00 (Tencent additive: negative) |
| Bulk path (dividend text) vs Sina factors, Moutai daily returns | n/a | max difference 0.005% |

## Rebuilding 14:00 / 14:30 after the fact (front page)

Quote feeds show only end-of-day values after the close, so `stockrt/checkpoint.py` rebuilds each
stock at a checkpoint from its 1-minute series and daily history:

- volume ratio (量比) = (volume so far ÷ minutes elapsed) ÷ (5-session average volume ÷ 240);
- turnover = volume so far ÷ float shares;
- MA5 / MA10 use the prior sessions plus the checkpoint price.

Checked by rebuilding 40 random stocks "at 15:00" and comparing with the real end-of-day quote:

| Field | Mean error | Max error |
|---|---|---|
| change % | 0.002 points | 0.005 points |
| volume ratio | 0.3% | 1.0% |
| turnover | 0.2% | 1.1% |
| float market cap | 0.01% | 0.03% |

Minute data is fetched only for stocks that end-of-day facts can't rule out (the day's range must
touch the 3–5% band, and end-of-day turnover must already exceed the minimum, because cumulative
turnover only grows). That is about 240 of 5,500 stocks, or roughly 40 s for the first run; finished
sessions are then cached. Main-fund flow at the checkpoint needs East Money's minute flow. When that
is unreachable, the session's end-of-day flow is used and the row is marked confidence "Medium". A
capture taken at that minute (`scripts/capture_snapshot.py` / auto-capture) replaces all of this
with the real values.

## Fallback chain

| Feature | Primary | Fallback |
|---|---|---|
| Main-fund flow | East Money batch | Sina per stock (mainland only) |
| Adjustment factors | Sina (cached 12 h) | stale cache, then Tencent dividend text |
| Stock lists | Sina (cached 12 h on disk) | stale list |
| Daily bars | Tencent, cached on disk and topped up every 60 s while trading | cached bars |
| Today's minutes | Tencent `minute/query` | latest day of Tencent `day/query` |
| Index move at a checkpoint | CSI 300 minute data | rule reported as unchecked |
| ETF fees | East Money (cached 7 days) | stale cache, or blank |
| Trading days / holidays | official calendars (below), refreshed daily | copy bundled in `stockrt/trading_calendar.json` |
