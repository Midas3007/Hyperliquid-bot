# Nov 12 review: smart sizing week, the dip idea, and a $1,500 pool

Read-only. Nothing in the bot was changed. Data: every USDT move of Midas's wallet and of Mel's wallet, and all
137,538 Uniswap swaps in the pools they use. Data runs to 12 Nov 11:00 UTC. Trades are counted the way `/today`
counts them (`profit`, gas top-ups inside) unless a table says "ex gas". All times are UTC unless marked Lagos
(Lagos = UTC+1).

## Short answers

1. **Smart sizing week:** Midas made $31.94 a day (ex gas) after the switch, up 77% from $18.03. Mel, who never
   had smart sizing, went from $9.89 to $15.87 (up 60%). The gap between those two rises is almost all one day
   (10 Nov). Without that day both rose by about $5 a day. Per dollar traded, nothing clearly changed. The bigger
   peak size was never used, because the wallet was too small. The filter on thin trades did work.
2. **"Dips create gaps":** cannot tell yet. Hour by hour, bot profit has no real link to whether prices fell. There
   is a weak link to how big the moves were, and one strong day (10 Nov). Not enough data to call it.
3. **$1,500 pool:** `MAX_TRADE_USD` 700, `SMART_PEAK_MAX_USD` 1,000, and the peak minimum edge from 0.3 to 1 bp.
   Patch in `kiyoshi/docs/sizing-1500.patch` (not applied).

One finding to check today: since 6 Nov 22:05 UTC the bot's trade sizes follow **90%** of the wallet, not the 80%
that the log and `render.yaml` say. See "What changed at the same time".

---

## Part 1. The smart sizing week

**Answer first.** Smart sizing was switched on at 5 Nov 09:12:18 UTC. Period A is before that, period B after it.
Midas earned more per day in B, but so did Mel, and the extra is mostly more money in the wallet plus one
unusual day. I cannot credit the schedule for the increase. I also see no sign that it hurt. Keep it on.

### Midas and Mel, before and after

Period A: first trade to 5 Nov 09:12 (Midas from 29 Oct 14:07, Mel from 29 Oct 11:39). Period B: 5 Nov 09:12 to
12 Nov 11:00.

| | Midas A | Midas B | Mel A | Mel B |
|---|---|---|---|---|
| Days covered | 6.80 | 7.07 | 6.90 | 7.07 |
| Trades | 1,431 | 1,713 | 1,333 | 2,116 |
| Trades per day | 210.6 | 242.1 | 193.3 | 299.1 |
| Profit per day | $17.11 | $30.88 | $9.89 | $15.87 |
| Profit per day, ex gas | $18.03 | $31.94 | $9.89 | $15.87 |
| Traded in total | $298,178 | $499,190 | $223,619 | $389,265 |
| Profit per $1,000 traded | 4.11 bps ($0.41) | 4.53 bps ($0.45) | 3.05 bps ($0.31) | 2.88 bps ($0.29) |
| Winning trades | 77.2% | 77.9% | 74.1% | 73.3% |
| Incompletes (`uni>uni`) | 10.1% | 11.3% | 10.2% | 10.3% |
| Average trade size | $208 | $291 | $168 | $184 |
| Median trade size | $210 | $264 | $195 | $150 |

A "bp" (basis point) is one hundredth of a percent. 4 bps is $0.40 on $1,000. Totals add up to the verified
$334.70 (Midas, 116.26 + 218.44) and $180.48 (Mel, 68.22 + 112.26). Midas's "profit" includes $6.23 (A) and $7.50
(B) of gas top-ups; Mel shows none.

### Difference in differences, in plain words

| Change from A to B | Midas | Mel | Midas minus Mel |
|---|---|---|---|
| Profit per day, ex gas | +$13.91 (+77%) | +$5.98 (+60%) | +$7.93 |
| Same, leaving out 10 Nov | +$5.00 (+28%) | +$5.19 (+52%) | about $0 |
| Profit per $1,000 traded | +0.42 bps | -0.17 bps | +0.59 bps |
| Same, leaving out 10 Nov | -0.49 bps | -0.46 bps | about 0 |
| Trades per day | +15% | +55% | |
| Average size | +40% | +10% | |
| Winning share | +0.7 pt | -0.8 pt | +1.5 pt |
| Incompletes | +1.2 pt | +0.1 pt | +1.1 pt (worse) |
| Trades under 3 cents | 47.8% to 33.7% | 52.4% to 56.0% | -17.7 pt |

