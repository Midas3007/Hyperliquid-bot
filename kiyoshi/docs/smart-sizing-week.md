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
| Nov 5 ~12:00, rest of the day | 20 | 0.6 | two-slot sizing: first trade ≤ 60% of the wallet, and Textile-first trades only try what fits beside Textile's hold |
| Nov 6, one day | 45 | 0.9 | middle ground for the scan; sizing back to normal |
| Nov 7 onward | 60 | 0.9 | back to the original, unless the review decides otherwise |

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
| Nov 5 | Claude | The Render log only prints the full-size quotes; half and quarter sizes are scanned but not shown, so "no trade" lines can look worse than they are | Cosmetic; could print the best of all sizes |
| Nov 5 | Midas asked, Claude proposed | Finer candidate sizes (¾, ⅜) and a best-size candidate from gap × $1.72M ÷ 2 | Offered, not built; decide at the review. Cost corrected: each extra size in the full scan adds ~16 quotes (4 corridors × 2 routes × 2 legs), so ¾ + ⅜ would make each scan about two-thirds slower. Cheaper version: after the normal scan, re-quote only the best opportunity at its formula size: 2 extra quotes, about a second |
