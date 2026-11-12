# Midas × Kiyoshi on-chain fund: notes and open decisions

Notes from the call with Mel (Kiyoshi is about to get a big upgrade). Scope: Midas's own Kiyoshi build, which
keeps pulling updates from Mel's repo.

## Roadmap: simplest first

Tags: 🟢 easy (days) · 🟡 medium (a week or two) · 🔴 hard (weeks, needs an audit) · ⚪ optional

**Phase 1: basic on-chain fund** (replaces the Supabase ledger)
- 🟢 Vault contract on Celo: deposit USDT, get shares (ERC-20 token); withdraw shares, get USDT.
- 🟢 Capital goes to the bot wallet; the fund value is posted by the manager/bot (option B below).
- 🟢 Deposits and withdrawals settle once a day (scheduled), so nobody times the bot.
- 🟢 10% performance fee on each person's profit, paid to the manager as shares (as today).
- 🟢 Approved-wallet list, total deposit cap, 2-of-3 multisig admin, testnet first.
- 🟢 Opening shares for Midas from the current ledger.
- 🟢 Dashboard: connect wallet (MetaMask, Valora, WalletConnect), deposit, see shares/value/profit, public fund
  value and share price chart, risk disclosure.

**Phase 1 status (29 Oct):** built and tested on a local chain; not deployed yet. Code: `fund-vault/` in the
Hyperliquid-bot repo (contract, 14 tests, Termux `deploy` / `vault` / `keeper` commands, README) and the
"On-chain fund" section of the site (connect wallet, deposit, redeem, cancel, collect; manager panel), which stays
hidden until the vault address is set. Choices made while building:
- Shares (MKF) cannot be transferred yet; transfers come in Phase 2 with their fee.
- One settlement a day after 20:00 UTC (9 pm Lagos); the keeper also posts the value hourly for the share price.
- Payouts are sent automatically; if a wallet refuses the transfer, the money waits for that member to collect it.
- The reported value may move at most 50% a day (owner can change it, 48-hour delay, 1% to 500%).
- The fee recipient (personal wallet) pays no fee; fee shares carry zero cost. Fee cap 20%, changes wait 48 hours.
- No multisig in v1 (the bot wallet is owner, as decided); the 48-hour delay and instant pause cover role changes.
- Not audited: testnet first, then mainnet with a small deposit cap.