How sure am I?

- Split into 24-hour blocks from the switch, the daily profit change is not significant. Midas minus Mel went
  from $5.69 a day (6 blocks) to $16.25 (7 blocks). Welch test p = 0.37, rank test p = 0.45.
- For profit per $1,000 traded, a bootstrap over days gives +0.63 bps with a 95% range of -0.7 to +1.6. That
  includes zero.
- 10 Nov (UTC) was Midas +$86.01 and Mel +$20.63 (ex gas). That day Mel's trades were capped at about $100 (his
  average was $91), Midas's averaged $324. Part of that day is a real burst in the market (Part 2), part is Mel
  being held back. It is not a clean test of anything.

### What smart sizing actually changed

- **The bigger peak size never happened.** Midas's largest first leg is 90% of the wallet. In B the wallet was
  $372 to $590, so the largest trade was $531 and the largest peak trade was $512. The caps ($500, later about
  $600 in the base, $750-800 in the peak) were never reached. So the week tested the minimum-edge schedule and
  nothing else about size.
- **The thin-trade filter worked.** Trades under 3 cents fell from 47.8% to 33.7% of Midas's trades. Mel's rose.
  Trades that realised under 1 bp: Midas 36.0% to 29.7%, Mel 37.1% to 40.0%.
- **Bigger trades make failures cost more.** Incompletes cost Midas -$10.98 in A (-$1.62 a day) and -$26.30 in B
  (-$3.72 a day). Mel: -$5.79 and -$14.01. Per dollar traded: Midas -3.67 to -4.23 bps, Mel -2.57 to -3.20.

### By schedule bucket (Lagos clock)

Profit is ex gas. "Per hour" divides by the clock hours of that bucket in the period, so it compares fairly.

| Bucket, period | Hours | Midas trades | Midas $ per hour | Midas bps | Mel $ per hour | Mel bps |
|---|---|---|---|---|---|---|
| Peak A | 12 / 14 | 186 | 3.61 | 10.17 | 1.54 | 7.21 |
| Peak B | 14 | 205 | 1.11 | 3.13 | 0.91 | 3.14 |
| Busy A | 33 | 370 | 1.09 | 4.53 | 0.50 | 3.13 |
| Busy B | 36 | 398 | 1.30 | 3.93 | 0.69 | 2.73 |
| Quiet A | 118 | 875 | 0.36 | 2.45 | 0.25 | 2.13 |
| Quiet B | 120 | 1,110 | 1.37 | 4.95 | 0.62 | 2.90 |

