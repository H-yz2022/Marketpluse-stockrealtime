# Strategy lab · 策略实验室

Backtest 2025-10-13 to 2026-10-09 (241 sessions, 5,123 stocks, rebuilt from 30-minute bars). Live record from 12 Oct 2026; last updated — Beijing.

Exit: **Next close (T+1)**. Excess = return after 0.20% round-trip costs minus the equal-weight market bought at the same moment. Rules were fixed before testing; see [docs/STRATEGY_RESEARCH.md](../../docs/STRATEGY_RESEARCH.md).

## Backtest

| Strategy | Signal | Trades | Days | Avg net | Avg excess | Beat market | t | Random-pick % | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| S1 · 尾盘选股 · Late-session picks | 14:30 | 1520 | 221 | -0.16% | -0.24% | 38% | -1.0 | 29% | Fails |
| S1b · 尾盘选股 14:00 · Late-session picks, half an hour earlier | 14:00 | 1549 | 222 | -0.28% | -0.42% | 36% | -3.8 | 0% | Fails |
| S2 · 竞价选股 · Opening-auction gap | 09:25 (auction) | 29714 | 240 | +0.18% | -0.25% | 41% | -3.7 | 1% | Fails |
| S3 · 高开不破 · Gap holding at 10:00 | 10:00 | 11539 | 240 | +0.10% | -0.24% | 42% | -2.3 | 9% | Fails |
| S4 · 首板 · First limit-up | 14:30 | 11291 | 240 | +1.10% | +1.06% | 52% | 9.5 | 100% | Passes |
| S4b · 首板 · 悲观成交 · First limit-up, pessimistic fill | 14:30 | 242 | 122 | -2.46% | -2.37% | 28% | -6.0 | 0% | Fails |
| S5 · 早盘放量突破 · Morning breakout on volume | 10:30 | 7908 | 240 | -0.28% | -0.34% | 41% | -3.4 | 0% | Fails |
| S6 · 超跌反弹 · Oversold bounce | 14:30 | 11548 | 236 | -0.39% | -0.34% | 40% | -3.0 | 0% | Fails |

## Live record

No sessions recorded yet.

Research tooling, not investment advice.
