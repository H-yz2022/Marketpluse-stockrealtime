# Strategy lab · 策略实验室

Backtest 2025-10-13 to 2026-10-09 (241 sessions, 5,123 stocks, rebuilt from 30-minute bars). Live record from 12 Oct 2026; last updated 2026-10-23 20:34 Beijing.

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
| S1 · 尾盘选股 · Late-session picks | 14:30 | 26 | 7 | -1.84% | -0.31% | 46% | -0.6 | 42% | Too early (7/60 days) |
| S1b · 尾盘选股 14:00 · Late-session picks, half an hour earlier | 14:00 | 27 | 8 | -0.87% | -0.01% | 41% | -0.4 | 64% | Too early (8/60 days) |
| S2 · 竞价选股 · Opening-auction gap | 09:25 (auction) | 41 | 9 | -1.52% | -0.53% | 44% | 0.4 | 29% | Too early (9/60 days) |
| S3 · 高开不破 · Gap holding at 10:00 | 10:00 | 219 | 9 | -2.95% | -1.49% | 31% | -1.6 | 0% | Too early (9/60 days) |
| S4 · 首板 · First limit-up | 14:30 | 303 | 9 | +0.47% | +1.01% | 50% | 1.5 | 100% | Too early (9/60 days) |
| S4b · 首板 · 悲观成交 · First limit-up, pessimistic fill | 14:30 | 14 | 5 | -2.98% | -2.97% | 21% | -2.4 | 0% | Too early (5/60 days) |
| S5 · 早盘放量突破 · Morning breakout on volume | 10:30 | 112 | 9 | -1.97% | -1.13% | 40% | -1.1 | 0% | Too early (9/60 days) |
| S6 · 超跌反弹 · Oversold bounce | 14:30 | 778 | 9 | -1.15% | -0.85% | 30% | -2.6 | 0% | Too early (9/60 days) |

Research tooling, not investment advice.
