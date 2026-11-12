# Fund treasury: gas + Render (design and build notes)

Built in the working tree only. Nothing is committed, deployed or switched on. Decision by Midas, 12 Nov: 10% of the
pool's trading profit goes into a pot that pays for gas and Render; Midas keeps 100% of his fees; members see the pot.

## For Midas: what you will see and what changes

**On the Dashboard** there is a new "Running costs" section: a pot that fills with gold as profit is carved in, the
balance, "about N days of costs covered", this month's totals (carved in / gas bought / Render) and a log of every
movement. Each gas purchase and each Render withdrawal links to its transaction on Celoscan.

**Every day at the settlement (about 20:07 UTC)** the keeper moves 10% of that day's trading profit into the pot, just
before it posts the fund's value. A losing day carves nothing. Profit that only wins back an earlier loss is not carved
(you take 10% of net profit, not 10% of every green day).

**When the bot buys gas** ($0.50 of USDT into CELO) the pot goes down by $0.50. Members' share value does not move.
If the pot is empty, gas falls on the share value exactly as it does today, and the log says so.

**When you pay Render** you send the USDT from your phone as usual, then (signed in as admin) Admin tab, "Record a
Render withdrawal": amount, date, a note. The site matches it to the real transfer within minutes and the pot goes
down. Members' share value does not move. If you type more than the pot holds, the form warns you and only lets you
record it if you tick a box (the rest then comes out of the share value, and the log says so).

**What changes for members:** the share price now grows by 90% of the pool's trading profit instead of "profit minus
gas" (about 96% of it at today's numbers). They pay a bit more than they paid for gas, and they get a transparent line
for it. Fees are untouched.

### Worked example with the real season numbers (season 2, 29 Oct to 12 Nov, from the chain)

| | |
|---|---|
| Trades | 3,144 |
| Profit as `/today` shows it | +$334.70 (already net of the 28 top-ups) |
| Profit before gas (the carve base) | +$348.43 (= 334.70 + 13.73 gas) |
| 10% carved | $34.84 |
| Gas bought (28 top-ups) | $13.73 |
| Pot if it had run since day one | $22.76 ($34.84 carved, $12.08 of gas paid from it) |
| Gas the pot could not pay (empty on day 1) | $1.65, falls on members as today |
| Members' value gained | $311.93 = 90% of $348.43 minus that $1.65 |

This is replayed day by day from the real `pool_tx` rows by the test script (hypothetical start at the season start;
the real start will be from zero). At today's pace the pot grows about $2.39 a day against about $1.25 of costs, and
shows about 18 days covered here because the first days had little carved yet; it is "full" at 60 days (about $75).

Effect on one member's share (a $590 fund, a member holding $100 of it, so 1/5.9 of the value):
- **Carve of $2.39:** fund value $590.00 to $587.61, the member's $100 to $99.60 (-0.4%). That day the member's value
  still rose by about 90% of the day's profit overall.
- **Gas top-up of $0.50 (pot has funds):** wallet $587.61 to $587.11, pot $2.39 to $1.89, value posted $587.61 to
  $587.61. Member: $99.60 before and after.
- **Render $8 (pot holds $20):** wallet -$8, pot -$8, value posted unchanged. Member unchanged.

## Technical notes

### 1. How the fund's value reaches the share price today
`vault-keeper` (hourly, `index.ts`) computes `value = walletValue(treasury)` on mainnet, where `walletValue` sums, for
the bot wallet, USDT and USDC at $1 and 11 other tokens priced through the Uniswap quoter (the `TOKENS` list). **CELO is
not in the list, so CELO is not counted.** It then calls `reportValue(value)` when the value differs from `nav()` by at
least one cent, and once a day after 20:00 UTC (`SETTLE_HOUR_UTC`, in practice the 20:07 run) `settle(value)` when
anyone is queued. The contract: `nav` is exactly the last posted value and `sharePrice() = nav * 1e18 / totalSupply()`.
The contract limits `nav` to move at most `maxNavChangeBps` (50%) from the value at the start of a rolling 24 h
window (`_checkJump`). `sync-value` and keeper step 1 do the same sum for the off-chain (Supabase ledger) season.

