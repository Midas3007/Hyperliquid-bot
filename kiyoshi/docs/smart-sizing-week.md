# Smart sizing trial week: Nov 5 → Nov 12, 2026

**Freeze until the review on Nov 12:** no changes to the bot, its settings or the schedule (Midas's instruction,
Nov 5). Issues and ideas raised during the week are logged below and decided at the review: optimize further, or
tone down.

## Baseline (before smart sizing)

From `trade-size-analysis.pdf`, normal period Oct 27 → Nov 5 (after the wBRL windfall):

| | Midas bot (0x6cd3…81dc) | Mel (0x497F…512f) |
|---|---|---|
| Profit per day | ~$13.5 | ~$9.5 |
| Nov 1–4 profit ÷ max trade, per day | 5.3% (avg max $303) | 6.8% ($195) |
| Share of profit 13:00–14:59 Lagos | 37% (both bots combined) | |
| Trades under 3¢ | 846 trades, −$19.94 | 868 trades, −$17.18 |
| Failed cycles (both legs Uniswap) | 173 trades, −$11.27 | 163 trades, −$7.00 |
| Shared gaps (within 20 s) | 30% of trades; first in ~5.6 bps, second ~3.3 bps | |

## Trial setup

- Smart sizing switched **ON** at 2026-11-05 09:12 UTC (10:12 Lagos).
- The bot's first smart heartbeat (09:50 UTC) reported a base max trade of **$500** (MAX_TRADE_USD, previously
  about $330). The 90%-of-cash limit binds first while the wallet holds about $370; check the real trade sizes.
- Peak sizes from $500: Mon/Fri $800 (ceiling), Tue–Thu $750, Sat/Sun $625.

## Review on Nov 12

Same method as the PDF (explorer token transfers → round trips, via Supabase `net.http_get`):

1. **Midas bot, trial week:** Nov 5 09:12 UTC → Nov 12.
2. **Midas bot, earlier weeks:** the normal period, Oct 27 → Nov 5.
3. **Mel's bot, trial week:** the same days.

Compare:
- profit per day, and profit ÷ size;
- the share of profit by window (peak, busy, quiet);
- trades under 3¢ and failed cycles;
- real trade sizes in the peak;
- the number of trades skipped and kept;
- gaps shared with Mel, and who got there first.

Then decide what to optimize and what to tone down.

## Scan interval tests (Midas approved the one exception to the freeze, Nov 5)

| From (UTC) | INTERVAL_SEC | FIRST_TRADE_SHARE | Notes |
|---|---|---|---|
| until Nov 5 ~11:15 | 60 | 0.9 | as before |
| Nov 5 ~11:15 | 20 | 0.9 | faster gap discovery; watch for rate-limit errors from forno or Textile |
| Nov 5 ~12:00, rest of the day | 20 | 0.6 | two-slot sizing: first trade ≤ 60% of the wallet, and Textile-first trades only try what fits beside Textile's hold. Textile's 4-quote limit was hit from 12:57 Lagos; Midas chose to keep the test running to collect data (option 2) |
| Nov 5 afternoon (Render dashboard, by Midas) | 30 | 0.8 | set by hand; written into render.yaml and pushed the same evening |
| rest of the trial week, to the Nov 12 review | 30 | 0.8 | Midas: leave it running all week. The 45 s (Nov 6) and back-to-60 s (Nov 7) switches are cancelled |

The Textile-hold tracking (code) stays in all along: with 0.9 it only stops the bot from asking Textile for trades
that cannot fit, which it used to skip anyway.

CHASE_SEC stays 5 throughout. At the review, split the week at these times and compare each interval with Mel over the
same hours: the share of shared gaps won, profit per hour, error count and skipped trades.

## Issues and ideas logged during the week

