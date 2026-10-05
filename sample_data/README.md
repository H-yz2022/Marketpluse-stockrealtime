# Built-in sample dataset

Market data as of 2026-09-30 (A-shares) / 2026-10-02 (HK), built 2026-10-04 with `python scripts/build_sample.py`.

| File | Contents |
|---|---|
| quotes.parquet | one quote per listed A-share / HK stock, ETF and index (that session's close) |
| daily.parquet | raw daily bars: 10 years for the starter universe, ~61 sessions for every other A-share |
| minutes.parquet | 1-minute series: last 5 sessions for the starter universe, plus the latest session for every stock the 14:00 / 14:30 replay needs |
| flow.parquet | main-fund net inflow per A-share for that session |
| universe.parquet | every listed code and name |
| factors.json, fees.json, announcements.parquet | adjustment factors, ETF fees, recent announcements |

Prices and flows come from Tencent, Sina and East Money public web endpoints and remain theirs; this small sample is included for demonstration and testing only.
