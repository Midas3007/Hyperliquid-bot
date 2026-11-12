# Priority gas vs both legs at once: which would earn more?

Measured on the chain for season 2 (29 Oct to 12 Nov, 11:00 UTC), read-only. Nothing in the bot was changed.
Data: every USDT movement of Midas's bot wallet and of Mel's, and all 137,538 swaps in the Uniswap pools both
bots trade. Trades are counted the way `/today` counts them.

## Short answer

1. **Priority gas: do not build it.** It can only change the order of transactions inside one block, and the bot
   is already at the front of its block. There is almost nothing for it to win.
2. **Both legs at once: only possible for half the trades, and it needs new on-chain code.** Worth about $1.50 a
   day. Not before the fund opens.
3. **A third option beats both: stop starting trades that cannot finish in profit.** The bot accepts a first leg up
   to 0.30% worse than the price it scanned, but its average winning margin is 0.064%. Tightening that one
   tolerance targets the same losses, needs no new contract, and can be tested on the phone with a second wallet.

## Where the money went

| | Midas | Mel |
|---|---|---|
| Trades | 3,144 | 3,449 |
| Profit before gas | **+$334.70** | +$180.48 |
| Profitable trades | 76.8% | 73.6% |
| Traded | $797,381 | $612,884 |

Midas by route (profit shown without the 28 gas top-ups, which cost $13.73 and are inside the +$334.70):

| Route | Trades | Profit | Losing trades | Lost |
|---|---|---|---|---|
| Buy on Uniswap, sell to Textile | 1,684 | +$270.98 | 125 | -$13.50 |
| Buy from Textile, sell on Uniswap | 1,121 | +$114.47 | 268 | -$21.50 |
| Bought on Uniswap, Textile leg failed, sold back on Uniswap ("incomplete") | 337 | **-$37.28** | 311 | -$38.41 |

Losses add up to about **$73 in two weeks**, roughly $5 a day against $24 a day of profit. That is the prize
any of these ideas is competing for. A winning trade makes 6.4 hundredths of a percent on average; a losing one
gives back 3 to 4.

## Priority gas

A higher tip only moves a transaction earlier **within its block**. Celo makes a block every second, so it
matters only when a competitor's swap lands in the same second, in the same pool, in the same direction.

