# Strategy lab · 策略实验室

Backtest 2025-10-13 to 2026-10-09 (241 sessions, 5,123 stocks, rebuilt from 30-minute bars). Live record from 12 Oct 2026; last updated 2026-11-13 23:17 Beijing.

Exit: **Next close (T+1)**. Excess = return after 0.20% round-trip costs minus the equal-weight market bought at the same moment. Rules were fixed before testing; see [docs/STRATEGY_RESEARCH.md](../../docs/STRATEGY_RESEARCH.md).

## Backtest

| Strategy | Signal | Trades | Days | Avg net | Avg excess | Beat market | t | Random-pick % | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| S1 · 尾盘选股 · Late-session picks | 14:30 | 1523 | 222 | -0.17% | -0.24% | 38% | -1.0 | 28% | Fails |
| S1b · 尾盘选股 14:00 · Late-session picks, half an hour earlier | 14:00 | 1552 | 223 | -0.27% | -0.41% | 36% | -3.7 | 0% | Fails |
| S2 · 竞价选股 · Opening-auction gap | 09:25 (auction) | 29758 | 241 | +0.18% | -0.25% | 41% | -3.6 | 1% | Fails |
| S3 · 高开不破 · Gap holding at 10:00 | 10:00 | 11553 | 241 | +0.10% | -0.24% | 42% | -2.3 | 9% | Fails |
| S4 · 首板 · First limit-up | 14:30 | 11344 | 241 | +1.10% | +1.07% | 52% | 9.6 | 100% | Passes |
| S4b · 首板 · 悲观成交 · First limit-up, pessimistic fill | 14:30 | 242 | 122 | -2.46% | -2.37% | 28% | -6.0 | 0% | Fails |
| S5 · 早盘放量突破 · Morning breakout on volume | 10:30 | 7918 | 241 | -0.28% | -0.34% | 41% | -3.5 | 0% | Fails |
| S6 · 超跌反弹 · Oversold bounce | 14:30 | 11676 | 237 | -0.43% | -0.35% | 40% | -3.0 | 0% | Fails |

## Live record

| Strategy | Signal | Trades | Days | Avg net | Avg excess | Beat market | t | Random-pick % | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| S1 · 尾盘选股 · Late-session picks | 14:30 | 103 | 21 | -1.34% | -0.58% | 35% | -0.9 | 9% | Too early (21/60 days) |
| S1b · 尾盘选股 14:00 · Late-session picks, half an hour earlier | 14:00 | 106 | 21 | -0.83% | -0.19% | 37% | -0.4 | 52% | Too early (21/60 days) |
| S2 · 竞价选股 · Opening-auction gap | 09:25 (auction) | 112 | 24 | -1.74% | -1.58% | 34% | -0.9 | 0% | Too early (24/60 days) |
| S3 · 高开不破 · Gap holding at 10:00 | 10:00 | 1153 | 24 | -0.30% | -0.20% | 45% | -1.6 | 52% | Too early (24/60 days) |
| S4 · 首板 · First limit-up | 14:30 | 1042 | 24 | +1.00% | +1.38% | 54% | 3.1 | 100% | Too early (24/60 days) |
| S4b · 首板 · 悲观成交 · First limit-up, pessimistic fill | 14:30 | 32 | 14 | -3.02% | -2.88% | 25% | -2.9 | 0% | Too early (14/60 days) |
| S5 · 早盘放量突破 · Morning breakout on volume | 10:30 | 392 | 24 | -1.52% | -0.91% | 40% | -1.8 | 0% | Too early (24/60 days) |
| S6 · 超跌反弹 · Oversold bounce | 14:30 | 1490 | 24 | -0.77% | -0.41% | 40% | -2.2 | 0% | Too early (24/60 days) |

Research tooling, not investment advice.