**Phase 2: more flexibility**
- 🟡 Instant withdrawals (15% of profit + 0.3% into the fund).
- 🟡 Transferable shares (15% of the sender's profit at transfer).
- 🟡 Gas reserve (10% of fees), 48-hour delay on setting changes, emergency pause and member exit.
- 🟡 MiniPay support, transaction history, tax records.

**Phase 3: trust-minimised**
- 🔴 Money stays in the vault; the bot trades through it on approved exchanges only (option A below).
- 🔴 Fund value read on-chain instead of posted; other chains' capital included.
- 🔴 Independent audit before raising the cap.

**Optional, any time after Phase 1**
- 🟡 Kiyoshi Pass NFT gating (decided 6 Nov): 100 passes; replaces the approved-wallet list; fees by passes held
  (20% / 15% / 10% / 7% for 1-4); needed to use shares received by transfer (see "Transferable shares and NFT
  gating").
- 🟢 Host the bot on a cloud server instead of the phone (**built 29 Oct: Render worker, see kiyoshi/HOSTING.md;
  the daily restart became a pause inside the bot from 20:00 UTC until the keeper reports the day settled**): a small always-on VPS (or a cloud container) running
  Kiyoshi under a process manager that restarts it, with the bot key in the server's secret store, logs and
  alerts (bot stopped, wallet value dropped, keeper missed a run), and the phone kept only for the owner's
  emergency commands (pause, role changes). The fund's settlement already runs on the server (Supabase keeper).
  **Daily restart around settlement (decided):** so the bot wallet is flat (all USDT, no half-finished trade)
  when the keeper settles at 20:07 UTC, the server stops the bot gracefully at **20:00 UTC** (it finishes the
  current cycle and sells or holds any leftover under its loss guard), the keeper settles at 20:07, and the bot
  starts again at **20:10 UTC**, so the day's new deposits are traded straight away. If the keeper reports
  "settlement waiting", the bot stays stopped until the next keeper run settles, then starts. Scheduled with the
  server's timer (cron or systemd timers); the times follow the keeper's schedule if that ever changes.

**Before opening to the public (any phase):** legal advice.

## Decisions from Midas (Phase 1)
- Start: Thursday.
- **Contract owner:** Midas asked for the bot wallet. Recommended instead: owner = a separate wallet Midas
  controls (or a 2-of-3 multisig), bot wallet = "operator" (can post value, settle, return funds; cannot change
  fees, owners or limits). If the bot key on the phone leaks, the attacker then cannot take over the contract.
  Ownership can be handed over later with one transaction.
  **Decided: the bot wallet is both owner and operator** (Midas will not use it for anything else). Mitigations
  kept because the key lives on the phone: 48-hour delay on owner/setting changes, daily payout cap, payouts
  only to members' own wallets, deposit cap, phone lock and backups of the key kept offline.
- **Midas's own money in the bot wallet:** at launch, everything in the bot wallet (cash and leftover tokens)
  belongs to the fund, and Midas's whole position becomes Midas's opening shares (today 100% of the fund).
  From then on, personal money must either go in through a deposit (to get shares) or live in another wallet.
  Gas (CELO) is a fund expense.
- **Midas's personal wallet:** `0xf95E42d5FC3602b05CE9cF8d2E4cFa6b810F3113`. It receives Midas's opening shares
  and is the wallet Midas uses on the site. The bot wallet never connects to the site.
- **Role split (decided):** day to day Midas only uses the personal wallet; the bot wallet stays on the phone.

  | Power | Wallet | Notes |
  |---|---|---|
  | Owner: emergency pause, assign or replace the roles below | bot wallet | backup if the personal wallet is lost or stolen; role changes go through the 48-hour delay |
  | Operator: trade the fund's money, post value, settle payouts | bot wallet | cannot change fees, roles or limits |
  | Manager: raise or lower fee rates | personal wallet | hard cap written into the contract (e.g. 20%, cannot be raised); every change waits 48 hours so members can exit first |
  | Fee recipient: fee shares are minted here | personal wallet | same as today's "fee units" |
  | Fee exempt: no performance or transfer fee on its own shares | personal wallet | bot wallet holds no shares after launch |
  | Withdrawals | whoever holds the shares | USDT is paid to the shareholder's own wallet (or an address that wallet chooses); nobody else, the bot wallet included, can redirect it |

  If the personal wallet is compromised, the worst case is a queued fee change (capped, delayed and visible on the
  dashboard); the owner (bot wallet) pauses the contract and replaces the manager. The site's admin page keeps
  working from the Midas login and does not depend on either wallet.
- **Using the bot wallet's powers (decided):** from Termux on the same phone, with a small command that signs with
  the bot key already in `.env`. The bot wallet never connects to a website or wallet app. Every command shows
  what it will do and asks for confirmation before sending.

  | What | Command | Takes effect |
  |---|---|---|
  | Emergency pause | `npm run vault -- pause` | immediately: deposits, withdrawals and settlements stop; nobody's money moves |
  | Unpause | `npm run vault -- unpause` | immediately |
  | Replace a role | `npm run vault -- set-manager 0xNEW…` (also `set-operator`, `set-fee-recipient`, `set-owner`) | queued for 48 hours, shown on the dashboard; then `npm run vault -- execute manager` |
  | Cancel a queued change | `npm run vault -- cancel manager` | immediately |
  | Roles, settings and queued changes | `npm run vault -- status` | read-only |

  Pause is instant because it cannot move money. Role changes wait 48 hours so a stolen bot key cannot quietly
  take over; cancel is instant so a change you did not make can be stopped. If the personal wallet is stolen:
  pause, queue a new manager, wait 48 hours, execute, unpause.
- **Share price starts at $1.00** in the new contract: Midas's opening position (about $113 at launch, exact
  value taken from the wallet that day) becomes that many shares at $1.00. The site already keeps seasons:
  finished seasons (Season 1: from Mon 26 Oct, off-chain) are archived with their return, trades and leaderboard
  and shown on the public page; the on-chain contract starts as the next season.
- **Site:** reuse the existing https://midas-kiyoshi-fund.vercel.app/ (add wallet connect, deposit, withdraw).
- **Mobile only:** every step must be doable from the phone. Deploy the contract from Termux with a small
  script (compiles with solc-js, deploys with the bot wallet key that is already in `.env`, verifies on
  Celoscan), so no laptop or Remix is needed; Rabby (or MetaMask/Valora) is used on the site.

## What was asked for
1. A smart contract for the fund (replaces the Supabase ledger).
2. A public landing page and dashboard.
3. Connect a wallet, deposit, and see your shares.
4. Fix any mismatch when someone sells shares while the bot is mid-trade or still earning.
5. Shares can be transferred.
6. The contract is connected to the wallet the bot trades from.
7. For Midas's version of Kiyoshi, which keeps pulling from Mel's.

## Decisions to make before writing code

| # | Decision | Options | Leaning |
|---|---|---|---|
| 1 | **Who holds the money** | (a) vault holds funds; bot trades *through* the vault via allow-listed exchanges only; (b) vault sends capital to the bot wallet; the bot reports the fund value | (a) is safer (a stolen bot key cannot drain the fund) but needs bot changes; (b) is simpler but members must trust the reported value |
| 2 | **How fund value is known** | on-chain read of balances (works for (a) on one chain); bot or keeper reports it (needed for (b) and multi-chain) | on-chain where possible; reported value with limits (max change per update) where not |
| 3 | **Deposits and withdrawals timing** | instant at the current share price; queued and settled when the bot is flat (ERC-7540 style), e.g. every hour or day | queued: this is what removes the "selling while the bot is mid-trade" mismatch and stops people jumping in right before a profit is booked |
| 4 | **Which chain the vault lives on** | Celo only (where Kiyoshi trades); one vault per chain; Celo vault with other chains reported | Celo first |
| 5 | **Fees** | (a) per-person: each holder's cost travels with their shares when they transfer them; (b) fund-level high-water mark | **Midas's call:** 10% performance fee paid as new shares (units) to the manager, as today; 10% of each fee goes to a gas reserve that pays the keeper and bot gas. Per-person (a) is doable on-chain; see "Fees in detail" |
| 6 | **Lock period / early-exit penalty** | keep the current ones; drop them | note: transferable shares let people exit by selling shares, which skips a lock |
| 6b | **Instant exits and transfers** | flat 1-2% of full value; 15% of profit | **Proposed:** 15% of profit (vs 10% for scheduled exits), +0.3% into the fund for instant exits; see "Exit and transfer pricing" |
| 7 | **Who may deposit** | anyone; allow-listed wallets only (members you approve) | allow-list at first (see Legal) |
| 8 | **Upgradeable or fixed contract** | fixed (simpler, trusted); upgradeable behind a timelock | fixed v1, migrate to v2 if needed |
| 9 | **Deposit limits** | per-person and total caps | a total cap while it is new, raised over time |

## Fees in detail

**Performance fee (10%), paid as manager units.** Two ways to measure the profit it is charged on:
- *Per person* (what the current site does): the contract stores each holder's cost. When shares are
  transferred, the matching part of the sender's cost moves with them, so the receiver inherits it. The fee is
  taken when that holder withdraws. Works on-chain; a buyer who paid more than the seller's cost will still be
  charged on the seller's gain, so buyers should price that in.
- *Fund level, high-water mark:* when the share price passes its previous peak, 10% of the new gain is minted
  as shares to the manager. Simple and identical for everyone, but it is not matched to each person's own gain:
  someone who joins while the fund is below its previous peak pays no fee on the recovery.

**Gas reserve:** 10% of every fee (so 1% of profit) goes to a gas wallet that pays for settlements, value
updates and bot gas. Show its balance on the dashboard.

**Transfer fee mechanics:** taken in shares at the moment of transfer. Tokens with a transfer fee do not work
well on DEXes or some wallets, so a secondary market would be limited (fine for a members' fund).

**Exit and transfer pricing (proposed, replaces the flat transfer fee):**

| Action | Fee on profit (to manager, as units) | Extra | Why |
|---|---|---|---|
| Scheduled withdrawal (settles at the next window) | 10% | none | the normal, cheapest way out |
| Instant withdrawal | 15% | 0.3% of the amount, paid **into the fund** (stays with remaining members) | the fund keeps cash idle for instant exits and may have to cut a trade short; the 0.3% stops in-and-out timing even when there is no profit |
| Transfer to another wallet | 15% of the sender's profit on the shares moved | none | the fee is settled at transfer, so the receiver starts fresh at today's price (no inherited cost) |
| Transfer between a member's own approved wallets | none | none | not a sale |
| Any exit or transfer inside the lock period | as above | early-exit penalty, paid into the fund | keeps the lock meaningful |

No profit means no profit fee in every row. 10% of every manager fee still goes to the gas reserve.

**Setting changes:** fee rates, the transfer fee, the bot address and allowed exchanges can only change after a
public delay (e.g. 48 hours, a "timelock"). Members see the pending change on the dashboard and can withdraw
first at the old terms.

## Transferable shares and NFT gating (decided by Midas, 6 Nov)

These are now part of the plan, not options. They override the Pass notes further down where the two differ.

- **Shares (MKF) are transferable.** The transfer pricing above applies (15% of the sender's profit on the shares
  moved, settled at transfer; free between a member's own wallets).
- **The NFT replaces the manual approved-wallet list.** Holding at least one pass is what lets a wallet deposit.
  No more adding wallets by hand.
- **The number of passes held sets the fee you pay.** More passes, lower performance and transfer fees.
- **Shares need a pass to be used.** If someone transfers shares to a wallet without a pass, the shares arrive
  but stay locked (no withdrawing, no moving them on) until that wallet holds a pass.

**Details (decided by Midas, 6 Nov):**

| Passes held (counted) | Performance fee on profit |
|---|---|
| 1 | 20% |
| 2 | 15% |
| 3 | 10% |
| 4 or more | 7% (at most 4 passes count) |

- **Counting:** passes are counted when the fee is charged (at withdrawal or transfer), and only passes the wallet
  has held for at least 7 days. Buying or borrowing passes just before an exit does not lower the fee.
- **Transfers and instant withdrawals** use the same tier rate as the holder's performance fee (instant
  withdrawals keep their extra 0.3% paid into the fund). The contract's 20% fee cap still holds: the top tier is
  20%. 10% of every manager fee still goes to the gas reserve.
- **Locked shares:** shares sent to a wallet without a pass stay locked. Within 30 days they can be sent back to
  the sender (to undo a mistake), or unlocked by getting a pass. After 30 days with no pass they join the next
  scheduled withdrawal automatically, at the 1-pass rate, so money is never stuck.
- **Selling your last pass** locks your shares the same way: get a pass back within 30 days, or they are
  withdrawn at the next settlement.
- **Safety:** the pass contract's address can only change after the 48-hour delay. Pausing or gating can never
  stop the normal scheduled withdrawal (locked shares included, through the 30-day rule).
- **Royalties** on pass resales are enforced (ERC-721C or the built-in resale page), since passes carry fee value.
- **Supply: 100 passes.** That is also the most members the fund can have.
- **Site:** shows each member's passes counted, their fee tier and any locked shares with the days left; checks
  for a pass before the deposit button; check whether MiniPay shows NFTs.
- **Redeem breakdown card (build with these additions, not before):** as a member types an amount to redeem, a
  small card shows the amount, the part that is profit, the fee at their tier rate on that profit, the 0.3% that
  stays in the fund if they choose instant, what they receive, and when it is paid. Example for $150 worth with
  $50 profit at 1 pass: scheduled gets $140; instant gets $139.55 ($10 fee + $0.45 into the fund).
- **Still invite-based.** The pass does not open the fund to the public; Midas still decides who gets one. It
  just replaces adding wallets to the list by hand.

## Kiyoshi Pass (membership NFT) ⚪ optional, later

- A limited NFT that acts as the membership card: only wallets holding a Pass can deposit (replaces the
  allow-list). Supply = member slots (e.g. 15), so a seat can be sold or handed on by transferring the Pass.
- The Pass carries **no share of profits**; shares stay separate. Possible perks: lower exit/transfer fees,
  earlier settlement, higher deposit cap, a say in settings, holder-only stats.
- Mint price (and a royalty on resales) is income for the manager, separate from fund performance.
- Pass flipping (buy, join, sell, repeat) is welcome: every resale pays a royalty. To keep seats meaningful,
  fund shares require a Pass: when a Pass is sold, the seller's shares are queued for the next scheduled
  withdrawal (normal 10% fee) unless they move them to another Pass-holding wallet. Otherwise people could buy
  a Pass, deposit, sell it, and the fund would end up with more members than seats.
- Royalties are optional on many marketplaces; enforce them with a royalty-enforcing NFT standard
  (e.g. ERC-721C) or a built-in resale page, or charge the fee in the Pass contract on every transfer.
- Legal note: a Pass does **not** by itself avoid securities rules. Regulators look at the substance (pooled
  money, profit from the manager's work), and NFTs sold with an expectation of profit have been treated as
  securities. It helps by keeping membership invite-based and separate from the investment, but get advice.

## Fund treasury: gas + render (Midas's idea, 9 Nov) 🟡 next week

A named treasury line, separate from the pool's trading value, that holds a carve-out for running costs. Gas
top-ups draw it down (the treasury balance ticks down, not every shareholder's value silently as today); at
month-end Midas withdraws from it to pay render. Replaces the old
"10% of fees → gas reserve" line, which never worked: fees only arrive on withdrawals, so early on the reserve
would be near-empty while gas runs every hour. Carving continuously from pool profit tracks the real cost.

**Measured (season 2, on-chain, 29 Oct → 9 Nov, read-only):**
- 4,366 transactions in 10.68 days = **409/day**; ~166k gas each, **~$0.0024/tx** at CELO $0.07.
- **Gas ≈ $0.96/day · ~$29/month.** Render **$8/month = $0.27/day.** Gross pool profit ≈ **$18.75/day** (season avg).
- So gas ≈ **5%** of gross profit today; render ≈ **1.5%**.

**Decision (Midas, 12 Nov — replaces the 8% / render-from-fees proposal above it in history):**
- **10% of the pool's trading profit goes into the treasury, and it pays for both gas and render.** It is shown
  to members openly as the fund's running costs (gas + hosting), not hidden inside the share price.
- **Midas keeps 100% of his fees.** No slice of fees flows into the treasury.
- **A pot on the dashboard:** it fills as profit is carved in, drops a little each time gas is bought
  automatically (the bot's USDT → CELO top-ups), and drops when Midas withdraws for render.
- **A log of every treasury movement:** each carve-in, each gas purchase (with its transaction link), each
  render withdrawal (amount, date, note).
- **A render withdrawal only shows on the treasury**, never as a dip in members' share value: the carve-out has
  already left the pool's value by the time it is withdrawn.

**Cover at today's numbers (season 2 to 12 Nov, from the chain):** trading profit about $334.70 in 14 days =
**~$23.90/day**, so 10% ≈ **$2.39/day** into the pot. Costs: gas ≈ $0.96/day + render $0.27/day =
**$1.23/day**. The pot covers costs about twice over, which leaves room for a CELO price rise (gas is paid in
CELO) and thin days. If the pot ever holds more than ~60 days of costs, sweep the excess back to the pool so
members are not over-charged.

**To settle while building (not decided yet):**
- Where the pot physically sits. Simplest: it is an accounting line over USDT that stays in the bot wallet
  (the vault's NAV subtracts it), so no extra transfers or gas. Alternative: a separate treasury address.
- The trade counter already treats a gas top-up as money out of the next trade, so "profit before gas" may
  already be net of top-ups. Check this before carving, or gas would be charged twice.
- Carve on realised profit per settlement day (not per trade), and never on a losing day.

## Pre-launch work (next week, after the Nov 12 analysis and tweaks) 🟡

**Read-side resilience — the `/today` "Explorer returned 429" error. ✅ Done 12 Nov.** What it really was:
the block explorer now allows **10 requests an hour** without a paid key, so downloading the whole season on
every refresh could never work again (retries and backoff do not help against an hourly quota). A first fix on
9 Nov added a second explorer as a fallback without testing it against the real service; it does not support
Celo, answered "chain not supported", and that was shown as an empty season on the dashboard and in /today.
The durable fix: the pool wallet's USDT movements are read from Celo's own node (the one the keeper uses), a few
minutes of blocks at a time, and stored in the database once (`pool_tx`, migration 013). The dashboard and
/today are answered from the database; a schedule tops it up every 2 minutes; if the node is unreachable the
numbers are served with their age, never as "no trades". Checked against the chain: every one of the season's
trades identical to the original counting, and the stored rows byte-identical to an independent read.
Rule kept from this: **no fallback or data source goes live until it has been run against the real service.**

**Pre-launch security review — the 6-phase pass (before opening the fund):** (1) map the codebase; (2) hunt for
vulnerabilities; (3) challenge each candidate; (4) record the findings; (5) verify the findings; (6) report
what's confirmed, rejected, or still needs validation. Run this on the vault contract, the keeper, and the site's
money paths before any real deposit beyond Midas's own.

## Fund-opening film (Midas's brief, 12 Nov) ⚪ after the work above

One film for the day the fund opens, made from "Let There Be Liquidity" and "The Movement", each design morphing
into the next with no cuts, using the stats current on the day:
1. The squiggly flower from the opening unravels and becomes the corridor.
2. The corridor separates, and each side morphs into one of the two hands.
3. The circle between the two fingers grows and flips, and becomes a watch face.
4. The movement (the seven steps), then the numbers.
5. **It must end with the closing transition of the "how the fund works" film:** the fan closes midway, blades
   fly off to the M, and the rest of the fan completes itself to form 爪idas × Kiy🪭shi.
Music: one score with the best of both (the felt piano and strings of Liquidity, the harp and cello of The
Movement). Midas left room to improve the visuals, pacing and script.

## Things easy to miss

**Contract and security**
- Admin keys: use a multisig (e.g. Safe with 2 of 3 signers), not one phone key. Put fee and setting changes
  behind a timelock so members can exit before a change applies.
- Emergency pause, and a way for members to withdraw even if the bot or keeper stops.
- The bot's key must not be able to send funds anywhere except allow-listed exchanges and the vault.
- First-deposit share-price attack on new vaults (ERC-4626 "inflation attack"): use the standard defence.
- Rounding always in the fund's favour; dust handling.
- Stablecoin risk: USDT/USDC depegs; decide what the share price is denominated in (USDT).
- Audit, or at least an independent review, before real money; testnet first, then a small cap on mainnet.
- Verified source code on Celoscan.

**Accounting**
- Leftover tokens from half-finished trades must count in the fund value (the bot already tracks them).
- Gas and bridge costs come out of the fund; show them.
- Migration: mint opening shares from the current ledger (today only Midas, $113.19, 19.36 units at $5.85).
- Records for members' taxes (deposit, withdrawal, price per share, dates).

**Legal and trust**
- A public pooled fund with transferable shares can count as a security or collective investment scheme
  (e.g. Nigerian SEC, and wherever members live). Get advice before opening it to the public; until then keep
  it invite-only (allow-list), with terms, risk disclosure, and blocked regions if needed.
- Kiyoshi's license and credit to Mel; keep the attribution tag.

**Landing page and dashboard**
- Wallet connection that works on phones: MiniPay and Valora (Celo wallets common in Nigeria), MetaMask,
  WalletConnect; automatic switch to Celo.
- Deposit flow: approve, deposit, then pending until the next settlement (if queued), then shares.
- Per wallet: shares, value, profit, pending deposits and withdrawals, history.
- Public: total value, share price chart, returns, fees taken, live bot activity, contract and bot wallet links.
- Risk disclosure the user must accept before the first deposit.
- Keep the private members' site (Supabase) working until the vault replaces it.
- Mobile first: most users will be on phones.

**Running it**
- A keeper that settles queued deposits and withdrawals and posts the fund value (who runs it, who pays gas).
- Alerts when the bot stops, the value drops, or the keeper misses an update.
- A written plan for incidents (pause, communicate, recover).

**Keeping up with Mel**
- Keep the fork following Mel's repo; keep fund-specific code separate so updates merge cleanly.