| Date | Raised by | Issue or idea | Notes |
|---|---|---|---|
| Nov 5 | Claude | Base max trade is $500, not $330; peak sizes are bigger than the analysis assumed | Check whether the extra size helped or hurt |
| Nov 5 | Midas (Render log + Mel's execution log, 12:17–12:23 Lagos) | **Mel chains trades back to back; we can't.** Textile holds the USDT of the last quote for ~2 min, and a new quote needs free (unheld) USDT. Mel trades $195 of ~$297, so $97.50 and $48.75 follow-ups still fit (his log: $195 at 12:17:05, then $97.50, $97.50 and $48.75 at 12:18:34–12:18:59). We trade 90% of the wallet ($338.56 of $376), so nothing is free, and the next Textile-first trade is skipped (18 skipped since Nov 3). On-chain, Oct 29–Nov 5: Textile→Uniswap trades followed by another within 60 s: Mel 107 of 369 (29%), us 23 of 304 (8%). Smart sizing's larger peak sizes make this worse, not better | Proposal: "two-slot" sizing, first trade ≤ ~60% of cash, so a half-size follow-up fits while Textile holds the first. **Midas approved a one-day test (Nov 5), with the hold tracking**; see the table above |
| Nov 5 12:57–13:01 Lagos | Midas (Render log, 20 s + 0.6 test) | **Textile allows at most 4 outstanding quotes (RFQs).** A wARS Uniswap→Textile trade bought on Uniswap, then Textile refused the sale: `429 Too many outstanding RFQs (max 4)`. The leftover wARS sold on the second try ($226.28, no loss). Then a half-size chase trade ($113) and a peak quarter-size trade ($79) found "funds reserved" on their Textile leg and waited ~2 min. The first went stale and was sold back on Uniswap (−$0.11) | Cause: more trades and quotes per 2 minutes than Textile allows (the 20 s scan plus follow-ups), and each quote counts for ~2 min. The hold tracking only covers Textile-first trades; Uniswap-first trades also need a free Textile slot for their second leg. Fix for the review: count Textile quotes in the last ~2 min and don't start any trade that needs Textile when 4 (or 3, to be safe) are outstanding. Mel's log shows 4 trades in under 2 min, i.e. exactly at the limit |
| Nov 5 13:13 Lagos | Midas asked if 30 s would fix "reserved, waiting 120s" | No: that trade came from the chase (5 s re-check after a win), which ignores INTERVAL_SEC. Correction: the ~2 min Textile wait on a chase's next Uniswap→Textile trade is normal and mostly profitable. Before any test (Oct 27–Nov 5, 60 s): bot 276 such trades, +$44.18, 84% profitable; Mel 216, +$23.66, 85%. Only the `429 max 4 RFQs` refusals are new today | At the review, separate the normal chase waits from the 429 refusals |
| Nov 5 ~14:00 Lagos | Midas (Render log, peak) | Many `429 max 4 RFQs` in the peak, mostly on Textile-first chase attempts (nothing bought). Cause: the 0.6 share leaves USDT free, so Textile accepts follow-up quotes until all 4 slots are used, then every 5 s chase re-check gets a 429. Before today (0.9) such trades were refused as "reserved" and skipped without using a slot. The scan interval barely matters here (the chase runs every 5 s). Each 429 counts as a failed cycle, so 3 in a row trigger the Telegram alert | Midas set INTERVAL_SEC to 30 in the Render dashboard (~14:00 Lagos); render.yaml still says 20, and a push would sync it back. Not pushed, at Midas's request. Fix for the review: count Textile quotes in the last ~2 min and pause Textile trades at 3–4 |
| Nov 5 (afternoon) | Midas | Set FIRST_TRADE_SHARE to 0.8 in the Render dashboard (render.yaml still says 0.6). At ~$377: first trade ≤ ~$301, ~$75 free, so only a quarter-size follow-up fits; fewer 429s, more "reserved" skips | Dashboard now: INTERVAL_SEC 30, FIRST_TRADE_SHARE 0.8. At the review, split the day at each manual change (times from the Render deploy log) |
| Nov 5 18:03 Lagos | Midas (Render log) | Uniswap→Textile wARS at half size ($153.68, expected +$0.48) bought on Uniswap but the Textile sale did not fill ("incomplete"); flatten sold it on Uniswap for ~$153.65 (≈ −$0.03). The full-size quote on the same scan showed −$0.25 while half size showed +$0.48: a likely stale or over-optimistic Textile preview ("phantom gap") | For the review: (1) distrust a smaller size quoting a much larger edge than the full size; (2) on Uniswap-first trades, get Textile's firm quote before buying on Uniswap, not only a preview |
| Nov 5 | Claude | The Render log only prints the full-size quotes; half and quarter sizes are scanned but not shown, so "no trade" lines can look worse than they are | Cosmetic; could print the best of all sizes |
| Nov 5 | Midas asked, Claude proposed | Finer candidate sizes (¾, ⅜) and a best-size candidate from gap × $1.72M ÷ 2 | Offered, not built; decide at the review. Cost corrected: each extra size in the full scan adds ~16 quotes (4 corridors × 2 routes × 2 legs), so ¾ + ⅜ would make each scan about two-thirds slower. Cheaper version: after the normal scan, re-quote only the best opportunity at its formula size: 2 extra quotes, about a second |
- **Nov 6: the profit counter used for the baseline reads high.** `wallet-stats.ts` only counts trades whose sale
  matches the buy within 1%, which drops most of the losing trades. For Nov 5 it read $76.65 (bot) and $44.29 (Mel).
  Counting every trade the way Telegram's /today does gives **$27.30 (bot) and $22.70 (Mel)**, which matches /today
  ($25.83; the small gap is where the day's edges fall). Before the review: redo the baseline (the ~$13.5 and ~$9.5
  a day, and the 1,887 / 1,820 trips) with the /today method, which `scripts/day-stats.ts` now uses, and check the
  trade-size PDF's figures the same way. The share price is the final check.
- **Nov 6, 14:00–14:27 Lagos (peak): Mel +$3.03 vs our +$1.22, from the chain.** Both bots wait about 2 minutes for
  Textile on many trades (Mel: buy 13:12:19 UTC, sell 13:14:24); Mel's dashboard shows only the sale time, so its
  trips look back to back. The gap came from: (1) size: Mel traded its whole ~$288 every time, we traded $65–$162 on
  6 of 11 trades (the 0.8 first-trade share, the Textile-hold limit and the size picker); during Mel's best minutes
  (14:11–14:16, +$0.66–0.69 a trip) we were flattening the 14:09 leftover, then did an $81 trip; (2) losers: one
  Textile-first trade (−$0.23 at 14:09) and two flattens sold on Uniswap (−$0.18 at 14:20, −$0.03 at 14:24);
  (3) "incomplete" trades: after the 2-minute wait the firm Textile quote no longer covered the cost (same rule as
  Mel), which happens more when the peak minimum (0.3 bps) accepts thin gaps like +$0.05 on $323; (4) both bots hit
  the same wARS pool in the same block at 14:22:49. For the review: full size when Textile holds nothing, a higher
  peak minimum to survive a 2-minute hold, and flatten waiting for Textile before selling on Uniswap at a loss.
- **Nov 6, 11:00–14:36 Lagos: the win rate fell from 82% to 76%.** Between the two /today readings: 35 trades,
  21 profitable (60%), +$1.16 on $7,634 traded (about 1.5 bps). Fits the entry above: the 0.3 bps peak minimum lets
  in thin gaps that a 2-minute Textile hold turns into losers or leftover sales.