- **Position in the block:** 1,485 of the bot's 3,479 Uniswap swaps were the first user transaction of their
  block (position 1; position 0 is always the chain's own bookkeeping transaction), and 3,044 (87%) were in the
  first three. There is rarely anyone to overtake.
- **Someone ahead in the same block:** 95 of 3,479 swaps (2.7%). Mel was that someone 64 times in two weeks.
- **Transactions that failed on the chain** (a race lost at the last moment): at most 33 of 6,349 sent (0.5%),
  and that 33 includes ordinary token approvals.
- **Being beaten by a second or more is what hurts, and a tip cannot fix that.** After buying on Uniswap, the
  trade ends incomplete 13% of the time when nobody else touched the pool in the last 30 seconds, and 20 to 23%
  when somebody did, whether that was in the same block or up to 30 seconds earlier.

Upper bound for priority gas: about 5 fewer incompletes in two weeks, well under a dollar a week, before paying
the higher tip on every transaction in the peak hours. Mel is not ahead because of gas; Midas out-earned Mel
over the season.

## Both legs at once (the 27 Oct idea)

One transaction that does both swaps and cancels itself if it would not end in profit.

- **Works for Textile-first trades.** Textile's firm quote can be requested up front, then one transaction fills
  it and sells on Uniswap, or reverts. That removes the 268 losing trades on that route: about **$21.50 in two
  weeks, $1.50 a day**, for a fraction of a cent of gas per cancelled attempt. A cancelled attempt still leaves
  Textile holding the quote for about two minutes, as today.
- **Does not work for Uniswap-first trades,** which is where most of the loss is ($37.28 of incompletes plus
  $13.50). Textile only gives a firm quote to sell a currency once the wallet already holds it, so the second leg
  cannot be known before the first is done.
- **Cost and risk:** it needs either a small contract of ours with authority over the bot wallet's USDT, or the
  wallet delegating to batch code (EIP-7702, to be confirmed on Celo with the bot's library). Either is new
  on-chain code in the path of members' money and would need the same review as the vault. Largest effort of the
  three.

## The better fix: match the tolerance to the edge

Today the first leg may fill up to **0.30%** worse than the scan showed (`SLIPPAGE_BPS = 30` in `desk.ts`), on
trades whose whole margin is about 0.06%. Two consequences, both visible in the data:

- **Textile-first:** the bot accepts a firm quote up to 0.30% under the preview, then sells on Uniswap at
  whatever the price is. The losing rate is highest (30%) exactly when **nobody** else traded the pool in the
  last 30 seconds, so these losses are not races; they are trades accepted at a quote that had already eaten
  the margin.
- **Uniswap-first:** if the pool moved just before, the bot still buys, receives less, and Textile's quote for
  the smaller amount no longer breaks even. That is the extra 7 to 10 points of incompletes after a competitor.

The change: allow the first leg only as much slippage as leaves the minimum edge intact (a few hundredths of a
percent, computed from the trade's own expected edge), instead of a flat 0.30%. A trade that fails this check is
simply not started: nothing is bought, no gas is spent (the bot simulates before sending). It will also skip
some trades that would have won; only a live test tells how many.

What it cannot fix: the 13% of Uniswap-first trades that go incomplete with nobody else around. Those come from
Textile's firm quote arriving lower than its preview after the purchase. The remedy there is on the list for the
review: skip Uniswap-first trades whose edge is thinner than Textile's usual preview-to-firm drop.

| | Priority gas | Both legs at once | Tolerance matched to the edge |
|---|---|---|---|
| Losses it targets (two weeks) | under $1 | $21.50 | $21.50 plus about $8 of incompletes |
| Share of trades it can affect | 2.7% | 36% (Textile-first only) | all |
| New on-chain code | no | yes | no |
| Extra cost per trade | higher tip | none | none |
| Effort | small | large | small |
| Risk | low | high until reviewed | low; may skip some winners |

## Testing on the phone with a second wallet

Run the test wallet in Termux next to the live bot, same hours, same settings, for 5 to 7 days:

1. New wallet, $50 to $100 of USDT and about 2 CELO. `MAX_TRADE_USD` at 90% of its USDT.
2. Same `INTERVAL_SEC`, `MIN_EDGE_USD` and smart sizing as the live bot, so the only difference is the tolerance.
3. Compare per route: share of losing trades, share of incompletes, profit per $1,000 traded, and how many trades
   each took. Sizes differ (the test wallet is small), so compare rates, not dollars.
4. Pass mark: losing Textile-first trades fall from about 24% to under 10%, incompletes fall, and profit per
   $1,000 traded does not drop.

Two wallets from the same phone do not share Textile's limit of four open quotes; that limit is per wallet.
Priority gas does not need a test: the numbers above already bound it.

## Two things found on the way (for the review, not changed)

- **A misleading log line.** When a Textile-first trade's first leg does not fill, the bot prints `incomplete
  (leftover held and managed by flatten)` although nothing was bought. The 9 Nov 04:28 wARS line ($387.23,
  expected +$2.69) was one of these: the wallet sent no transaction between 04:20 and 04:36. Mel's bot took a
  $300 trade in that gap at 04:28:38 for +$2.08. An earlier explanation in chat (that wARS was bought and held)
  was wrong.
- **An unguarded sale.** If the Uniswap sale of a Textile-first trade reverts, the currency stays in the wallet
  with no cost recorded, and the next cycle sells it at any price, outside the 0.5% loss guard. It has not
  happened often (at most 33 failed transactions all season), but the guard should cover it.
- **A small speed-up.** After sending a leg the bot checks for its receipt twice straight away, then only every
  4 seconds (the library's default, set for slower chains; Celo makes a block every second). Usually the first
  checks find it: the second leg lands a median 2 seconds after the first on Textile-first trades and 5 seconds
  on Uniswap-first ones, where Textile's quote takes the time. Checking every second would only trim the slow
  cases, at no cost.
