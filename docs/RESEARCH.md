# Strategy research log (19 Nov 2026)

All tests: real fees (taker 0.045% + 0.02% slippage on market fills, maker 0.015% on limit targets), a candle that
touches stop and target counts as the stop, R = net P&L / net loss at the stop, 90% bootstrap intervals.
Walk-forward: parameters picked on the first 2/3 of the data, judged on the last 1/3 they never saw.
A strategy "passes" only if its out-of-sample interval is entirely above zero.

| Test | Data | Result |
|---|---|---|
| Scalp 0.2 (VWAP pullback + 2h sweep), live rules | BTC 1m, 32 days | -0.24R/trade [-0.36, -0.12], 124 trades: **no edge** |
| Scalp arms B-H (targets, stops, exits, 3m/5m, all-day) | same | all negative; 5m least bad (-0.14R [-0.27, +0.01]) |
| Opening-range breakout (London / NY, 60 variants) | BTC 1m, 90 days | best in-sample +0.17R, **out-of-sample -0.35R** |
| VWAP +-2-3 sigma fade in a range (18 variants) | same | negative in and out of sample |
| 15m Donchian breakout with 4h trend (12 variants) | same | -0.15R out of sample, significant |
| 4h Donchian 30/10, BTC/ETH/SOL | HL 4h, Aug 2024 - Nov 2026 | +0.11R in-sample, **-0.02R out-of-sample** |
| 4h EMA 10/200, BTC/ETH/SOL | same | +0.36R full period (90 trades), +0.04R out-of-sample, wide interval |

Conclusions
- At Hyperliquid's base fee tier, 1m BTC scalping with market entries pays ~0.13% per round trip, which consumes the
  small intraday edges tested here. None of the scalp setups passes. Do not fund the scalper on this evidence.
- Slow trend-following is the closest thing to an edge (positive over the full 2.3 years, about flat on the newest
  third). Not proven; worth running forward on paper, not with money.
- Reproduce: `python scripts/research_scalp.py --days 90`, `python scripts/research_trend.py`,
  `/backtest arms 30` in Telegram.
