# A-share real-time strategies: research, test plan and first results

Status: **built** (12 Oct 2026). The plan below was written on 11 Oct, before any result was seen. The website's **Strategy lab · 策略实验室** page runs it: one year of backtest, a live record from 12 Oct 2026, and today's signals in real time. Section 6 has the first results; section 7 lists what changed from the plan, and why.

## 1. What the research says (China A-shares only)

| Finding | Source | What it means for us |
|---|---|---|
| **Overnight returns are negative and intraday returns are positive** in China, the reverse of the US. This is linked to the T+1 rule and to the retail investor base. | Qiao & Dam (2020), *J. Financial Markets* 50, 100534; [PKU seminar abstract](https://econ.pku.edu.cn/xkzy/jrxx/xkdt2/xkdtzs/376755.htm); [Frontiers 2021](https://www.frontiersin.org/articles/10.3389/fenrg.2021.807881/full) | Any "buy late, sell next morning" strategy, including 尾盘选股, starts with a structural headwind. The benchmark has to include it. |
| **Intraday momentum on the CSI 300**: the first half-hour return predicts the last half-hour. Other work says most of this comes from the overnight gap. | [Jin et al., Hull](https://hull-repository.worktribe.com/output/3295550/intraday-time-series-momentum-evidence-from-china); [Hull working paper](https://hull-repository.worktribe.com/OutputFile/3016448) | A market-timing filter. It is tradable only via index futures, because stocks are T+1. |
| **Limit-up "magnet effect"**: large accounts push stocks to the upper limit on day 0 and sell on day 1. Another study finds next-day *continuation* instead. | [Chen, Gao, He, Jiang, Xiong (VoxChina)](https://voxchina.org/show-3-49.html); [Wan et al. 2015, PLOS ONE](https://pmc.ncbi.nlm.nih.gov/articles/PMC4395215) | 首板 / 打板 strategies are contested. They need a test rather than a belief. |
| **Short-term reversal** beats turnover as a fourth factor in China. Abnormal turnover is a sentiment proxy. | [Liu, Stambaugh & Yuan, "Size and Value in China"](https://faculty.wharton.upenn.edu/wp-content/uploads/2018/03/Size-and-Value-in-China.pdf); [Lin et al. 2020, PBFJ](https://ideas.repec.org/a/eee/pacfin/v62y2020ics0927538x2030113x.html) | An oversold-bounce strategy is grounded in the research. Chasing high-turnover winners may work *against* us. |
| **MAX / lottery effect**: stocks with extreme one-day jumps underperform afterwards, strongest after high-sentiment periods. | [Waikato paper](https://researchcommons.waikato.ac.nz/bitstreams/0061c5f2-31b5-461c-ace4-b9fd75bbc060/download); [Bi, Gui & Zhu](https://ideas.repec.org/a/eee/jbfina/v76y2017icp189-197.html) | This is a warning for breakout and limit-up chasing. |
| **短线情绪 indicators** used by practitioners: 炸板率, 连板高度, 连板晋级率, and how yesterday's limit-up stocks trade today. | [财联社 daily reviews](https://www.cls.cn/detail/2423004) | All of these can be computed live from our whole-market snapshot and used as a regime filter. |
| Published practitioner "win rates" for 竞价 / 尾盘 / 首板 strategies have **no stated sample, period or costs**. | [qteasy 竞价 example](https://qteasy.readthedocs.io/zh-cn/latest/examples/Example_03_%E9%9B%86%E5%90%88%E7%AB%9E%E4%BB%B7%E9%80%89%E8%82%A1.html); [同花顺 quant](https://quant.10jqka.com.cn/view/article/41276) | We don't trust any of these numbers. We measure everything ourselves. |
| **Real-time northbound flow data ended in 2024.** | [Caixin](https://www.caixinglobal.com/2024-07-30/china-to-stop-releasing-daily-global-stock-flows-in-mid-august-102221407.html); [HKEX](https://www.hkex.com.hk/News/Market-Communications/2024/2404122news) | "Follow foreign money" strategies are no longer possible in real time. They are excluded. |

## 2. Strategies

Every strategy respects T+1: a stock bought on day *t* can be sold on *t*+1 at the earliest. The universe for
every strategy and for the benchmark is non-ST Shanghai / Shenzhen A-shares with 60+ sessions of history.
Code: `stockrt/strategies.py`.

| # | Strategy | Signal time and rules | Entry | Exits tested |
|---|---|---|---|---|
| S1 | **尾盘选股** (existing) | 14:30: the front-page rules. Main-fund inflow is checked live only (no history) | 14:30 price | T+1 open, T+1 close, T+2 close |
| S1b | **尾盘选股 14:00** | the same rules at 14:00 | 14:00 price | same |
| S2 | **竞价选股** (existing) | 09:25: gap +2–7%, not a limit-up open, float cap ¥3–50B. Live only: auction value ≥ ¥10M, auction volume ≥ 5% of yesterday's, auction turnover ≥ 0.3% | open | same |
| S3 | **高开不破** (auction gap holding) | 10:00: S2's price rules, and the price is still ≥ the open | 10:00 price | same |
| S4 | **首板** (first limit-up) | 14:30: at the limit price, yesterday not limit-up | limit price, assuming the order fills (optimistic) | same |
| S4b | **首板 · 悲观成交** (added after S4's result, see §7) | S4, kept only when the seal breaks before the close, so a 14:30 order certainly fills | limit price | same |
| S5 | **早盘放量突破** (morning breakout) | 10:30: above the 20-session high, volume ratio > 2, change +3–7%, float cap ¥5–50B | 10:30 price | same |
| S6 | **超跌反弹** (oversold bounce, reversal) | 14:30: 5-session return to yesterday's close in the market's bottom 5%, today −7% to −2%, float cap ≥ ¥3B | 14:30 price | same |
| F1 | **Sentiment filter** | Yesterday's limit-up stocks' average move at the signal time (> 0 = warm); 炸板率 and 连板高度 shown alongside | — | Each strategy's excess on warm vs cold days |
| F2 | **Intraday momentum** (diagnostic) | CSI 300: previous close → 10:00 (and open → 10:00) against 14:30 → 15:00 | — | Correlation, slope t-stat, sign agreement |

Execution: an entry at the limit-up price can't be bought, so it is skipped (S1, S1b, S3, S5, S6). S4 buys at
the limit but skips stocks sealed at the limit since the open (一字板).

## 3. Benchmarks: how "working" is judged

1. **Main benchmark: the same-time equal-weight market.** Buy *every* eligible A-share at the same moment and sell at the same exit. Excess return is the strategy minus this. It removes both the market's move and China's built-in overnight effect.
2. **CSI 300 and CSI 1000** over the identical window, timed with their 30-minute bars.
3. **Random-pick placebo.** On each signal day, the same number of random eligible stocks. The share of such pickers the strategy beats is computed exactly from each session's spread of returns (the mean and variance of a random sample). A test checks it against 2,000 simulated draws.
4. **Costs, deducted from every trade (0.202% per round trip):**
   - commission 0.025% per side;
   - stamp duty 0.05% on the sell (since 28 Aug 2023);
   - transfer fee 0.001% per side;
   - slippage 0.05% per side.

   Excess = the trade's return after costs minus the benchmark's return (the benchmark pays no costs: it stands for simply holding the market).
5. **Metrics:** trades and signal days; mean and median net return; mean excess; win rate and the share of trades beating the market; t-statistic of *daily* average excess (picks on one day move together); cumulative excess and its maximum drawdown; monthly excess; three sub-periods.
6. **Pass rule, fixed before seeing any results.** A strategy passes when all of these hold:
   - net excess return > 0 with t ≥ 2, or a placebo percentile ≥ 95%;
   - it beats the market on more than half of its trades;
   - it is positive in at least 2 of 3 sub-periods.

   Backtest verdicts need 20 signal days and 30 trades. Live verdicts need at least 60 signal days (about 3 months).

## 4. Data and its checks

- **Tencent `mkline` 30-minute bars** page backwards with an end time of `YYYYMMDDHHMM` and reach back to **13 Oct 2025** (241 sessions to 9 Oct 2026). Each session has bars ending 10:00 (with the opening auction), 10:30, 11:00, 11:30, 13:30, 14:00, 14:30 and 15:00 (with the closing auction). Beijing-exchange stocks have none.
- **Daily bars** (Tencent, forward-adjusted) give prior closes, moving averages, the 5-session average volume, the 20-session high and float shares (volume ÷ turnover). The two are joined with a per-session offset, so moving averages are not distorted by dividends; returns are raw exit ÷ raw entry, and trades spanning an ex-date or a suspension are dropped.
- **Download:** 5,123 stocks, 1.23 million stock-sessions, about 18 minutes (`python scripts/strategy_lab.py fetch`, kept in `data/lab/`, not in git).
- **Validation** against the front page's archived 1-minute replays at 14:30 (`python scripts/strategy_lab.py validate`): on 8 Oct 2026, 282 stocks compared, identical prices and changes, volume ratio identical to 6 decimals, turnover within 0.003 percentage points, and the same pass list (every rule but main-fund flow). On 30 Sep, 236 stocks, same result.

**Known gaps:** main-fund flow and auction volume have no history (checked live only); ST status and the stock list are today's, so stocks delisted during the year are missing (survivorship bias); limit-up fills (S4) and limit-down exits are assumed possible.

## 5. Live monitoring (built)

- **Today · 实时信号** on the Strategy lab page: each strategy's picks as soon as its signal time passes (the 09:25 auction is read from 09:31), with the move since the signal. It is computed in the background from a shortlist (a few hundred stocks), about a minute.
- **Live record:** after each close, `scripts/daily_update.py` (GitHub Actions, from 15:05 Beijing on weekdays) records every strategy's picks with all rules, including main-fund flow and auction volume. It fills in exit prices as they happen and publishes `lab/` on the repository's `results` branch. Picks are never changed after they are recorded.
- **Scorecards:** backtest and live side by side, for any exit and benchmark, live with all rules or only the backtestable ones (like for like). Static copy: `lab/README.md` on the `results` branch.

## 6. First results: backtest 13 Oct 2025 – 9 Oct 2026

Sell at the next close (T+1), against the equal-weight market, after 0.202% costs:

| Strategy | Trades | Picks / day | Avg return | Avg excess | Beat market | t | vs random | Verdict |
|---|---|---|---|---|---|---|---|---|
| S1 尾盘选股 14:30 | 1,520 | 6.9 | −0.16% | −0.24% | 38% | −1.0 | 29% | Fails |
| S1b 尾盘选股 14:00 | 1,549 | 7.0 | −0.28% | −0.42% | 36% | −3.8 | 0% | Fails |
| S2 竞价选股 (price rules) | 29,714 | 124 | +0.18% | −0.25% | 41% | −3.7 | 1% | Fails |
| S3 高开不破 | 11,539 | 48 | +0.10% | −0.24% | 42% | −2.3 | 9% | Fails |
| S4 首板 (fill assumed) | 11,291 | 47 | +1.10% | +1.06% | 52% | +9.5 | 100% | Passes |
| S4b 首板 (pessimistic fill) | 242 | 2.0 | −2.46% | −2.37% | 28% | −6.0 | 0% | Fails |
| S5 早盘放量突破 | 7,908 | 33 | −0.28% | −0.34% | 41% | −3.4 | 0% | Fails |
| S6 超跌反弹 | 11,548 | 49 | −0.39% | −0.34% | 40% | −3.0 | 0% | Fails |

Other exits tell the same story. At T+1 open every strategy except the two 首板 rows is between −0.13% and −0.60%; at T+2 close
between −0.01% and −0.40% (S4 +1.17%, but under half its trades beat the market, so it fails).

**What it means:**

- **No strategy with a realistic fill beat the market.** Before costs most are close to the market (S1 −0.04%, S2 −0.05%, S3 −0.04%). After 0.2% costs they lose to it. 尾盘选股 at 14:30 is not significantly worse (t −1.0), but there is no sign of an edge either. At 14:00 it is clearly worse.
- **首板's +1.06% is the classic 打板 premium, and it isn't capturable late.** It comes from stocks that stay sealed at the limit, where an order placed at 14:30 sits at the back of the queue. When a 14:30 order does certainly fill (the seal breaks), the trade loses 2.4% against the market (S4b). The real result for a late buyer lies between the two, probably nearer S4b. Only being early in the queue (seconds after the stock hits the limit) could earn it.
- **The overnight effect holds in this year:** the average stock lost 0.049% from close to next open and gained 0.084% from open to close. Late-day strategies that sell next morning fight this.
- **CSI 300 intraday momentum:** the move to 10:00 (including the overnight gap) predicts the last half hour weakly (correlation +0.15, t +2.4). Open → 10:00 alone does not (t +1.1), consistent with the research that the overnight component drives it. Following it averaged +2.3 bp a session before costs, too small to trade.
- **Sentiment filter:** on warm days (yesterday's limit-ups up at the signal time, about 85% of sessions) S1's excess was −0.14% against −0.24% on cold days. The filter is too weak to turn any strategy positive.

These are one year of data and one set of rules: the live record decides. The rules stay fixed.

## 7. Changes from the plan, and why

- **S4b added after S4's result.** S4's fill assumption was flagged as optimistic in the plan. S4b measures the other extreme, so S4's number isn't read as tradable. It is an execution check, not a new signal.
- **Non-ST and "buyable at entry" apply to every strategy.** The plan listed non-ST for some strategies only. A stock at the limit-up can't be bought, so counting it would flatter the result.
- **The placebo is computed exactly** instead of with 1,000 simulated draws: the result is the same, and the live record can update it every day without storing every stock's return.
- **"Warm" means yesterday's limit-ups are up on average at the signal time** (> 0). The plan didn't fix a threshold; 0 was chosen as the natural one before looking at the split.

*Research tooling, not investment advice.*
