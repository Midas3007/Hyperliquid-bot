# HEVI — Futures Challenge Protocol v3.3 — Hyperliquid

2026-11-06 · @Midas · adapted from Bybit v2.0 (canonical) and Bitget v2.1 ($1 micro phase)

This is the human-readable spec that HEVI runs. HEVI enforces it through the rules engine in `hlbot/rules/`: pure,
unit-tested functions. Every threshold lives in one file, `protocol.toml`. Where v2.0 and v2.1 differ, v2.1's logic
is used, and when a conflict remains **the stricter rule wins**. Every v3.0 open question is now settled; see
[Decisions](#decisions-v31).

**v3.1 in one paragraph:**

- **Autopilot.** HEVI trades on its own: no `y`, no input.
- **Every rule stays.** A trade still has to pass every filter, the sizing and the Module 13 final check.
  Autopilot never uses overrides.
- **Prove, then live.** By default HEVI paper-trades until its record shows an edge, then switches to real money
  by itself.
- **Token-unlock skip dropped**, by your decision.
- **Everything else** follows the recommendations below.

| Module | Code | Tests |
|---|---|---|
| 0 Pre-flight | `engine._run`, `rules/schedule.macro_in_window` | `test_rules`, `test_pipeline` |
| 1 BTC gate | `rules/gate.py` | `test_rules::test_btc_bands`, `test_post_pump_flags` |
| 2 Screener | `rules/screen.py` | `test_sweeps_*`, `test_cooldowns_*`, `test_auto_skips` |
| 3 Deep dive, filters, ranking | `rules/metrics.py`, `rules/filters.py` | `test_hard_filters`, `test_ranking`, `test_metrics` |
| 4 TP rule | `rules/bracket.take_profit` | `test_take_profit_rule` |
| 5 Direction | `rules/direction.py` | `test_direction` |
| 6 Entry | `rules/bracket.entry_confluence`, `rules/schedule.cancel_triggers` | `test_entry_confluence_*`, `test_cancel_triggers` |
| 7 Risk and sizing | `rules/sizing.py`, `rules/bracket.stop_loss` | `test_rules_sizing` (worked examples) |
| 8 Circuit breakers | `rules/breakers.py` | `test_breakers` |
| 9 Management | `rules/schedule.py`, `engine.manage` | `test_schedule_windows`, `test_time_stop_*` |
| 10 Execution | `engine.confirm`, `engine._post_fill` | `test_full_flow_*`, `test_reissue_*` |
| 11 Logging and review | `store.py`, `rules/review.py` | `test_review_and_recalibration` |
| 12 Autopilot and commands | `engine.autopilot_tick`, `engine.start/resweep/override`, `bot.parse` | `test_pipeline::test_autopilot_*` |
| 13 Hard rules | `rules/card.final_check` (runs on the exact order, overrides can't bypass it) | `test_card_and_final_check` |

---

## Role, goal and conventions

HEVI calculates, the rules decide, and HEVI acts. You watch, and can `/pause` at any time. If a rule blocks a
trade, HEVI logs which rule and moves on. "No trade today" is a valid, complete result. The goal is $1,000 equity,
through maximum long-run compounded growth without ruin.

- **UTC everywhere.** Days, windows and "daily change" (% from the 00:00 UTC open) are all UTC. v1's `TIMEZONE`
  setting is gone.
- **Funding:** Hyperliquid pays funding every hour. Every funding number is shown on the protocol's 8h basis
  (hourly rate × 8). 0.01%/8h is the neutral default, not a cap.
- **R** = the dollar loss if the SL hits, fees and slippage included. Every result is logged in R.
- **Missing, stale (older than 15 min), zero or null data = FAIL** for that check. The bot never estimates a
  missing value. Failing on missing data is a hard failure: no override can pass it.
- Every number on a card is recomputed from live data, and recomputed again right before the order is sent.
- Claude never sets prices, sizes, leverage or direction, and never picks the trade. Ranking is deterministic.
  Claude rates news headlines, writes the post-trade study as a hypothesis, and flags thesis-breaking news on an
  open position.

## Module 0 — Setup and pre-flight

**One-time setup (Hyperliquid):**

- **Equity** = perp account value from `clearinghouseState` (`marginSummary.accountValue`). Available =
  `withdrawable`. Spot USDC doesn't count; move it Spot → Perps. A dedicated sub-account is recommended as the
  challenge firewall.
- **Volatility source**, labelled on every card: BTC and ETH use Deribit DVOL percentile (**IV**). Everything else
  uses the 30-day realized-volatility percentile (**RV**). A 0 reading = missing data.
- **Whale L/S** (v3.3) = live positions of the 200 most profitable active traders on Hyperliquid's leaderboard,
  weighted by notional. "Active and profitable" means: account of $100k or more, in profit this month and all-time,
  and a month's volume between 0.5x and 100x the account. Below that range the account isn't trading perps; above
  it, it's a market maker whose positions are inventory, not bets. The list is re-picked every 6h; positions
  refresh every 10 min. If fewer than 5 of the 200 hold the coin, **big taker orders** stand in: buy vs sell
  notional of orders of at least max($10k, 0.02% of the coin's 24h volume) over the last 4h, from the trade stream,
  with fills of one order added together, and at least 5 such orders. Neither available = no whale reading.
  Hyperliquid has no all-accounts ratio, so Crowd L/S is always N/A and its rules never apply.
- **Fees** come from your account's `userFees` rates (maker = `userAddRate`, taker = `userCrossRate`). The
  fallback is the base tier: 0.015% maker / 0.045% taker.
- **API key:** an API (agent) wallet can trade but cannot withdraw. Your main wallet's key never goes into the
  bot, and the bot never logs secrets.

**Every scan (autopilot every 30 min, or `start`):**

1. Confirm equity from the API. If your stated `$X` differs by more than 5%, the bot sizes on the lower number and
   says so.
2. Check open positions and orders: max 1. Untracked resting entry orders left from earlier sessions are
   cancelled.
3. Check circuit breakers (Module 8).
4. Check the trading window (Module 9).
5. Check high-impact US releases (CPI, FOMC, NFP) inside the planned hold.
   - Sources: the free ForexFactory weekly feed, which covers every possible hold because holds end by Friday
     20:00 UTC, plus `calendar.toml` as a manual fallback.
   - If a release falls inside the hold, HEVI skips. Overriding is manual-only.
   - If neither source covers the hold, it fails as missing data.

## Module 1 — BTC gate

x = BTC perp % change from today's 00:00 UTC daily-candle open. The gate is re-checked while an entry is unfilled.

| x | Action |
|---|---|
| x ≤ −4% | No new trades today |
| −4% < x ≤ −2% | Half risk, max 1 trade, no re-entries |
| −2% < x < +3% | Normal |
| x ≥ +3% | Normal risk (never size up) + post-pump check |

**Post-pump check.** Reversal risk is flagged when 2 or more of these hold:

- funding is at least 0.03%/8h and rising (the latest hourly rate is above the average of the 8 before it)
- OI is up at least 2% in 24h while the last completed 4H candle moved less than 0.5%
- the last completed 4H or 1D candle shows a rejection wick at structural resistance (upper wick at least 50% of
  the candle's range, high within 0.2% of the level, close back below it)
- whale L/S is up at least 10% versus 24h ago

Inputs that are N/A can't raise a flag, and the card lists them.

**Cancel rule:** if BTC moves into a stricter band while an entry is unfilled, the entry is cancelled.

## Module 2 — Screener

Run in the same order every session:

- **Sweep 1:** highest taker buy/sell ratio, 24h notional volume above $50M, top 5.
- **Sweep 2:** strongest momentum (UTC-day % change, positive only), volume above $100M, top 3.
- **Sweep 3 (v3.1):** heaviest sell flow (lowest buy/sell ratio), volume above $50M, top 3. Shorts mirror sweep 1,
  so trend shorts can be found.
- Each sweep walks down its ranking past automatically skipped coins until it has its quota.

**Automatic skips:**

- stablecoins
- EXTREME volatility (85th percentile or higher; a hard rule)
- coin on cooldown
- in the BTC half-risk band, a coin already traded today

**Moved or dropped in v3.1:**

- "Down more than 8%" and "true whale L/S below 1:1" now apply per direction through the Module 3 hard filters.
  Longs are filtered exactly as before; shorts get the mirrored versions.
- The token-unlock skip is off (`unlock_check = false`, your decision). Setting it to `true` brings back the
  `calendar.toml` check.

**Cooldowns, the same for every coin:**

- an SL hit puts the coin off-limits for 24h
- 2 SLs on the same coin within 30 days put it off-limits for 14 days, counted from the latest SL

The v1 TAO skip list is gone.

## Module 3 — Deep dive

Every input carries a source and a timestamp.

| Input | Hyperliquid source |
|---|---|
| Price, UTC-day change | mark price (asset contexts); 1D candle open at 00:00 UTC |
| 14-day ADR | 14 completed UTC daily candles, mean (high − low) / open |
| 4H and 1H ATR(14), % of price | completed 4H / 1H candles |
| Buy/sell ratio | taker buy vs sell notional from the websocket trade stream, 60-min window. N/A until 30 min of trades |
| Whale L/S | positions of the 200 most profitable active traders; big taker orders as fallback (Module 0) |
| OI 24h delta, 30d growth | current OI from asset contexts vs the bot's own hourly snapshots |
| Funding (8h) + trend | asset contexts × 8; `fundingHistory` for "rising" |
| Volatility | IV (DVOL) or RV percentile, labelled |
| News | last 3 matching headlines from 9 public crypto RSS feeds plus a per-coin Google News search (no key), rated by Claude, plus any thesis-breaking catalyst in the hold |
| S/R on 5m, 4H, 1D | Bollinger, Fibonacci pivots, VWAP and Volume Profile on each timeframe, plus 4H swing points |
| Contract specs | `szDecimals`, margin table (max leverage and MMR by size), `onlyIsolated` |

**Hard filters (any fail = skip).** Shorts mirror the directional filters.

| Signal | Long | Short |
|---|---|---|
| UTC-day change | not down > 8% (red = caution) | not up > 8% |
| Whale L/S | ≥ 1.5 | ≤ 1/1.5 (0.67) |
| OI 24h | growing or flat (≥ −1%) | same |
| Volatility | LOW / NORMAL / HIGH (caution) — EXTREME = skip | same |
| News | 2+ of the last 3 bullish, no thesis-breaking catalyst | 2+ of 3 bearish |
| TP | passes Module 4 | same |
| Net R/R | ≥ 1:3 (Module 7) | same |

Volatility bands are LOW < 25th, NORMAL 25–75th, HIGH 75–85th and EXTREME ≥ 85th percentile.

**Ranking.** +1 each for:

- whale L/S ≥ 2:1
- 30-day OI growth > 0
- news 3/3
- LOW volatility
- net R/R ≥ 1:4
- TP distance ≤ 1.0× ADR

The highest score wins; ties go to the higher net R/R. The card names the runner-up.

## Module 4 — TP rule

- **Anchor:** the first real 4H structural level in the trade's direction, beyond the current price. "Real
  structure" means 4H swing highs/lows, 4H volume-profile POC/VAH/VAL and weekly Fibonacci pivots, with levels
  within 0.15% merged into one zone. Bollinger bands and VWAP are dynamic, so they are not structure.
- **Clean air:** because the TP anchors to the *first* such level, nothing significant sits between entry and TP.
- **Front-run:** the TP sits before the level by max(0.2%, 0.1 × 4H ATR).
- **Reachability:** TP distance must be ≤ 1.2× the 14-day ADR.
- Never a 1D level. One TP, one SL, no trailing.

If no level qualifies, or the first level is too close for 1:3 net, the coin is skipped. The TP never jumps past
the first level.

## Module 5 — Direction

Long is the default but needs 4 of 5 triggers. Shorts need 2 or more of theirs. N/A counts as a fail.

| Long (4 of 5) | Trend short (2+) | Reversal short (2+) |
|---|---|---|
| buy/sell > 3x | sell/buy > 3x | rejection wick at 1D/4H structure |
| whale L/S > 1.5 | whale L/S < 0.7 | funding ≥ 0.03%/8h and rising |
| funding 0.00–0.03%/8h | funding ≤ −0.03%/8h (shorts pay it; counted in net R/R) | OI ≤ −5% on a ≥ +3% day |
| BTC normal band or better | BTC's last 4H close broke below 4H structure | whale L/S flips below 1 after a ≥ +5% day |
| F&G > 50 | F&G < 40 | BTC IV jumps into HIGH/EXTREME on a ≥ +3% BTC day |

- **Reversal 2+:** the bot shows long and short setups (long only if long also qualifies) and you choose with
  `y 1` / `y 2`.
- **Long and trend short both qualify:** this is a conflict, and the coin is skipped.

## Module 6 — Entry discipline

- **Post-only limit.** Hyperliquid's ALO (add-liquidity-only) time-in-force rejects an order that would cross the
  book, so the maker fee is guaranteed and a mistyped price can't fill at market.
- **5m confluence:** at least 3 of the 4 methods (Bollinger, VWAP, Fibonacci, Volume Profile) within 0.25% of
  each other; a 4-method cluster wins when there is one. It sits on the passive side of price, within 1% of it. 5m
  sets timing only; the SL anchors to 4H.
- **TP and SL attached to the entry:** sent as one `normalTpsl` group with the entry. They are reduce-only market
  triggers that fire on the **mark price**.
- **Cancel an unfilled limit when any of these happen** (never chase):
  - 4h pass
  - BTC enters a stricter band
  - price covers half the distance to TP unfilled
  - a 4H candle closes beyond the SL level
  - Friday 00:00 UTC arrives
- **Partial fill:** Hyperliquid activates `normalTpsl` children only after the entry fills completely.
  - In live mode HEVI subscribes to your account's fill stream, so any fill triggers an immediate sync that
    places position-level TP/SL, usually within a second.
  - The 20-second sync is the backstop.
  - Position TP/SL is resized as the fill grows, and the remainder is cancelled on any cancel trigger.

## Module 7 — Risk and sizing

Size by dollar risk. Leverage only decides how much margin is posted.

- **Risk:** normal 10% of equity at the SL (= 1R).
  - Half risk = min(5%, your risk / 2). It applies in the BTC −4% to −2% band, after 2 straight SLs, or at 30%+
    below peak.
  - Hard ceiling 15%. `risk X%` sets your own level within the ceiling.
- **SL:** beyond the nearest 4H structural level by at least 0.25 × 4H ATR, and never closer than 1.5% to entry.
  It is rounded away from entry to Hyperliquid's price ticks (5 significant figures, at most 6 − szDecimals
  decimals), so rounding can never pull it inside the floor.
- **C** (loss-side costs) = maker + taker + 0.05% slippage = **0.11%** at Hyperliquid base tier (Bybit 0.125%,
  Bitget 0.13%).
- **Notional** = equity × risk% ÷ (SL% + C). **Qty** = notional ÷ entry, rounded **down** to the coin's size step.
- **MMR** comes from the coin's Hyperliquid margin table: maintenance rate = 1 / (2 × max leverage of the tier
  that holds this notional), with deductions so the margin stays continuous across tiers.
- **Leverage** = floor(1 ÷ (SL% + MMR + 2%)), capped at 25x and the tier's max. No floor.
- **Margin** (isolated) = notional ÷ leverage, at most 80% of available.
- **Liquidation:** Hyperliquid's isolated formula is liq = entry − side × (margin − maintenance) ÷ size ÷
  (1 − MMR × side). It must sit at least 1.5% beyond the SL, measured against max(entry, SL). After a fill the bot
  reads the exchange's own liquidation price; if the buffer is short it adds isolated margin
  (`updateIsolatedMargin`), or closes the position if that fails.
- **Net R/R** = (qty × TP move − maker entry fee − taker exit fee − **TP slippage** − funding cost over the 48h time
  stop) ÷ (notional × (SL% + C)). It must be at least 1:3. Funding counts only when this side pays it.
- **v3.1:** the TP is a market trigger, so its fill slips too (`slippage_on_tp`). That matches v2.0's own $7.66
  example.
- **Loss check:** the loss at the SL must equal the planned R within 5%. Coarse size steps on small accounts can
  fail this; the bot skips rather than size wrong.

**Micro phase (v2.1, rebuilt for Hyperliquid's $10 minimum order):**

- It applies below **$6** equity.
- If normal risk buys less than the minimum, risk rises **only as far as the minimum needs**, up to 15%.
- It is never allowed in a half-risk state.
- If the minimum needs more than 15%, the coin is skipped.
- Above $6, an order under the minimum is simply skipped.

### Worked examples, recomputed for Hyperliquid (fixtures in `tests/test_rules_sizing.py`)

**Bybit v2.0 Module 7 example.** Equity $25, risk 10%, entry $92.50, SL 1.5%, MMR 1% (a 50x-max coin), TP +5.1%,
funding 0.01%/8h.

| | Bybit v2.0 | Hyperliquid v3.1 |
|---|---|---|
| C | 0.125% | 0.11% |
| Notional → qty | $153.8 → 1.66 | $155.28 → **1.67** ($154.48) |
| Leverage | 22x | **22x** (floor(1/0.045)) |
| Margin | ≈ $6.98 | **$7.02** |
| Liq | ≈ $89.22 (2.0% beyond SL) | **$89.19** (2.08% beyond the $91.11 SL) |
| Net reward | ≈ $7.66 | **$7.61** (after $0.02 maker, $0.07 taker, $0.08 TP slippage, $0.09 funding over 48h) |
| Loss at SL | $2.50 | **$2.49** (within 0.5% of R) |
| Net R/R | 1:3.07 | **1:3.06** (1:3.09 without TP slippage, as in v3.0) |

On a 10x-max coin (MMR 5%) the same trade gets 10x leverage (the formula gives 11, capped at the coin), $15.45
margin and liquidation at $87.63.

**Bitget v2.1 chunk 28 ($1 micro phase).**

| Equity $1 | Bitget ($5 min, C 0.13%) | Hyperliquid ($10 min, C 0.11%) |
|---|---|---|
| SL 1.5%, 10% risk | $6.13 ≥ $5: OK | $6.21 < $10. The minimum needs 16.1% risk > 15%: **skip** |
| SL 2.5% | 10%: $3.80; micro 15%: $5.70: OK | needs 26.1%: **skip** |
| SL 3% | 15%: $4.79: skip | needs 31.1%: **skip** |

**Nothing is tradeable at $1 on Hyperliquid.** The smallest equity that can trade at all is about $1.07 (a 1.5%
stop at 15% risk), and Hyperliquid's minimum deposit is 5 USDC anyway. Tested Hyperliquid equivalents:

- **$1.20, SL 1.5%:** micro at 13.4% risk for exactly $10 notional.
- **$1.20, SL 2.5%:** skip.
- **$5, SL 5%:** micro at 10.2%.
- **$5 in half risk, SL 3%:** skip (no micro in a half-risk state).

## Module 8 — Circuit breakers

Breakers fire automatically and are announced, not asked.

- **SL hit:** no new entries until 00:00 UTC.
- **2 consecutive SLs:** half risk until the next TP.
- **3 consecutive SLs:** a 48h pause, then a full review before trading again. With autopilot on, HEVI runs the
  review itself and sends it to you. "Consecutive" counts SL exits since the last TP; time stops and
  discretionary exits don't reset the streak (the stricter reading).
- **Equity 30%+ below peak:** half risk until back within 15% of peak.
- **Equity 50%+ below peak:** stop for 7 days, then a review, then restart only on a new `version` in
  `protocol.toml`. **This is the one thing HEVI never does by itself:** after losing half the account, a human
  decides.
- **Max 2 new entries per UTC day:** 1 in the BTC half-risk band, with no re-entries into a coin traded today.
- **Paper mode:** breakers count from the last `/paperreset`.

**Overrides (manual trading only; autopilot never overrides).** `override: [reason]` takes the closest
candidate that failed **soft** rules only (for example news,
whale, OI, direction count, confluence, macro release, cooldown). The card shows `OVERRIDE` and the rules it
bypasses. The trade is logged with the reason, and overrides get their own line in every review. Hard rules
(Module 13), sizing and missing data can never be overridden.

## Module 9 — Trade management

- **Entries:** Mon 00:00 – Thu 23:59 UTC only.
- **Friday:** unfilled entries cancel at Fri 00:00 UTC, and every position closes by Fri 20:00 UTC.
- **Time stop:** close at market 48h after the fill.
- The bot never moves the SL, adds to a position, or removes TP/SL. `/cancel` touches resting entries only.
- **Early exits on objective triggers only:**
  - BTC ≤ −4% for longs or ≥ +4% for shorts
  - a CPI, FOMC or NFP release inside the remaining hold
  - thesis-breaking news on the coin (Claude reads new headlines hourly)

  With autopilot on, HEVI closes the position (`act_on_early_exits`), logs it as `early` and later compares it
  with what the bracket would have made. With autopilot off, it only suggests.
- **Discretionary exits** (`/close COIN [reason]`) are always allowed and logged as `discretion`. Once the time
  stop has passed, the bot records what the bracket would have made (`bracket_r`). After 10 discretionary exits,
  if discretion trails the bracket, `/close` requires a reason.

## Module 10 — Execution checklist (autopilot, or after `y`)

1. Re-fetch everything and re-run every filter for this coin and side.
   - If price moved more than 0.3% since the card, or any number changed, the card is **re-issued** instead of
     placed.
   - If a filter now fails, nothing is placed.
2. Run the Module 13 final check on the exact order.
3. Set isolated leverage for the coin, then submit exactly the card: ALO limit, side, qty, price, plus
   reduce-only SL and TP mark-price triggers (`normalTpsl`).
4. Confirm the order is live (order id + status). Any other status: cancel, log a mismatch, report.
5. On fill:
   - confirm TP and SL exist (re-attach at position level if not, or close as a mismatch)
   - confirm the exchange's liquidation price is at least 1.5% beyond the SL (add margin if not)
   - record the actual fill price and recompute R from it

## Module 11 — Logging, study and review

**Trade log (`trades` table):**

| Field | Column |
|---|---|
| # / date | `id`, `created_at`, `filled_at`, `closed_at` |
| protocol version | `version` |
| coin / direction | `coin`, `side` |
| setup score / BTC band | `score`, `btc_band` |
| entry / SL / TP / actual fill | `entry`, `sl`, `tp`, `fill_px` |
| risk $ / % / mode | `risk_usd` (recomputed from the fill), `risk_pct`, `risk_mode` (normal/half/micro) |
| exit price / exit type | `exit_px`, `exit_type` (tp, sl, time, early, discretion, friday, mismatch, liquidated) |
| result $ / R | `pnl` (after fees and funding), `result_r` |
| MAE / MFE | `mae_r`, `mfe_r` (from 5m candles while open) |
| fees / funding paid | `fees`, `funding` |
| override / bracket comparison | `override` (reason), `bracket_r` (what the bracket would have made after an early or discretionary exit) |
| card + every input | `plan` (JSON), post-trade study in `study` (JSON) |

**Skip log (`skips` table):** `ts`, `version`, `coin`, `side`, `rule` (the first rule that blocked it), `detail`,
the would-be `entry`/`sl`/`tp` when one was computed, and `outcome` 48h later: `tp` / `sl` first, `no fill`,
`neither`, or `n/a` when no bracket was computed.

**After every trade,** Claude compares the plan with what happened and records a **hypothesis**, never a rule. A
hypothesis can be promoted (`/approve N`) only when 5+ trades support it and at least 20 trades have closed. Rules
never change mid-trade.

**Every 20 trades** (and on `review`) the bot reports:

- hit rate, average win and loss in R, expectancy in R
- max drawdown in R and the longest losing streak
- the exit-type split
- early and discretionary exits vs the bracket, and override PnL
- average MAE, MFE on losers, and how many losers went green first
- the skip outcomes
- **recalibrated risk** = ½ × (p − (1 − p) ÷ b) over the last 30 trades, capped at 15%. Under 2% means no edge:
  stop and rework the screener.

## Module 12 — Autopilot and commands

**Autopilot (v3.1 default).** Every minute HEVI checks whether a scan is due. It scans when all of these hold:

- it is not paused
- it is inside the entry window
- no position or entry is open
- the 30-minute warm-up after a restart is over
- 30 minutes have passed since the last scan

A scan runs the full flow below. If a setup passes every rule, HEVI re-checks it, runs the Module 13 final check
and places it. When reversal signals produce both a long and a short, it picks the higher score, then net R/R,
then long.

It also:

- sends a message for every trade, and a daily report at 21:00 UTC (mode, equity, drawdown, trades, scans and why
  the last scan placed nothing)
- **MODE=prove** (default): runs on the paper trader until the record passes the go-live gate (10+ trades,
  expectancy ≥ +0.10R per trade, profit factor ≥ 1.2), then switches to real money once flat. Without the
  Hyperliquid keys it says the record is ready and keeps paper trading. `MODE=paper` never goes live;
  `MODE=live` starts live.

The flow (`start ($X)` runs the same, manually):

1. pre-flight
2. BTC gate and F&G
3. both sweeps
4. deep dive on every candidate
5. hard filters and ranking
6. the best coin and direction
7. all the numbers
8. the card

| Command | Action |
|---|---|
| `/pause` · `/resume` | Autopilot kill switch: no new trades; open positions stay protected and managed |
| `start ($X)` | Run the flow manually. `$X` is your equity; the API figure is checked against it |
| `y` / `y 1` / `y 2` | Execution checklist (1 = long, 2 = short when both are shown) |
| `nah` / `skip` / `try another` | Re-sweep fresh candidates, max 2 per session (UTC day), then "No trade today" |
| `n` | Discard the card |
| `how is it going` | Price, uPnL in $ and R, distance to TP/SL, funding paid, time left |
| `review` | Module 11 stats |
| `risk X%` | Set risk within the 15% ceiling |
| `override: [reason]` | Closest soft-blocked candidate (Module 8) |
| `/status` `/journal` `/close COIN [reason]` `/cancel` `/lessons` `/approve N` `/reject N` `/pump` `/paperreset N` | Utilities |
| `/why` · `/backtest` | The last scan coin by coin · replay ~200 days of prices through the exit rules (v3.3) |

v1's `start ($X) margin $Y` is retired, because trades are sized by Module 7 only.

## Module 13 — Hardcoded rules (no override)

These are checked by `rules/card.final_check` on the exact order right before it is sent:

- TP and SL attached at entry; the SL never moves away from entry
- isolated margin; max 1 open position or pending entry
- risk ≤ 15% of equity, sized only by Module 7
- SL ≥ 1.5% and beyond 4H structure; liquidation ≥ 1.5% beyond the SL
- net R/R ≥ 1:3; TP at a real, reachable 4H level
- BTC −4% or worse = no trades; EXTREME volatility = skip
- no entries Friday–Sunday; flat by Friday 20:00 UTC
- breakers trigger automatically
- missing or stale data = fail; equity confirmed within the last 15 minutes
- **v3.1 (your decision):** an order needs authorization, which means autopilot on (the default) or your `y` in
  manual mode. Authorization never bypasses anything above: the final check runs on every order, and autopilot
  never uses overrides.

## Confirmation card

A real card from the end-to-end test (`tests/test_pipeline.py`: autopilot, paper mode, crafted market). The
indented lines are the data behind the card, and `!` lines are cautions. In manual mode the last line asks for `y`.

```
Equity $100.00 (confirmed 12:00 UTC) | risk 10% | breakers: none active
BTC gate: +1.01% from the UTC open -> NORMAL
Fear & Greed: 62 (Greed)

# HEVI - Open Limit Order - Protocol v3.2 (Hyperliquid) - PAPER
--------------------------
Symbol:      SOL-PERP (Hyperliquid)
Direction:   Long
Margin mode: Isolated
Limit:       $99.61 post-only (ALO) - 5m confluence BB+FIB+VP+VWAP
Qty:         6.23 SOL (~$620.57 notional)
Leverage:    16x (MMR 2.50%, coin max 20x)
Margin used: ~$38.79 (39% of equity)
TP:          $105.78 (+6.19%) - in front of 4h SWING high 106
SL:          $98.115 (-1.50%) - past 4h SWING low 98.4 (widened to the 1.5% floor), mark-price trigger
Liq:         ~$95.779 - 2.35% beyond SL OK
--------------------------
Equity:      $100.00 (confirmed 12:00 UTC)
Risk @ SL:   ~$10.00 (10.0% of equity, costs incl.)
Net @ TP:    ~$37.35 (+37.3% of equity)
Net R/R:     1:3.74 OK
Costs:       fees ~$0.39 (base tier (paper)) | TP slippage ~$0.33 | funding ~$0.37 (48h)
Time stop:   Thu 12 Nov 16:00 UTC latest (48h after fill / Fri 20:00 UTC)
Risk mode:   Normal 10% | Breakers: none active
Setup score: 1/6 (30d OI growth) | runner-up: none
BTC band:    normal | volatility NORMAL
Data as of:  12:00 UTC
  SOL +1.01% today | ADR 6.00% | 4H ATR 1.00% | 1H ATR 0.50%
  buy/sell 3.50x | whale L/S 1.80 | OI 24h +2.0% | OI 30d +25.0%
  funding +0.0100%/8h | vol NORMAL (RV: 30-day realized-vol percentile) | news 2 bullish / 0 bearish of 3
  triggers: buy/sell > 3x, whale L/S > 1.5, funding 0.00-0.03%/8h, BTC normal band or better, F&G > 50
--------------------------
HEVI autopilot: every rule passed - placing it now.
```

---

## Hyperliquid mapping

**How each item was checked:**

- **SDK:** confirmed against the installed `hyperliquid-python-sdk` 0.24.0 source.
- **2nd-hand:** confirmed by third-party documentation only. The session couldn't reach Hyperliquid's own docs or
  API, so check these against the official docs.
- **Assumed:** from the existing bot or general knowledge; verify.

| Protocol concept | Hyperliquid implementation | Checked |
|---|---|---|
| Equity | `clearinghouseState` → `marginSummary.accountValue`, available = `withdrawable` | SDK |
| Isolated margin, leverage | `updateLeverage(lev, coin, is_cross=False)` before every entry; `onlyIsolated` coins are fine | SDK |
| Contract specs | `meta`: `szDecimals`, `maxLeverage`, `marginTableId` → `marginTables` tiers; price = 5 significant figures, ≤ 6 − szDecimals decimals, integers always allowed | SDK (tick rule assumed) |
| Minimum order | $10 notional | assumed (the bot's earlier behaviour); verify |
| MMR | 1 / (2 × tier max leverage); isolated liq formula as in Module 7 | 2nd-hand |
| Post-only | `{"limit": {"tif": "Alo"}}` | SDK |
| TP/SL at entry, mark trigger | `bulk_orders(..., grouping="normalTpsl")` with reduce-only `trigger` orders (`isMarket`, `tpsl`); triggers use the mark price; children activate only after a full fill → position TP/SL (`positionTpsl`) on partial fills | SDK (grouping) / 2nd-hand (mark price, partial-fill behaviour) |
| Funding | hourly; `fundingHistory` for the trend; normalized × 8 | SDK |
| Fees | `userFees` → `userAddRate` / `userCrossRate` | SDK |
| Whale / Crowd L/S, buy/sell | no native ratios → leaderboard positions; taker flow from the `trades` websocket; Crowd = N/A | SDK (subscription) |
| OI 24h / 30d | asset-context OI + the bot's own hourly snapshots | SDK |
| RV percentile | hourly candles, 24h window ranked over 30 days | — |
| BTC daily change | BTC perp 1D candle open (UTC) | SDK (candles) |
| F&G, news, unlocks, macro | alternative.me; RSS + Claude; `calendar.toml`; `calendar.toml` | — |
| Add margin after fill | `updateIsolatedMargin` | SDK |
| Funding paid | `userFunding` ledger | SDK |
| API key | agent wallet, trade-only | SDK |

## Rules that can't be implemented on Hyperliquid as written

| # | Rule | Problem | v3.1 resolution |
|---|---|---|---|
| 1 | Unlock auto-skip (M2) | no free unlock feed | **dropped** by your decision (`unlock_check = false`) |
| 2 | TP/SL attached at entry (M6/M13) | Hyperliquid activates `normalTpsl` only after a full fill | fill-stream websocket → position TP/SL within about a second; 20s sync as backstop |
| 3 | Whale L/S (M3/M5) | no native top-trader ratio | v3.3: the 200 most profitable active leaderboard traders' positions, notional-weighted; big taker orders when under 5 of them hold the coin |
| 4 | Crowd L/S (v2.1 chunk 9) | no all-accounts ratio | dropped; whale data used |
| 5 | Buy/sell ratio | no ratio endpoint | taker flow from the trade stream; 30-min warm-up after restarts (autopilot waits for it) |
| 6 | OI 24h delta / 30d growth | no OI history endpoint | own hourly snapshots, shared by paper and live (`market.db`); no trades in the first 24h after the first deploy |
| 7 | News "2+ of 3" | thin free coverage for alts | 9 crypto RSS feeds + per-coin Google News search, no key needed |
| 8 | Scheduled catalysts | no calendar feed | Claude flags them from headlines; delisted coins leave Hyperliquid's universe |
| 9 | Macro calendar | no keyed API wanted | free ForexFactory weekly feed + `calendar.toml` fallback |
| 10 | "Check Bybit's actual liq price" | — | Hyperliquid `liquidationPx`; margin added automatically |
| 11 | Manual entry fallback (M10) | — | not needed with autopilot; the card lists every field |

## Changelog

**v3.3 (data and tools) vs v3.2:**

- **Smarter whale list.** v3.2 read the 60 largest accounts. On 13 Nov 2026 only 11 of them held any perp position,
  and those were mostly market makers. Now: the 200 most profitable active traders (see Module 0). In the same
  check, 151 of the 200 held positions, and the number of coins with 5+ whale holders went from 26 to 127. Whale
  L/S history is reset once, because the old and new ratios aren't comparable.
- **Big-order fallback.** When fewer than 5 tracked traders hold a coin, big taker orders from the trade stream give
  the whale reading (same thresholds). This was "whale absent" in v3.2: swing failed the coin and day skipped the check.
- **`/why`.** The last scan coin by coin, with the first rule that blocked each candidate and when the next scan runs.
- **`/backtest`** and `scripts/backtest.py`: about 200 days of hourly Hyperliquid prices replayed through the live
  stop, target, sizing and Friday rules (`hlbot/backtest.py`). It tests the exits only. Flow, whale, OI and news
  have no history, so direction is "every setup" or "the side of the last 24h move".
- **Public track record.** HEVI publishes its trades to a page on Netlify backed by Neon (`record-site/`,
  `RECORD_URL` / `RECORD_TOKEN`). Only trade facts are sent: no wallet address, no keys.
- **Backtest findings (13 Nov 2026, 8 coins, 30 weeks), and why nothing was loosened:**
  - A valid bracket exists in about 0.5% of coin-hours per side. Of the rest, 67% fail net 1:3 at the first 4H level
    and 23% have that level too close. This is why HEVI trades rarely. The two rules causing it are hard rules.
  - On price alone, the exits lose a little: about -0.1R per trade with one position at a time, 20-25% winners.
  - No ADR reach limit (0.8-1.5x) or hold time (24/48h) tried turns that positive.
  - Counting only 4H swing points as targets looked better (day mode +0.32R/trade over 32 trades). But it was
    negative in the most recent third of the data, and at that sample size the differences are within noise.
    The definition of a real 4H level was a v3.1 decision (Q5), so it is unchanged.
  - Conclusion: the edge has to come from the entry filters, which can't be backtested. The go-live gate (10+ paper
    trades, +0.10R per trade, profit factor 1.2) is what keeps real money out until the paper record proves it.

**v3.2 (modes) vs v3.1:**

- **Modes** (`/mode swing|day|multi`, default `multi`). The hard rules are identical in every mode.
  - `swing`: the v3.1 protocol as written.
  - `day`: holds up to 24h, and the target must sit within one day's range (1.0x ADR). Long needs 3 of the
    measurable triggers (not 4 of 5). Buy/sell threshold 2.5x. A coin no tracked whale holds is not penalised.
    With fewer than 2 rated headlines there is no sentiment check, but a thesis-breaking catalyst still blocks.
  - `multi`: day when BTC has moved 2%+ since the UTC open or BTC volatility is HIGH/EXTREME, otherwise swing.
  - Scalping is not a mode. A 1.5% stop and a 1:3 net target need a 5%+ move, so a scalp would break the
    stop-loss and reward-to-risk hard rules. Changing those needs your explicit approval.
- **"No top whale holds it" is not missing data.** Only a failed leaderboard fetch counts as missing.
- **Buy/sell coverage survives websocket reconnects.** A reconnect used to restart the 30-minute window, so the
  screener's first sweep went empty for half an hour after every drop.
- **Why-no-trade report.** Every scan logs its funnel and most common blockers; the daily report includes them.
- Trades record their strategy, and `review` splits results by mode.
- Removed the dead news feeds newsbtc.com and cryptopotato.com.

**v3.1 (HEVI) vs v3.0:**

- **Autopilot** replaces the human-in-the-loop hard rule (your decision): scans, places, manages, exits and
  reports with no input. `/pause` and `/resume` remain.
- **MODE=prove** (default) paper-trades until the go-live gate passes, then goes live by itself. `paper` and
  `live` modes are also available.
- **Objective early exits** are executed by the autopilot and reviewed against the bracket.
- **The 3-SL breaker review** is done by HEVI itself; the 50% drawdown stop still needs you.
- **Token-unlock skip** off (your decision).
- **Entry confluence** is 3 of 4 methods (4 of 4 still wins when available).
- **Shorts** are possible: direction-aware filters, plus a sell-flow sweep.
- **TP slippage** is counted in net R/R.
- **Data:** a fill-stream websocket, Google News plus more RSS feeds, and the ForexFactory macro feed.
- **Cost:** Claude only reads news for coins that pass every other rule.
- **Telegram:** the bot is named HEVI and sets its own Telegram name, description and command menu.

**v3.0 (Hyperliquid) vs v2.0 / v2.1:**

- **Exchange:** Bybit/Bitget → Hyperliquid perps. Every exchange-specific item is remapped (table above).
- **Costs:** C is 0.11% at base tier (v2.0 0.125%, v2.1 0.13%), using your live fee tier when available.
- **Margin:**
  - MMR now comes from Hyperliquid margin tables (1 / (2 × tier max leverage)), not risk-limit tier 1.
  - Liquidation uses Hyperliquid's isolated formula.
  - The leverage cap is min(25x, the tier's max).
- **Micro phase:** rebuilt for the $10 minimum. The ceiling is $6 instead of $3. Risk rises only as far as the
  minimum needs (the stricter reading of v2.1, whose example jumped straight to 15%). A $1 start is impossible on
  Hyperliquid.
- **Funding:** normalized from hourly to 8h. Projected over the 48h time stop and counted only when this side pays.
- **Volatility:** Crowd L/S percentile logic (v2.1) is unused. EXTREME ≥ 85th percentile (v1 bot used 90th).
- **Liq buffer:** measured against max(entry, SL), the stricter base.
- **Half risk:** with a custom risk it is min(5%, risk / 2).
- **Consecutive SLs:** time and discretionary exits don't reset the streak (stricter).
- **Ranking:** deterministic score + net R/R tie-break. Claude no longer picks the trade.
- **Partial fills:** handled by position-level TP/SL; `/cancel` no longer removes TP/SL.

**Removed from the v1 bot:**

- Mode A (80–85% margin) and Mode B sizing
- the BTC +3% size-up
- the TAO skip list
- the local-timezone Friday rules
- the Friday 1:1.5 minimum R/R
- the 8% max-SL cap
- the 2-method entry confluence
- the 1D TP targets
- v1's 0.5% maintenance assumption
- custom margin
- the order-book buy/sell fallback

## Decisions (v3.1)

| Q | Question | Decision |
|---|---|---|
| Q1 | Token unlock data | **Dropped** (your call) |
| Q2 | Leaderboard positions as whale data | Kept |
| Q3 | Buy/sell warm-up | 30 min of trades; autopilot waits it out after every restart |
| Q4 | Entry confluence | **3 of 4** methods |
| Q5 | Structural 4H levels | Swing points, volume profile, weekly Fibonacci (kept) |
| Q6 | Volatility bands | LOW < 25, HIGH ≥ 75, EXTREME ≥ 85 (kept) |
| Q7 | Undefined magnitudes | Defaults kept (OI flat ±1%, rising +2%, stall < 0.5%, ratio +10%, OI drop ≤ −5% on ≥ +3%, pump ≥ +5%) |
| Q8 | Volume screens | $50M / $100M kept |
| Q9 | Micro phase | Below $6, risk only as far as the $10 minimum needs (kept) |
| Q10 | Shorts | **Direction-aware filters + sell-flow sweep** |
| Q11 | Long and trend short both qualify | Skip (kept) |
| Q12 | Partial-fill protection | **Fill-stream websocket** |
| Q13 | News coverage | **Google News + more RSS feeds** (free) |
| Q14 | OI history | Own snapshots; 24h warm-up after the first deploy |
| Q15 | Equity account | Main account works; a dedicated sub-account is still recommended as a firewall |
| Q16 | Macro calendar | **ForexFactory feed** + `calendar.toml` fallback |
| Q17 | F&G staleness | By fetch time (kept) |
| Q18 | Half risk with a custom risk | min(5%, risk / 2) (kept) |
| Q19 | Slippage at TP | **Counted** |
| — | Human confirmation | **Replaced by autopilot** (your call); prove-then-live by default, going live after **10** paper trades (your call, was 20) |

---

## Carried over

**Rules learned in v1** (now in the code):

- sub-1.5% stops hunted → 1.5% floor
- TAO on a red BTC day → BTC gate
- 1D TPs unreachable → 4H TP rule
- early manual exits → discretion logged against the bracket
- 0.01%/8h funding is neutral, not a cap

**Trade history (Bybit, v1):**

| # | Coin | Entry | Exit | Result | Key lesson |
|---|---|---|---|---|---|
| 1 | SUI long | ~$0.689 | — | +$3.90 | First win, template set |
| 2 | XRP long | $1.3019 | ~$1.318 | +$1.42 | Trailing stop worked |
| 3 | SUI long | ~$0.952 | $0.9981 | +$7.10 | Best % on small balance |
| 4 | ONDO long | ~$0.4416 | SL $0.427 | −$0.49 | SL too tight |
| 5 | XRP long | $1.5285 | $1.5728 | +$8.55 | Biggest single win |
| 6–8 | SUI ×3 | Various | SL | −$0.58 | Sub-1.5% SL hunted |
| 9 | TAO long | $288.00 | SL $282.00 | −$10.46 | Red BTC day, biggest loss |
| 10 | WLD long | ~$0.4751 | Manual | Profitable | Early close, TP not hit |
| 11 | SUI long | $1.158 | SL $1.1457 | −$3.94 | ~1.1% SL hunted instantly |
| 12 | WLD test | ~$0.5090 | ~$0.5466 | +$1.70 | $1 test worked cleanly |
| 13 | HYPE long | $92.00 | $94.50 | +$10.00 | New ATH, read the reversal |
| 14 | HYPE long | $92.50 | Live | In progress | TP $97.04, SL $91.11 |

Open from v2.0: v1 Module 1 referenced two TAO losses, but only one is logged.

**Balance tracker:**

- Bybit run: started at $3.20; peak $24.77 after Trade 13.
- HEVI run: starts from your first deposit.
- Milestones: $6 (micro phase ends) → $10 → $50 → $100 → $250 → $500 → $1,000.
- Optional vault rule: at $100, $250 and $500, move 20% of equity to a separate sub-account.
