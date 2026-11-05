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

## Issues and ideas logged during the week

| Date | Raised by | Issue or idea | Notes |
|---|---|---|---|
| Nov 5 | Claude | Base max trade is $500, not $330; peak sizes are bigger than the analysis assumed | Check whether the extra size helped or hurt |
| Nov 5 | Midas asked, Claude proposed | Finer candidate sizes (¾, ⅜) and a best-size candidate from gap × $1.72M ÷ 2 | Offered, not built; decide at the review. Cost corrected: each extra size in the full scan adds ~16 quotes (4 corridors × 2 routes × 2 legs), so ¾ + ⅜ would make each scan about two-thirds slower. Cheaper version: after the normal scan, re-quote only the best opportunity at its formula size: 2 extra quotes, about a second |
