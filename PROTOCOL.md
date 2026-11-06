# Futures Challenge Protocol v3.0 — Hyperliquid

2026-11-06 · @Midas · adapted from Bybit v2.0 (canonical) and Bitget v2.1 ($1 micro phase)

This is the human-readable spec. The bot enforces it through the rules engine in `hlbot/rules/`: pure, unit-tested
functions. Every threshold lives in one file, `protocol.toml`. Scheduled events (macro releases, token unlocks) live
in `calendar.toml`. Where v2.0 and v2.1 differ, v2.1's logic is used, and when a conflict remains **the stricter
rule wins**. Values marked **[Qn]** are defaults waiting on an answer in [Open questions](#open-questions).

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
| 12 Autopilot and commands | `engine.start/resweep/override`, `bot.parse` | `test_pipeline` |
| 13 Hard rules | `rules/card.final_check` (runs on the exact order, overrides can't bypass it) | `test_card_and_final_check` |

---

## Role, goal and conventions

The bot proposes and calculates, the rules decide, and you confirm. If a rule blocks a trade, the bot names the
rule and stops. "No trade today" is a valid, complete result. The goal is $1,000 equity, through maximum long-run
compounded growth without ruin.

- **UTC everywhere.** Days, windows and "daily change" (% from the 00:00 UTC open) are all UTC. v1's `TIMEZONE`
  setting is gone.
- **Funding:** Hyperliquid pays funding every hour. Every funding number is shown on the protocol's 8h basis
  (hourly rate × 8). 0.01%/8h is the neutral default, not a cap.
- **R** = the dollar loss if the SL hits, fees and slippage included. Every result is logged in R.
- **Missing, stale (older than 15 min), zero or null data = FAIL** for that check. The bot never estimates a
  missing value. Failing on missing data is a hard failure: no override can pass it.
- Every number on a card is recomputed from live data, and recomputed again when you type `y`.
- Claude never sets prices, sizes, leverage or direction, and never picks the trade. Ranking is deterministic.
  Claude rates news headlines, writes the post-trade study as a hypothesis, and flags thesis-breaking news on an
  open position.

## Module 0 — Setup and pre-flight

**One-time setup (Hyperliquid):**

- **Equity** = perp account value from `clearinghouseState` (`marginSummary.accountValue`). Available =
  `withdrawable`. Spot USDC doesn't count; move it Spot → Perps. A dedicated sub-account is recommended as the
  challenge firewall **[Q15]**.
- **Volatility source**, labelled on every card: BTC and ETH use Deribit DVOL percentile (**IV**). Everything else
  uses the 30-day realized-volatility percentile (**RV**). A 0 reading = missing data.
- **Whale L/S** = live positions of the 60 largest Hyperliquid leaderboard accounts (by account value), weighted
  by notional. With fewer than 5 of them holding the coin, it's N/A **[Q2]**. Hyperliquid has no all-accounts
  ratio, so Crowd L/S is always N/A and its rules never apply.
- **Fees** come from your account's `userFees` rates (maker = `userAddRate`, taker = `userCrossRate`). The
  fallback is the base tier: 0.015% maker / 0.045% taker.
- **API key:** an API (agent) wallet can trade but cannot withdraw. Your main wallet's key never goes into the
  bot, and the bot never logs secrets.

**Every session (`start`):**

1. Confirm equity from the API. If your stated `$X` differs by more than 5%, the bot sizes on the lower number and
   says so.
2. Check open positions and orders: max 1. Untracked resting entry orders left from earlier sessions are
   cancelled.
3. Check circuit breakers (Module 8).
4. Check the trading window (Module 9).
5. Check high-impact US releases (CPI, FOMC, NFP) inside the planned hold, using `calendar.toml`.
   - If one falls inside the hold, the bot skips. This is a soft block, so you can override it.
   - If the calendar doesn't cover the hold, it fails: missing data **[Q16]**.

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
- OI is up at least 2% in 24h while the last completed 4H candle moved less than 0.5% **[Q7]**
- the last completed 4H or 1D candle shows a rejection wick at structural resistance (upper wick at least 50% of
  the candle's range, high within 0.2% of the level, close back below it)
- whale L/S is up at least 10% versus 24h ago **[Q7]**

Inputs that are N/A can't raise a flag, and the card lists them.

**Cancel rule:** if BTC moves into a stricter band while an entry is unfilled, the entry is cancelled.

## Module 2 — Screener

Run in the same order every session:

- **Sweep 1:** highest taker buy/sell ratio, 24h notional volume above $50M, top 5 **[Q8]**.
- **Sweep 2:** strongest momentum (UTC-day % change, positive only), volume above $100M, top 3.
- Each sweep walks down its ranking past automatically skipped coins until it has its quota.

**Automatic skips:**

- stablecoins
- down more than 8% on the UTC day
- a token unlock of at least 1% of circulating supply inside the hold window + 24h. No unlock data = FAIL **[Q1]**
- EXTREME volatility (85th percentile or higher; a hard rule)
- true whale L/S below 1:1
- coin on cooldown
- in the BTC half-risk band, a coin already traded today

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
| Buy/sell ratio | taker buy vs sell notional from the websocket trade stream, 60-min window. N/A until 30 min of trades **[Q3]** |
| Whale L/S | top-60 leaderboard positions (Module 0) |
| OI 24h delta, 30d growth | current OI from asset contexts vs the bot's own hourly snapshots **[Q14]** |
| Funding (8h) + trend | asset contexts × 8; `fundingHistory` for "rising" |
| Volatility | IV (DVOL) or RV percentile, labelled |
| News | last 3 matching headlines from public RSS, rated by Claude, plus any thesis-breaking catalyst in the hold **[Q13]** |
| S/R on 5m, 4H, 1D | Bollinger, Fibonacci pivots, VWAP and Volume Profile on each timeframe, plus 4H swing points |
| Contract specs | `szDecimals`, margin table (max leverage and MMR by size), `onlyIsolated` |

**Hard filters (any fail = skip).** Shorts mirror the directional filters.

| Signal | Long | Short |
|---|---|---|
| UTC-day change | not down > 8% (red = caution) | not up > 8% |
| Whale L/S | ≥ 1.5 | ≤ 1/1.5 (0.67) **[Q10]** |
| OI 24h | growing or flat (≥ −1%) **[Q7]** | same |
| Volatility | LOW / NORMAL / HIGH (caution) — EXTREME = skip | same |
| News | 2+ of the last 3 bullish, no thesis-breaking catalyst | 2+ of 3 bearish |
| TP | passes Module 4 | same |
| Net R/R | ≥ 1:3 (Module 7) | same |

Volatility bands are LOW < 25th, NORMAL 25–75th, HIGH 75–85th and EXTREME ≥ 85th percentile **[Q6]**.

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
  within 0.15% merged into one zone **[Q5]**. Bollinger bands and VWAP are dynamic, so they are not structure.
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
| funding 0.00–0.03%/8h | funding ≤ −0.03%/8h (shorts pay it; counted in net R/R) | OI ≤ −5% on a ≥ +3% day **[Q7]** |
| BTC normal band or better | BTC's last 4H close broke below 4H structure | whale L/S flips below 1 after a ≥ +5% day |
| F&G > 50 | F&G < 40 | BTC IV jumps into HIGH/EXTREME on a ≥ +3% BTC day |

- **Reversal 2+:** the bot shows long and short setups (long only if long also qualifies) and you choose with
  `y 1` / `y 2`.
- **Long and trend short both qualify:** this is a conflict, and the coin is skipped **[Q11]**.

## Module 6 — Entry discipline

- **Post-only limit.** Hyperliquid's ALO (add-liquidity-only) time-in-force rejects an order that would cross the
  book, so the maker fee is guaranteed and a mistyped price can't fill at market.
- **5m confluence:** all 4 methods (Bollinger, VWAP, Fibonacci, Volume Profile) within 0.25% of each other
  **[Q4]**, on the passive side of price, within 1% of it. 5m sets timing only; the SL anchors to 4H.
- **TP and SL attached to the entry:** sent as one `normalTpsl` group with the entry. They are reduce-only market
  triggers that fire on the **mark price**.
- **Cancel an unfilled limit when any of these happen** (never chase):
  - 4h pass
  - BTC enters a stricter band
  - price covers half the distance to TP unfilled
  - a 4H candle closes beyond the SL level
  - Friday 00:00 UTC arrives
- **Partial fill:** Hyperliquid activates `normalTpsl` children only after the entry fills completely. On a
  partial fill the bot places position-level TP/SL sized to the position on its next sync (every 20s), resizes
  them as the fill grows, and cancels the remainder on any cancel trigger **[Q12]**.

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
- **Net R/R** = (qty × TP move − maker entry fee − taker exit fee − funding cost over the 48h time stop) ÷
  (notional × (SL% + C)). It must be at least 1:3. Funding counts only when this side pays it.
- **Loss check:** the loss at the SL must equal the planned R within 5%. Coarse size steps on small accounts can
  fail this; the bot skips rather than size wrong.

**Micro phase (v2.1, rebuilt for Hyperliquid's $10 minimum order):**

- It applies below **$6** equity **[Q9]**.
- If normal risk buys less than the minimum, risk rises **only as far as the minimum needs**, up to 15%.
- It is never allowed in a half-risk state.
- If the minimum needs more than 15%, the coin is skipped.
- Above $6, an order under the minimum is simply skipped.

### Worked examples, recomputed for Hyperliquid (fixtures in `tests/test_rules_sizing.py`)

**Bybit v2.0 Module 7 example.** Equity $25, risk 10%, entry $92.50, SL 1.5%, MMR 1% (a 50x-max coin), TP +5.1%,
funding 0.01%/8h.

| | Bybit v2.0 | Hyperliquid v3.0 |
|---|---|---|
| C | 0.125% | 0.11% |
| Notional → qty | $153.8 → 1.66 | $155.28 → **1.67** ($154.48) |
| Leverage | 22x | **22x** (floor(1/0.045)) |
| Margin | ≈ $6.98 | **$7.02** |
| Liq | ≈ $89.22 (2.0% beyond SL) | **$89.19** (2.08% beyond the $91.11 SL) |
| Net reward | ≈ $7.66 | **$7.69** (after $0.02 maker, $0.07 taker, $0.09 funding over 48h) |
| Loss at SL | $2.50 | **$2.49** (within 0.5% of R) |
| Net R/R | 1:3.07 | **1:3.09** |

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
- **3 consecutive SLs:** a 48h pause, then you must send `review` before trading again. "Consecutive" counts SL
  exits since the last TP; time stops and discretionary exits don't reset the streak (the stricter reading).
- **Equity 30%+ below peak:** half risk until back within 15% of peak.
- **Equity 50%+ below peak:** stop for 7 days, then `review`, then restart only on a new `version` in
  `protocol.toml`.
- **Max 2 new entries per UTC day:** 1 in the BTC half-risk band, with no re-entries into a coin traded today.
- **Paper mode:** breakers count from the last `/paperreset`.

**Overrides.** `override: [reason]` takes the closest candidate that failed **soft** rules only (for example news,
whale, OI, direction count, confluence, macro release, cooldown). The card shows `OVERRIDE` and the rules it
bypasses. The trade is logged with the reason, and overrides get their own line in every review. Hard rules
(Module 13), sizing and missing data can never be overridden.

## Module 9 — Trade management

- **Entries:** Mon 00:00 – Thu 23:59 UTC only.
- **Friday:** unfilled entries cancel at Fri 00:00 UTC, and every position closes by Fri 20:00 UTC.
- **Time stop:** close at market 48h after the fill.
- The bot never moves the SL, adds to a position, or removes TP/SL. `/cancel` touches resting entries only.
- **Early-exit suggestions** (objective triggers only; the bot alerts and never acts):
  - BTC ≤ −4% for longs or ≥ +4% for shorts
  - a CPI, FOMC or NFP release inside the remaining hold
  - thesis-breaking news on the coin (Claude reads new headlines hourly)
- **Discretionary exits** (`/close COIN [reason]`) are always allowed and logged as `discretion`. Once the time
  stop has passed, the bot records what the bracket would have made (`bracket_r`). After 10 discretionary exits,
  if discretion trails the bracket, `/close` requires a reason.

## Module 10 — Execution checklist (after `y`)

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
| exit price / exit type | `exit_px`, `exit_type` (tp, sl, time, discretion, friday, mismatch, liquidated) |
| result $ / R | `pnl` (after fees and funding), `result_r` |
| MAE / MFE | `mae_r`, `mfe_r` (from 5m candles while open) |
| fees / funding paid | `fees`, `funding` |
| override / bracket comparison | `override` (reason), `bracket_r` (what the bracket would have made after a discretionary exit) |
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
- discretionary exits vs the bracket, and override PnL
- average MAE, MFE on losers, and how many losers went green first
- the skip outcomes
- **recalibrated risk** = ½ × (p − (1 − p) ÷ b) over the last 30 trades, capped at 15%. Under 2% means no edge:
  stop and rework the screener.

## Module 12 — Autopilot and commands

`start ($X)` runs, in order:

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
| `start ($X)` | Run the flow. `$X` is your equity; the API figure is checked against it |
| `y` / `y 1` / `y 2` | Execution checklist (1 = long, 2 = short when both are shown) |
| `nah` / `skip` / `try another` | Re-sweep fresh candidates, max 2 per session (UTC day), then "No trade today" |
| `n` | Discard the card |
| `how is it going` | Price, uPnL in $ and R, distance to TP/SL, funding paid, time left |
| `review` | Module 11 stats |
| `risk X%` | Set risk within the 15% ceiling |
| `override: [reason]` | Closest soft-blocked candidate (Module 8) |
| `/status` `/journal` `/close COIN [reason]` `/cancel` `/lessons` `/approve N` `/reject N` `/pump` `/paperreset N` | Utilities |

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
- no order without your explicit `y`. The only exceptions are protocol cancels and closes: time stop, Friday
  flatten, cancel triggers and mismatch handling.

## Confirmation card

A real card from the end-to-end test (`tests/test_pipeline.py`, paper mode, crafted market). The `!` lines are
cautions; the indented lines are the data behind the card.

```
Equity $100.00 (confirmed 12:00 UTC) | risk 10% | breakers: none active
BTC gate: +1.01% from the UTC open -> NORMAL
Fear & Greed: 62 (Greed)

# Open Limit Order - Protocol v3.0 (Hyperliquid) - PAPER
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
Net @ TP:    ~$37.68 (+37.7% of equity)
Net R/R:     1:3.77 OK
Costs:       fees ~$0.39 (base tier (paper)) | funding ~$0.37 (48h)
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
Type "y" to confirm.  (nah / skip / try another = re-sweep, n = discard)
Card expires in 10 min.
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

None of the hard rules has been changed. Where data is missing, the protocol's own rule applies (missing data =
FAIL), even where that blocks trading. Each item below needs your decision.

| # | Rule | Problem | Current behaviour | Proposed substitute |
|---|---|---|---|---|
| 1 | Unlock auto-skip (M2) | no free unlock feed | **every coin fails** until `calendar.toml` [unlocks] covers the hold + 24h | maintain `calendar.toml` weekly, or wire a paid API (e.g. Tokenomist) — **Q1** |
| 2 | TP/SL attached at entry (M6/M13, **hard**) | Hyperliquid activates `normalTpsl` only after a full fill | a partial fill can be unprotected for up to ~20s until position TP/SL is placed | subscribe to the user-fills websocket so protection lands within a second — **Q12** |
| 3 | Whale L/S (M3/M5) | no native top-trader ratio | top-60 leaderboard accounts' positions, notional-weighted, N/A under 5 holders | keep, or mark N/A (= fail, so longs need the other 4 triggers) — **Q2** |
| 4 | Crowd L/S (v2.1 chunk 9) | no all-accounts ratio | always N/A; whale data used instead | drop |
| 5 | Buy/sell ratio | no ratio endpoint | taker flow from the trade stream; N/A for the first 30 min after a restart (v1's order-book fallback removed: it measured something else) | keep — **Q3** |
| 6 | OI 24h delta / 30d growth | no OI history endpoint | own hourly snapshots: **OI 24h fails for the bot's first 24h** (hard filter); 30d growth N/A for 30 days (ranking only) | accept, or a paid history source — **Q14** |
| 7 | News "2+ of 3" | free RSS covers majors well and many alts thinly | fewer than 2 rated articles fails the filter; none = N/A (hard) | add a news API (needs a key) — **Q13** |
| 8 | Scheduled catalysts (listing/delisting, votes, upgrades) | no calendar feed | Claude flags them from headlines; delisted coins drop out of Hyperliquid's universe | accept |
| 9 | Macro calendar (CPI/FOMC/NFP) | no free API | `calendar.toml`, prefilled to 31 Dec 2026 (the FOMC date needs checking); coverage gap = FAIL | maintain manually — **Q16** |
| 10 | "Check Bybit's actual liq price" | — | Hyperliquid's `liquidationPx`; margin added automatically (mismatch handling) | done |
| 11 | Manual entry fallback (M10) | — | the card already lists every field to enter in the app | done |

## Changelog

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

## Open questions

The first three block or shape live trading.

1. **Q1 — Token unlocks (blocks all trades today).** With no unlock data every coin fails Module 2. Options:
   - maintain `calendar.toml` yourself
   - pay for an unlock API
   - accept Claude's headline check as the unlock source (a weaker substitute that changes how "missing data" is
     applied, so it needs your OK)
2. **Q12 — Partial fills vs "TP/SL attached at entry" (hard rule).** Do you accept a gap of up to ~20s, or should
   I add the fills websocket (recommended)?
3. **Q14 / Q13 — Data that starts empty.** OI 24h fails for the first 24h after deploy. News is thin for many
   alts. Accept, or add paid or keyed sources?
4. **Q10 — Shorts.** As written, the whale auto-skip (< 1:1) plus the mirrored whale filter (≤ 0.67) make shorts
   impossible, and neither sweep looks for sell-side candidates. Options:
   - keep it long-only in practice
   - make the whale skip and filters direction-aware
   - add a sell-flow sweep
5. **Q11 —** Long and trend-short both qualify: skip (current) or show both?
6. **Q4 —** Entry confluence: all 4 methods (current, strict) or 3 of 4?
7. **Q5 —** Structural 4H levels: swing points, volume profile, weekly Fibonacci (current). Add or remove any?
8. **Q2 —** Does top-60 leaderboard positioning count as "true whale" data?
9. **Q7 —** Undefined magnitudes. Defaults:
   - OI flat ±1%, OI rising +2%
   - stall < 0.5% (last 4H)
   - long ratio climbing +10% vs 24h
   - OI dropping sharply ≤ −5% on a ≥ +3% day
   - strong pump ≥ +5%
10. **Q6 —** Volatility bands LOW < 25 / HIGH ≥ 75 (only EXTREME ≥ 85 is in the protocol).
11. **Q8 —** Keep the $50M / $100M volume screens on Hyperliquid, or rescale them?
12. **Q9 —** Micro phase below $6, raising risk only as far as the $10 minimum needs. OK?
13. **Q15 —** Challenge equity: main account or a dedicated sub-account (recommended)?
14. **Q16 / Q17 —** Macro calendar maintained by hand. F&G (a daily index) judged stale by fetch time, not by its
    once-a-day timestamp. OK?
15. **Q18 / Q19 —**
    - Half risk with a custom risk: min(5%, risk / 2). OK?
    - Net reward excludes slippage at TP, as the formula is written; v2.0's own $7.66 example looks like it
      subtracted slippage. Which one?

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
- Hyperliquid run: starts from your first deposit.
- Milestones: $6 (micro phase ends) → $10 → $50 → $100 → $250 → $500 → $1,000.
- Optional vault rule: at $100, $250 and $500, move 20% of equity to a separate sub-account.