(Peak A hours: Midas 12, Mel 14, because Midas's wallet only started trading on 29 Oct at 14:07 UTC, after that
day's peak.)

| Bucket | Winning trades A to B (Midas) | Incompletes A to B (Midas) | Average size A to B (Midas) |
|---|---|---|---|
| Peak | 83.9% to 75.6% | 6.5% to 10.7% | $229 to $242 |
| Busy | 77.3% to 74.1% | 10.0% to 12.3% | $215 to $299 |
| Quiet | 75.8% to 79.6% | 10.9% to 11.0% | $201 to $298 |

Reading it:

- **Peak got much worse for both bots.** Midas $3.61 to $1.11 an hour (-69%), Mel $1.54 to $0.91 (-41%). The
  peak is 34% of profit in A (both bots together) and 8% in B. In A, two Lagos days did most of it: Midas made
  $17.09 on 30 Oct and $19.24 on 2 Nov in the peak, out of $43.32. The gaps of those two days (about 15 to 19
  bps) did not come back.
- **Quiet got much better.** Midas $0.36 to $1.37 an hour, Mel $0.25 to $0.62. 10 Nov is a big part of it.
- **Busy** is about the same for both.

### By route

| Route | Bot | Trades A to B | Profit A to B | bps A to B | Winning % A to B |
|---|---|---|---|---|---|
| Uniswap then Textile | Midas | 858 to 826 | $96.58 to $174.39 | 5.41 to 6.82 | 92.0 to 93.2 |
| | Mel | 732 to 962 | $56.88 to $91.86 | 4.58 to 5.07 | 91.5 to 92.1 |
| Textile then Uniswap | Midas | 428 to 693 | $36.56 to $77.91 | 4.08 to 4.30 | 70.6 to 79.5 |
| | Mel | 460 to 932 | $17.26 to $33.33 | 2.28 to 2.04 | 65.9 to 68.0 |
| Incomplete (`uni>uni`) | Midas | 144 to 193 | -$10.98 to -$26.30 | -3.67 to -4.23 | 9.0 to 6.7 |
| | Mel | 136 to 218 | -$5.79 to -$14.01 | -2.57 to -3.20 | 10.3 to 12.4 |

Eleven trades that were Textile on both legs (`tex>tex`) are in the totals but left out of this table. Textile
then Uniswap is the one place Midas improved clearly against Mel (winning share +8.9 pts against +2.2). That is
consistent with the thin-trade filter, but I cannot separate it from the "skip if Textile still holds funds" rule.

### What changed at the same time

| What | When | Effect on the comparison |
|---|---|---|
| Wallet grew | Wallet $372 (5 Nov) to $590 (12 Nov), which equals B profit of $218. I see no deposits or withdrawals in B. The only incoming transfers from the fund address are $101.95 (29 Oct), $21.21 (2 Nov), $16.26 (3 Nov), all in A | Midas's sizes rose 40% because the wallet did, not because of the schedule |
| `FIRST_TRADE_SHARE` | Read from the chain (trade size / wallet): 0.9 until 5 Nov 11:41, 0.6 until about 14:08, 0.8 until 6 Nov 22:05, **0.9 from then on** | In B the share was 0.9 for 80% of the hours, 0.8 for 19%, 0.6 for 1%. So it muddies less than the log suggests. The repo's `render.yaml` still says 0.8 |
| Scan interval | 60 s to 20 s to 30 s on 5 Nov (from the issue log; not visible on chain) | Cannot be isolated |
| Mel's own size | Mel's trades were $195 until 5 Nov, about $300 from 6 Nov, about $100 on 10 Nov | The control changed too |
| Market | Peak gaps thin in B for both bots, quiet-hour gaps wider, one burst on 10 Nov | Hits both bots, but Midas's sizes make it bigger in dollars |
| Midas's start | Midas has no trades before 29 Oct 14:07 | A is 6.80 days, not 7 |

### The earlier baseline, recomputed

The log says the baseline "reads high" because `wallet-stats.ts` drops losing trades. With the `/today` method on
the days I have (29 Oct to 5 Nov 09:12, so not 27-28 Oct):

| | Baseline (PDF) | Recomputed |
|---|---|---|
| Midas profit per day | ~$13.5 | $17.11 (ex gas $18.03) |
| Mel profit per day | ~$9.5 | $9.89 |
| Midas profit ÷ biggest trade, 1-4 Nov | 5.3% (avg max $303) | 5.3% ex gas, 5.0% with gas ($15.99 and $15.24 a day over $304) |
| Mel same | 6.8% ($195) | 6.8% ($13.33 a day over $195) |
| Trades under 3 cents, Midas / Mel | 846 / 868, -$19.94 / -$17.18 | 684 / 698, -$19.69 / -$15.97 (A only, so fewer days) |
| Failed cycles, Midas / Mel | 173 / 163, -$11.27 / -$7.00 | 144 / 136, -$10.98 / -$5.79 (A only) |
| Peak share of profit, both bots | 37% | 34.0% |

So: the **headline per-day figures did not read high**. For Midas the PDF number is lower than mine; for Mel it is
close. The 1-4 Nov ratio, the thin-trade losses, the failed cycles and the peak share all reproduce, allowing for
a shorter window. I cannot recompute the 1,887 / 1,820 trips or the $13.5 / $9.5, because the chain data I hold
starts at 29 Oct and the PDF window starts on 27 Oct (I do not know its exact hours). The "reads high" problem is
real for the single day in the log (5 Nov: $76.65 against $27.30); my 5 Nov (UTC day) is $26.91 for Midas and
$23.58 for Mel with gas inside, close to the log's $27.30 and $22.70.

---

## Part 2. "Huge dips in BTC and altcoins create gaps"

**Answer first. Cannot tell yet.** Over 14 days the hours when prices fell were not hours when the bots earned
more, once you allow for the time of day. Big moves in either direction go with more swaps in the pools, mostly
in the CELO pools, which is expected because CELO is itself a price that moves. For the two currency pools the
bots trade (wARS and cNGN) the link is weak. One strong day (10 Nov) fits the idea. Other big drops do not.

### Price source and its limits

All four exchange APIs were blocked by the sandbox proxy (HTTP 403 on data-api.binance.vision, api.binance.com, Coinbase and Kraken).
So I used **on-chain Celo prices** read through forno, at the top of each hour (one block per second, so the
block is known exactly). I checked each token with `symbol()` and `decimals()`:

| Asset | Pool read (`slot0`) | Check |
|---|---|---|
| BTC | WBTC (0x8aC2...5B2D, 8 decimals) / USDT, 0.3% pool | 14-day move -8.0% |
| ETH | WETH (0xD221...D9Af, 18 decimals) / USDT, 0.01% pool | -12.0% |
| CELO | CELO / USDT, 0.01% pool (the bots' own pool) | -18.5% |

Limits: these are pool prices, not exchange prices. They lag an exchange by however long arbitrageurs take, they
move in steps, and the BTC pool is thin. A second ETH pool (0.3%) agreed in direction but showed no change in 23%
of hours, so it is stale at times. On average ETH rises 1.8% in the 23:00 to 00:00 UTC hour and falls 1.8% in the next
(CELO +1.5% and -1.6%), every day; I cannot tell if that is real or a pool habit. Taking out each hour's average
handles it for correlations. I could not check any price against an exchange.

### The series

332 hourly points, 29 Oct 15:00 to 12 Nov 10:00 UTC (starting after Midas's first trade). For each hour: trades
and profit (ex gas) of each bot, by the hour of the first buy; and swaps and USD volume in the five pools from the
`swaps` table, **leaving out the two bots' own swaps**. "FX pools" means the wARS and cNGN pools only.

### Correlation results (Spearman unless noted; n = 332)

Return is the price change during the hour. "Abs" is its size, ignoring direction.

| Bot profit or pool activity vs | Same hour, raw | Same hour, after removing time-of-day |
|---|---|---|
| Midas profit vs BTC return | Pearson -0.06 (p 0.31) | Spearman -0.08 (p 0.14) |
| Mel profit vs BTC return | Pearson -0.01 (p 0.79) | Spearman -0.01 (p 0.88) |
| Midas profit vs abs BTC | Pearson +0.08 (p 0.13) | Spearman +0.18 (p 0.001) |
| Mel profit vs abs BTC | Pearson -0.01 (p 0.91) | Spearman +0.05 (p 0.37) |
| All pool swaps vs abs BTC | +0.53 (Pearson), +0.33 (Spearman) | +0.41 (Spearman) |
| FX-pool volume vs abs BTC | Pearson +0.11 (p 0.05) | Spearman +0.18 (p 0.001) |
| FX-pool volume vs BTC return | Pearson -0.08 (p 0.17) | Spearman -0.04 (p 0.44) |

What this says:

- **Direction does not matter.** Profit against the signed return is near zero in every version (|r| of 0.09 or
  less at the same hour, for both bots, against BTC, CELO and the BTC-ETH average). If dips created gaps, profit and
  return would be clearly negative at lag 0 and a few hours after. They are not.
- **Size of move matters a little, and only for Midas.** Midas's profit and trade count rise with absolute moves
  (Spearman +0.18 to +0.23 at lags 0 to 2 once the time of day is removed). Mel shows nothing. Midas trades
  larger, so a few big hours carry more dollars. I would not read much into it.
- **Lag +6 is the one odd number.** Six hours after a BTC drop, bot profit is a little higher: both bots
  combined Spearman -0.17 (p 0.002). The lags next to it are about zero (-0.01 to -0.06 at +7 to +9), so it looks
  like one lucky lag out of many, not a pattern. Of 416 tests on the bots, 50 reach p < 0.05 by Spearman with
  raw series (about 21 expected by luck) and 77 after removing time-of-day. Tests overlap, so treat the counts as
  a warning, not proof.
- **Pool activity does follow big moves.** All-pool swap counts have +0.53 with absolute BTC moves. That is mostly
  the CELO pools (arbitrageurs rebalancing as CELO moves), so it is not evidence for currency gaps.

### Event study: the 10 biggest drops

I took the 10 largest one-hour drops and the 10 largest four-hour drops (events at least 6 hours apart), and added
up each bot's profit in the 6 hours after. The comparison is the same 6 clock hours on every other day.

| Drop series | Midas after, vs other days | Mel after, vs other days | Midas p (permutation) |
|---|---|---|---|
| BTC, 1 hour | $10.38 vs $4.29 (2.4x), 5 of 10 above | $2.88 vs $2.10 (1.4x), 4 of 10 | 0.035 |
| BTC, 4 hours | $9.59 vs $5.12 (1.9x), 5 of 10 | $2.15 vs $2.94 (0.7x), 3 of 10 | 0.077 |
| ETH, 1 hour | $10.36 vs $3.77 (2.7x), 5 of 10 | $3.16 vs $1.74 (1.8x), 5 of 10 | 0.026 |
| CELO, 1 hour | $4.89 vs $4.68 (1.0x), 4 of 10 | $2.32 vs $2.02 (1.1x), 4 of 10 | 0.40 |

Per event (BTC, 1 hour), 6-hour profit after versus the same hours on other days:

| Drop hour (UTC) | BTC move | Midas | Mel |
|---|---|---|---|
| 5 Nov 15:00 | -3.04% | $9.65 vs $8.57 | $7.46 vs $3.68 |
| 29 Oct 20:00 | -2.27% | $10.13 vs $4.84 | $0.00 vs $2.47 |
| 8 Nov 21:00 | -2.18% | $0.00 vs $4.56 | $2.61 vs $1.97 |
| 10 Nov 15:00 | -1.16% | **$59.12 vs $4.77** | **$13.75 vs $3.20** |
| 12 Nov 00:00 | -1.15% | $3.12 vs $2.57 | -$0.08 vs $1.11 |
| 10 Nov 00:00 | -1.12% | $2.15 vs $2.65 | $0.50 vs $1.07 |
| 11 Nov 00:00 | -0.95% | $15.59 vs $1.61 | $2.60 vs $0.90 |
| 30 Oct 18:00 | -0.86% | $2.58 vs $7.56 | $1.71 vs $3.53 |
| 7 Nov 00:00 | -0.78% | $1.08 vs $2.73 | -$0.48 vs $1.14 |
| 4 Nov 03:00 | -0.61% | $0.35 vs $3.08 | $0.68 vs $1.91 |

The Midas result is one event. Leave out 10 Nov and Midas is $4.96 against $4.24 (1.2x); Mel is $1.67 against
$1.98. 10 Nov is real, though: ETH fell 2.8% and BTC 1.2% in the hour 15:00 to 16:00; in the hour after, the two
FX pools saw $20,000 of outside volume, the most of the season (about 10 times the average hour), and both
bots earned several times their usual amount ($19.73 for Midas, $6.01 for Mel in 16:00 to 17:00). The biggest
drop of the season (5 Nov 15:00, BTC -3.0%) was followed by an ordinary 6 hours for Midas (1.1x) and about 2x for
Mel.

### Verdict

**Cannot tell yet.** One clean example (10 Nov), several big drops with nothing special after, no link to the
direction of price in hour-by-hour data, and a weak link to the size of moves. Fourteen days, about 330 hours, one
market that went down 8 to 18% the whole time: that is too little to separate "dips make gaps" from "busy hours
make gaps". What would settle it: (1) real exchange prices (BTC, ETH, and the local currencies against the dollar:
NGN, ARS), which this sandbox could not reach; (2) three or four more weeks, with at least one fast rally and one
quiet week; (3) a log of the gap itself (Uniswap price against Textile's quote every minute, for each pool),
instead of the trades the bots happened to make; (4) bot settings held steady, so size changes do not mix in.

---

## Part 3. Settings for a $1,500 pool

**Answer first.** Raise `MAX_TRADE_USD` to **700** and `SMART_PEAK_MAX_USD` to **1,000**. Raise the peak minimum
edge from 0.3 to **1 bp** (floor $0.02). Leave the busy (1 bp, $0.02) and quiet (1.5 bps, $0.03) minimums as
they are. Do not go above 800 for the base or about 1,000 for the peak yet.

### How the caps are applied (from the code)

- `schedule.ts`: busy and quiet use `MAX_TRADE_USD` as the cap. Peak uses `MAX_TRADE_USD` times 2 (Mon, Fri), 1.5
  (Tue to Thu) or 1.25 (weekend), limited by `SMART_PEAK_MAX_USD`.
- `desk.ts` / `strategy.ts`: first trade = the smaller of 90% (or `FIRST_TRADE_SHARE`) of the wallet and the cap.
  The bot then tries that size, half of it and a quarter (the "ladder"), and takes the one with the best expected
  profit, if it beats the minimum edge for its size.
- Minimum edge = the larger of the dollar floor and (bps x size). After a Textile-first trade Textile holds that
  USDT for about 2 minutes, and a new Textile-first trade must fit in what is left of the wallet.

With the wallet at $590 today, 90% is $531. That is below every cap, so **the cap has never been what limited a
trade in B**: the wallet was. The new numbers only start to bind when the wallet passes about $780 (700 / 0.9) for
busy and quiet hours and about $1,110 for the peak. Setting them now is harmless.

### What the data says

**Price impact is real and measured.** I measured how much each Uniswap swap moved the price against the pool's
price just before it (all swaps, 12,509 in the wARS pool and 1,436 in the cNGN pool):

| Pool | Cost at tiny size | Extra cost per $100 | Check |
|---|---|---|---|
| wARS / USDT | 1.0 bp (the pool fee) | **0.58 bps** | se 0.002 |
| cNGN / USDT | 1.0 bp | **0.55 bps** | se under 0.001 |
| USDT / USDC | 1.0 bp | 0.01 bps | nearly no impact |

That matches the 0.55 bps per $100 from your earlier simulation. A trade of size S that sees a gap of g bps earns
about S x (g - 0.57 x S / 100). The best size is about **$88 for each 1 bp of gap**: 4 bps of gap wants $350,
8 bps wants $700, 15 bps wants $1,300.

**What gaps looked like** (implied gap of Midas's completed trades):

| Bucket | A: typical gap, best size | B: typical gap, best size |
|---|---|---|
| Peak | 12.1 bps, about $1,000 | 5.3 bps, about $400 |
| Busy | 6.5 bps, about $530 | 6.9 bps, about $530 |
| Quiet | 4.1 bps, about $350 | 7.5 bps, about $600 |

(Best size here is the size that maximises profit if all of that week's trades were scaled together.) Peak days
vary a lot: 30 Oct best size about $1,700, 2 Nov about $1,300, most other days $300 to $500.

**Raw profit by trade size cannot show the effect.** Midas's profit in bps goes 4.1 (under $100), 2.6 ($100-200),
4.9 ($200-300), 3.4 ($300-400), 5.9 ($400-531). Larger trades are only taken when gaps are larger, and the
biggest ones come from the high-wallet, high-gap days. So I used the measured impact above, not this table.

| Size (Midas) | Trades | bps | Losing trades | Incompletes | Average win / loss |
|---|---|---|---|---|---|
| Under $100 | 233 | 4.14 | 18.5% | 8.2% | +$0.04 / -$0.02 |
| $100-200 | 753 | 2.60 | 26.0% | 11.8% | +$0.07 / -$0.05 |
| $200-300 | 1,100 | 4.87 | 19.2% | 7.3% | +$0.16 / -$0.10 |
| $300-400 | 649 | 3.39 | 25.6% | 14.5% | +$0.20 / -$0.14 |
| $400-531 | 407 | 5.90 | 21.6% | 13.5% | +$0.41 / -$0.21 |

Incompletes are somewhat more common above $300 (14% against 7 to 12%). Losses grow in step with size. A failed
Uniswap-first trade costs about 3.8 bps on average (mean of 337 incompletes), so about $0.38 at $1,000.

**How often the wallet, not the setting, limited the size.** In B: every time. 90% of the wallet (80% or 60% for
short spells) never reached the cap. Since 6 Nov 22:05, Midas used the full size in 48% of trades, half size in
39%, quarter size in 13% (586 / 481 / 158 trades). Their bps: 5.0, 3.6, 4.4. Their incomplete rates: 15.5%,
9.6%, 7.6%. In period A I cannot say: the base cap was about $330 (log) and 90% of the wallet was about $335 by the end, so the two were close and moved during the week.

**Same-clock test of the caps (with the ladder).** I replayed last week's and the week before's Midas trades as
if the wallet were $1,500, with the bot choosing a size from the ladder using only that day's typical gap, and the
impact above. Model profit in $ per day at a $1,500 wallet (A = 29 Oct to 5 Nov, B = since):

| Cap | Peak A | Peak B | Busy B | Quiet B |
|---|---|---|---|---|
| 400 | 9.78 | 2.64 | 7.79 | 26.63 |
| 600 | 12.92 | 2.47 | 8.59 | 31.27 |
| 700 | 14.35 | 2.30 | 8.82 | 31.84 |
| 800 | 15.74 | 2.38 | 9.26 | 31.84 |
| 1,000 | 17.54 | 2.57 | 9.33 | 32.05 |
| 1,200 | 18.49 | 2.47 | 8.60 | 31.71 |

Reading it: from $600 up the busy and quiet lines are flat, so there is nothing to gain by a high base cap and a
small risk in one. The peak is flat in B (thin gaps) and keeps rising to about $1,000 in A (wide gaps), so a high
peak ceiling is cheap insurance for the wide days. This model assumes Textile quotes the same gap at those sizes.
I have no data above $531 to check that.

A stricter version (every trade simply scaled up with the same size choice) gives a lower best size: about $750
for all trades together, and falling after $800. That is why I do not go past 700 for the base.

### Recommended values

| Setting | Now | Recommended | One line of reasoning |
|---|---|---|---|
| `MAX_TRADE_USD` (Render) | 600 | **700** | Start of the flat part of the curve. Also 700 + 700 fits in $1,500, so a follow-up trade fits while Textile holds the first (2 x 750 is the limit) |
| `SMART_PEAK_MAX_USD` (Render) | 800 | **1,000** | Wide-gap peaks wanted $1,000 to $1,700. The ladder drops to $500 or $250 on thin days. A $1,000 Textile-first trade leaves $500 free for a follow-up |
| Peak minimum edge (`schedule.ts`) | 0.3 bps, $0.01 | **1 bp, $0.02** | A failed Uniswap-first trade (17% of them) costs 3.8 bps, so the edge needed to break even is about 0.8 bps before any slippage from preview to firm quote |
| Busy minimum edge | 1 bp, $0.02 | unchanged | Already at break-even plus margin |
| Quiet minimum edge | 1.5 bps, $0.03 | unchanged | Same |
| `PEAK_MULTIPLIER` | 2, 1.5, 1.25 | unchanged | At 700 base the cap limits Mon to Fri to $1,000 and the weekend to $875 |
| `FIRST_TRADE_SHARE` | 0.8 in `render.yaml`, 0.9 on chain | leave | It does not matter below a $1,200 cap on a $1,500 wallet. Check which value Render has |

Set in Render's dashboard: **`MAX_TRADE_USD` = 700** and **`SMART_PEAK_MAX_USD` = 1000**. (`MAX_TRADE_USD` is
`sync: false` in `render.yaml`, so it lives only in the dashboard. `SMART_PEAK_MAX_USD` is in `render.yaml`;
the patch changes that line too, so a push does not sync it back to 800.)

The patch (`kiyoshi/docs/sizing-1500.patch`, not applied) changes `schedule.ts` (peak minimum edge), its test, the
`HOSTING.md` table row and `render.yaml`. I checked it applies cleanly in a dry run and that the five schedule
tests pass with it, on a copy.

### What I am unsure about

- **Textile's depth above $531.** I have no trade above that. Textile gave "no quote much above $100" for CELO
  (log, 6 Nov). Raise gradually and watch incompletes: if they pass about 20%, step down.
- **The minimum edge.** I do not have the expected edge the bot saw at decision time, only the result. The 1 bp
  peak figure rests on the break-even arithmetic. The stakes are small: peak trades that realised under 1 bp lost
  $7.72 in two weeks in total (A and B).
- **The model gain.** It says about $43 a day at $1,500 (peak 1,000, rest 700, B week) against $32 last week, most of that from the
  bigger wallet, not from tuning the caps. Treat the level as a guide. The shape (flat above $600, higher peak
  helps only on wide days) is the part I trust.
- **The 4-quote limit.** More, larger trades per two minutes may hit "max 4 outstanding quotes" more often. Not
  tested.
- **One week.** B had a market burst on 10 Nov. A quiet week could look different.

---

## What I could not establish

- Any exchange price (all four sources blocked by the proxy). Part 2 uses on-chain pool prices, which lag and
  are thin for BTC.
- Render's real settings. I read `FIRST_TRADE_SHARE` and sizes from the chain. I assumed `MAX_TRADE_USD` is 600
  and `SMART_PEAK_MAX_USD` is 800 now (from your note and `render.yaml`). I cannot see when `MAX_TRADE_USD` moved
  from $500 to $600.
- Midas's expected edge at decision time, skipped trades, and Render's error counts.
- Mel's settings (size cap, share). He changed size on 6 and 10 Nov, so he is an imperfect control.
- The 27-28 Oct days of the earlier baseline, and its 1,887 / 1,820 trip counts.
- I did not sync newer data: everything stops at 12 Nov 11:00 UTC so numbers match the verified totals.

## How this was measured

All files are in `/tmp/claude-0/-home-user/3a8d6223-ebc9-5af4-ae9c-0452ac68f60b/scratchpad/review/`.

- **Trades.** `01_views.sql` adds two views on top of `v_trades`: the time of the first buy (so a trade belongs to
  the hour it was started), the period (A before 1793869938000 ms = 5 Nov 09:12:18 UTC, B after), the Lagos hour
  and weekday, and the bucket (peak 13, 14; busy 0, 1, 9, 11, 12, 20, 21 on Mon to Fri; quiet otherwise).
  `size_ex` is the buy side without gas top-ups. `bps` = profit ex gas / size_ex x 10,000. Incomplete = path
  `uni>uni`. Winning = profit ex gas above 0.
- **Part 1.** `03_part1.py` (tables, bucket hours counted on the Lagos clock, 24-hour blocks from the switch,
  Welch and rank tests, 2,000-draw bootstrap by day). `04_baseline.py` (the baseline rows). Outputs `p1_*.csv`,
  `p1_out.txt`, `p1_baseline.txt`.
- **Share and wallet.** `bal_fetch.mjs` reads the wallet's USDT at the block before each trade's first buy
  (`eth_call` on forno at that block). `07_part3a.py` divides trade size by that balance: the clusters at
  0.9 / 0.45 / 0.225 (or 0.8 / 0.4 / 0.2, or 0.6 / 0.3 / 0.15) show the share in force.
- **Part 2 swaps.** `02_swaps_hourly.sql`: per hour, swap count and the USDT side of each swap in USD, for the
  five pools (USDT/USDC, wARS, cNGN, and two CELO/USDT pools), excluding swaps whose recipient is either bot.
  Pool tokens were confirmed with `pools.mjs`.
- **Part 2 prices.** `rpc.mjs`, `find.mjs` (factory `getPool`, `symbol()`, `decimals()`), `px_probe.mjs`,
  `px_fetch.mjs` (`slot0` at the block for each hour, block = 81382085 + seconds since 1793282843), `05_prices.py`
  (sqrt price to USD).
- **Part 2 tests.** `06_part2.py`: Pearson and Spearman at lags -6 to +6, raw and with each Lagos hour-of-day mean
  removed, for BTC, ETH, CELO and the BTC-ETH average; event study with the 10 largest 1-hour and 4-hour drops
  (6-hour sums against the same clock hours on other days; 20,000-draw permutation test with random hours).
  Outputs `p2_*.csv`, `p2_out.txt`, `hourly_bots_swaps.csv`, `hourly_returns.csv`. The lag 0 to +24 check is `12_lags24.py`
  (same Spearman, de-seasonalised, both bots).
- **Part 3.** `09_impact.py` (each swap's cost against the pool price just before it, regressed on size; pools
  wARS, cNGN, USDC). `08_part3b.py` (size buckets, loss and incomplete rates), `10_model.py` and `11_scale.py`
  (the stricter "scale every trade" version), `13_ladder.py` (the ladder replay with the day's typical gap,
  `p3_ladder.csv`). Model: profit = S x (g - 0.57 x S / 100) for a completed trade, with g = realised bps + 0.57 x
  S / 100; incompletes lose their realised bps times the new size.
- **Patch.** Built with `diff -ru` on copies in `pa/`, tested with `patch --dry-run -p1` on the repo and
  `tsx --test` on the copy.