### 2. Where the pot lives, and the identity
The pot is an accounting line over USDT in the bot wallet (default confirmed; no reason found against it). Let W be the
wallet value the keeper counts and P the pot. Posted value V = W - P.
- **Carve c:** W unchanged, P + c, so V drops by c (by design: that is the 10%).
- **Gas top-up g (P >= g):** USDT -g leaves W; the CELO bought is not counted (not in `TOKENS`), so W falls by exactly g;
  P falls by g; V unchanged. This depends on CELO staying uncounted: if the keeper ever counts CELO, the identity breaks.
- **Gas with P < g:** pot pays min(P, g), V falls by the rest: as today. The entry records `uncovered`.
- **Render r:** USDT -r leaves W, P - r: V unchanged. Same rule if the pot is short.
- **Sweep s (manual):** P - s, W unchanged, V + s.
So V = V0 + 90% of gross profit - gas/Render the pot could not cover. The test proves this on the real rows.

### 3. No double charging: the formula
The dashboard counter treats each top-up (a transaction the wallet sends to the router, USDT out) as a buy leg of the
next trade, so its profit is already net of gas. `_treasury_profit` rebuilds the same trades from `pool_tx` with
top-up legs excluded (a leg is a top-up if sent by the wallet, to the router, USDT out of at most `gas_max_usd` = $1).
Checked on the real rows: same 3,144 trades; $348.428575 vs $334.698424; the difference is exactly the $13.730151 of
top-ups.

Carve for a settlement day = `carve_pct` (10%) x max(0, window profit + carried loss). The window runs from the end of
the previous window to the time the trade history has been read up to (`pool_sync.block_time`); a trade belongs to the
window in which its sale closed, so each trade is counted once (test: 3,144 trades, windows contiguous). Only realised
profit counts; open inventory does not. **Losing days:** high-water rule on (`carry_losses` true): a loss is carried and
later profit first wins it back, so the pot takes 10% of net profit and members never pay 10% on a recovery. Choice
reasoning: it matches "10% of the pool's profit" literally and is the member-friendly default; at $24/day profit against
$1.25/day of costs the pot has room. Switch with `admin_treasury_settings(p_carry_losses => false)`.

### 4. Timing, atomicity, gas source
Code path in the keeper: `treasuryValue()` (`functions/_shared/treasury.ts`) runs, in order: status check (off or not
installed: behave as before); refresh `pool_tx` through `trade-stats`; `treasury_sync()`; `treasury_carve()` when the run
is due; read head block; read the wallet; `treasury_sync()` again; `treasury_status(head)`; post `W - P`. `treasury_carve`
is keyed by the UTC day in `treasury_days` (primary key) under a row lock, so a second run (or a concurrent one, tested
with two connections) returns "already" and carves nothing. Anything carved is in the database before the value is posted.
**Gas source:** derived from `pool_tx`, not the heartbeat, because the heartbeat only carries the last top-up (a missed
heartbeat loses one) while `pool_tx` is complete and re-audited daily. Verified on the 28 real ones: the rule finds exactly
28, total $13.730151; the smallest other router buy is $19.66 (gap from $0.50). The tx hash is unique per gas entry
(index plus `on conflict do nothing`), so seeing one twice changes nothing. The heartbeat is used only to attach the CELO
amount. Residual risk: a real trade leg under $1 to the router would be misread as gas (the bot does not make them; the
threshold is configurable).

**Fail-safe:** if the pot cannot be read, `treasury_sync` errors, the history is more than 45 blocks behind the chain, a
Render transfer is unmatched (hold), or P >= W, the keeper posts nothing that run and notes why in `last_result`; it tries
again an hour later. If only the carve could not run (history stale), it still reports the value but does not settle
and does not mark the day settled. Switching the treasury off (`active = false`) restores the old behaviour at once. If
the carve fails persistently, settlements wait: this is deliberate, and the off switch is the escape.

