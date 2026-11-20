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

## Forward test (from 19 Nov 2026)

The 4h EMA 10/200 system now runs forward on paper as the TREND arm (`hlbot/trend.py`, own $100 ledger in
DATA_DIR/trend, 1% risk, 3x notional cap). Rules are frozen as above. `/trend` shows the record, `/trend backtest`
reruns the history. It is not part of any go-live gate; real money needs 30+ forward trades with the low end of the
interval above zero, and the owner's explicit go-ahead.

## Doing the opposite (20 Nov 2026, scripts/research_inverse.py)

Every strategy above with the side flipped, stop and target mirrored, same costs, same walk-forward judging.
Nothing passes; most flipped versions lose about as much as the originals, because fees are paid either way.

| flipped | out-of-sample | full period |
|---|---|---|
| live scalper (A) | - | -0.19R [-0.31, -0.06], 141 trades (original -0.24R) |
| best flipped scalp variant (C wider stop) | -0.15R [-0.33, +0.06] | -0.17R |
| ORB | -0.62R [-0.84, -0.34] | -0.08R |
| VWAP fade | -0.47R | -0.39R |
| 15m Donchian | -0.17R | -0.08R |
| 4h Donchian 55/10 | -0.12R | -0.08R (-29R) |
| 4h EMA 10/50 | -0.09R | -0.11R (-30R) |

Reading: a strategy and its mirror both losing means the entries are close to random and costs decide the result.
The flipped scalper was also flattered (market-price entries charged the maker fee) and still lost.