### 5. Render withdrawals
Recorded by the signed-in admin (`admin_treasury_record_render`, admin only, no money moves) as "pending". Matching
(`treasury_sync`, every 5 minutes and on every keeper run): a USDT transfer sent by the wallet to the USDT contract,
same amount, not already matched, after the start, up to a week before the record; with a hash given it must be that
transaction. Only then does the pot go down. While a record is unmatched, or a direct transfer is unrecorded, for
`hold_minutes` (180) the keeper posts nothing, so a Render payment never shows as a dip. More than the pot holds is
refused unless confirmed, then the pot pays what it has and the rest is logged as paid by members.

### 6. Sweep
Manual and logged (`admin_treasury_sweep`, a button on the Admin tab when the pot is above 60 days of costs). Not
automatic, because it raises members' value in a step and "days of costs" rests on an estimate; Midas should choose
when. Costs = last 14 days of top-ups from the chain + $8/month.

### 7. Daily limit and starting
Starting from zero: V = W - 0, so the first posted value after switching on is unchanged. Carves are small: the
largest in the replay is $8.10 (1.4% of $590), far below the 50% limit. A back-carve of the season (would take $34.84
out at once, about 6% at today's wallet) would not trip the limit but would show as a 6% loss on the chart and in
every member's profit, so it is not done. The pot only counts from the moment Midas presses Start.

### Tests (`fund-site/supabase/test/treasury.test.mjs`, 113 checks, all pass)
Run: start the local Postgres, `NODE_USE_ENV_PROXY=1 node fund-site/supabase/test/treasury.test.mjs`. It rebuilds the
`treasury_test` database (never touches `public` of the other database; reads real rows from it select-only), runs 014
twice, then: privileges and RLS; the 28 real top-ups; profit with and without top-ups; the full season replayed day by
day (windows contiguous, pot never negative, members' value identity, rerun changes nothing); a losing day (carry on and
off); two runs in a day (and two real connections at once); stale history; admin start (nothing back-dated); top-up with
empty and short pot; the same top-up seen twice; Render matching, larger than the pot, two equal withdrawals, cancel;
holds; sweep; table constraints; the keeper's decision code. Screenshots (400 px and 1280 px, mock plus real replay
data) are in the scratchpad `treasury/` folder; `harness.mjs` there regenerates them.

### Recommendations (not done: frozen)
None required. Optional: have the bot heartbeat list every top-up since the last beat (would remove the need for the
`celo` enrichment guess).

## Decisions for Midas (defaults chosen, all easy to change)
1. Start from zero when you press Start; no back-carve of season 2.
2. Losing stretches are carried (net profit rule). Off with `admin_treasury_settings(p_carry_losses => false)`.
3. Carve 10% (`p_carve_pct`), Render $8/month and full at 60 days (`p_render_usd_per_month`, `p_target_days`).
4. Sweep of the excess is manual, not automatic.
5. The pot also applies to the off-chain Supabase season value (keeper step 1 and `sync-value`), not only the vault.
6. A day's carve happens even on an early "Settle now", at most once per UTC day.
7. Gas paid while the pot is empty (first day) falls on members, as today.
8. A top-up is "a router call of at most $1"; Render matching tolerates a week between sending and recording.
9. I added a "Running costs" item to the risk disclosure (footer) telling members about the 10%: please review the wording.
10. Dashboard hides the section until the treasury is started.

## Deploy steps (in this order; nothing was deployed by me)
1. Run `fund-site/supabase/014_treasury.sql` in the Supabase SQL editor (safe to repeat; the treasury stays OFF).
2. Deploy the functions: `vault-keeper` and `sync-value` (both import `functions/_shared/treasury.ts`, so deploy from
   `fund-site/supabase` so the `_shared` folder is included). Until step 5 they behave exactly as before.
3. Deploy the site (`fund-site/web`: `treasury.js`, `app.js`, `index.html`, `styles.css`).
4. Check the pool wallet is set in Settings and the Dashboard shows nothing new yet.
5. Admin tab, "Start the treasury". The next daily settlement makes the first carve.
6. Watch the keeper's last result on the Admin page for the first few runs; record your next Render withdrawal.
Rollback: `update public.treasury_config set active = false;` (the pot's history stays).
