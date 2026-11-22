# MINAKIBA: build brief for Claude Code

You are building **MINAKIBA**, the Midas × Kiyoshi naira fund on **Base**. It is the sister of the Midas × Kiyoshi Fund
on Celo, which has run since 26 October 2026. Everything that worked there is described below; carry it over, with the
fixes its security review found already built in. Hosting: **Neon** (Postgres) for data and **Vercel** for the site,
the API and the scheduled jobs.

Read this whole file before writing code. Then make a short plan, confirm the open facts (section 2) with the manager,
and build in the order of section 13.

---

## 1. What the fund is

* A private pool. Approved members deposit **USDC on Base** from their own wallets and receive non-transferable shares.
* **Kiyoshi** (Mel's trading bot, already running on Base) trades the naira: it buys cNGN on one venue and sells it on
  the other when the gap between **Uniswap** and **Textile** pays for itself. The bot's wallet is the fund's treasury.
* Profit settles into the share price once a day (20:07 UTC). Members redeem whenever they like; redemptions settle at
  that day's price, paid back to the member's wallet, after the performance fee on profit above what they put in.
* Every dollar figure on the site has its **naira value under it**, at the fund's own naira rate (section 7).
* Name: MI(das) + NA(ira) + KI(yoshi) + BA(se). Tagline: **"Every gap, golden."**

## 2. Facts to confirm before coding (never guess an address)

Ask the manager, or read them from Mel's Kiyoshi-on-Base config, and write them into one config file with a comment
saying where each came from:

* Chain: Base mainnet (8453); testnet Base Sepolia (84532).
* USDC on Base, cNGN on Base (address, decimals), and which stablecoin Kiyoshi actually holds.
* Uniswap v3 on Base: factory, quoter, router, and the cNGN pool(s) and fee tier Kiyoshi uses.
* Textile on Base: reactor/settlement contract, API base URL, ticker id for cNGN.
* Kiyoshi's Base wallet (the treasury), and how it reports (logs, heartbeat, settings it reads).
* The manager's personal wallet, the fee-recipient wallet, and the owner (should be a Safe multisig or hardware wallet,
  **not** the bot wallet; see section 9).
* Performance fee at launch (the Celo fund runs 15%; hard cap 20%), deposit cap, minimum deposit.

## 3. Brand (use exactly)

* Colours: Night `#070B18`, Base blue `#0052FF` (text-on-dark variant `#4D86FF`), Midas gold `#E9C766`, Paper `#F4F1E8`,
  Profit green `#3DDC97`, naira text `#9FB0D6`, panels `#0D1430`, lines `#1E2A4D`, muted text `#8C98B8`.
* Fonts: Inter Display (wordmark, headings), Inter (text), JetBrains Mono (numbers, labels).
* Wordmark: `MINAKIBA`, Inter Display 600, letter-spacing 0.2em.
* **Logo: a square blue block** with Kiyoshi's five-blade gold fan rising from a gold base line, and the naira's two
  bars cut across the fan. SVG, 100×100 box (draw it from this function):

```js
function logo(size, o = {}) {
  const cx = 50, cy = 60, R = 34, r0 = 5, n = 5, gap = 3.2, gold = '#E9C766', blue = '#0052FF'
  let d = ''
  for (let i = 0; i < n; i++) {
    const a0 = (180 + i * 180 / n + gap / 2) * Math.PI / 180, a1 = (180 + (i + 1) * 180 / n - gap / 2) * Math.PI / 180
    const p = (a, r) => (cx + r * Math.cos(a)).toFixed(2) + ',' + (cy + r * Math.sin(a)).toFixed(2)
    d += `M${p(a0, r0)} L${p(a0, R)} A${R},${R} 0 0 1 ${p(a1, R)} L${p(a1, r0)} Z `
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="${size}" height="${size}">` +
    (o.mono ? `<rect x="3" y="3" width="94" height="94" rx="7" fill="none" stroke="${gold}" stroke-width="2.6"/>`
            : `<rect x="2" y="2" width="96" height="96" rx="8" fill="${blue}"/>`) +
    `<path d="${d}" fill="${gold}"/>` +
    `<rect x="12" y="40.2" width="76" height="2.6" fill="${blue}"/><rect x="12" y="47.6" width="76" height="2.6" fill="${blue}"/>` +
    `<rect x="15" y="61.2" width="70" height="3" rx="1.5" fill="${gold}"/></svg>`
}
```

* Dark, calm, financial. No emoji in the product UI. Labels in small spaced mono caps.

## 4. Repository layout

```
contracts/        FundVault.sol (+ tests), deploy and admin scripts (Foundry or Hardhat)
web/              static site: index.html, app.js, vault.js, styles.css, config.js, vendor/ (bundled libs)
api/              Vercel serverless functions (TypeScript)
db/migrations/    numbered SQL files for Neon, applied in order
docs/             runbooks, security review, weekly reports
vercel.json       headers, CSP, cron schedule
```

## 5. The vault contract (port the Celo FundVault, then apply the changes in 5.2)

### 5.1 Behaviour to keep

* ERC-20 shares, 18 decimals, **non-transferable** (only mint, burn, and moves into and out of the vault's own escrow).
* Members: `requestDeposit(assets)` (allow-listed only, ≥ minDeposit, within the deposit cap; USDC waits in the vault),
  `cancelDeposit()`, `requestRedeem(shares)` (shares go to escrow), `cancelRedeem()`, `claim()`.
  Cancel and claim work **while paused**.
* Operator (keeper wallet, holds no money): `reportValue(nav)` hourly and `settle(nav)` daily. Settlement prices every
  queued redemption and deposit at the same price `nav / supply`; redemptions first, then deposits; deposits pay the
  redemptions, and the treasury tops up or receives the difference (`transferFrom(treasury)` / `transfer(treasury)`).
* A payout to a blocked address goes to `claimable` instead of reverting everyone's settlement.
* Fee: on each redemption, `fee = (gross − costPart) × feeBps`, only on profit, never on a loss; paid as shares to the
  fee recipient (who pays no fee). Cost basis per member, reduced pro rata on partial redemptions.
* Value-jump guard: the reported value may move at most `maxNavChangeBps` from the start of a day-long window.
* Queue limit (100 per side) so `settle` stays within gas.
* One-time `openingShares` at $1.00 per share, before anyone else holds shares.
* Delayed changes, **48 hours**, with queue, cancel and execute (anyone can execute after the delay): owner, operator,
  manager, fee recipient, fee (manager only, capped at 20%), value-jump limit, treasury.
* Pause: owner or manager, immediate, cannot move money; a pause by the owner can only be lifted by the owner.
* Events for everything (DepositRequested, Deposited, RedeemRequested, Redeemed with gross/fee/paid, Claimed,
  ValueReported, Settled, ChangeQueued/Cancelled/Executed, Paused, Allowed, LimitsSet, OpeningShares).

### 5.2 Changes from the Celo version

* **Owner is not the bot wallet.** Owner = a Safe multisig (or hardware wallet). The bot wallet is only the treasury.
* Default `maxNavChangeBps` = **2500** (25%).
* Store the **fund naira rate** with each settlement (an extra field on `Settled`, or a separate `RateReported` event
  from the operator), so naira values on the site can be recomputed from the chain.
* Keep the contract small. No upgradeability, no external calls except USDC.

### 5.3 Tests (must all pass before any deploy)

Deposit/redeem round trip at $1.00 and at a moved price; fee only on profit; partial redemption cost basis; fee
recipient pays no fee; cancel while paused; claimable fallback; value-jump guard; queue limit; every role change waits
48 h and only the right role can queue/cancel; fee cap; non-transferability (`transfer`, `transferFrom` revert);
settle with deposits > payouts and payouts > deposits; empty-fund settle.

Deploy to **Base Sepolia first**, run a full week of fake deposits, redemptions and settlements, then mainnet. Verify
the source on Basescan. After deploy, check the on-chain bytecode matches the build (mask immutables).

## 6. Data (Neon Postgres)

Use the pooled connection string (`DATABASE_URL`) in Vercel; migrations are plain SQL files run in order (keep a
`schema_migrations` table). Tables, with the purpose of each:

* `vault_config` (network, vault address, USDC address, keeper address, last run, last result, last settle day)
* `vault_history` (every report and settlement: nav, supply, price, naira rate, tx, time)
* `pool_tx` (every USDC/cNGN transfer of the treasury wallet, from the chain: block, tx index, time, sent/received,
  amount, counterparty) and `pool_sync` (cursor). Pair into trades in SQL: number each leg by how many sales came before
  it; a trade = its buys plus the one sale that closes it; profit = sale − buys. Exclude transfers to/from the vault and
  the costs pot.
* `treasury_config`, `treasury_days`, `treasury_entries` (the costs pot, section 8)
* `bot_settings` (smart sizing, skip waits, tight first leg: switches the bot reads), `bot_heartbeat`
* `alert_config`, `alert_state` (Telegram, section 10)
* `seasons` (finished seasons with their summary and leaderboard)

**Access rules:** the browser never gets a database connection. Everything goes through `api/` functions, which hold
`DATABASE_URL`. Public endpoints are read-only and return only public data. Admin endpoints require a wallet-signed
session (section 9).

## 7. The fund naira rate

* At each settlement the keeper reads the cNGN price on Uniswap (pool price) and Textile (public ticker mid) and stores
  their **average** as the fund naira rate (₦ per $1). The site shows it in the header ("₦1,600.25 / $1") and uses it
  for every naira value until the next settlement.
* Display: dollars first, naira under it in smaller, softer mono text (`#9FB0D6`). Over ₦1,000: no decimals; under:
  two decimals.

## 8. The running-costs pot (treasury)

* Once a day, after settlement, **10% of the day's trading profit** is carved into the pot. The pot pays the bot's gas
  and the hosting. The manager keeps 100% of the performance fee; none of it goes to the pot.
* The pot is not part of the share price: the keeper reports the treasury wallet's value **minus the pot**. If the pot
  cannot be read or disagrees with the wallet, post nothing that hour (never post a value with the pot inside it).
* When the pot is short, the rest of a cost is paid by the shares' value (recorded, not owed back).
* Withdrawal from the admin page for hosting: **$5–$10, at most once in 30 days, to any address**, paid by the bot
  (record before sending; never to the bot, vault, token, router or reactor addresses).
* Show a pot visualiser (fill level against 60 days of costs), "costs now: $X a day (gas, hosting)", and a full log
  of carves, gas purchases and withdrawals with explorer links.

## 9. Security (built in from day one)

* **Keys:** the bot key lives only where Mel's bot runs. The keeper key lives only in a Vercel encrypted env var (or a
  KMS); it holds gas only. Never log, print, commit or send a key. `.env*` in `.gitignore`.
* **Pinned addresses:** the site takes the vault and USDC addresses **only from its own config file**. If the API names
  different ones, show a safety stop and refuse to send any transaction.
* **Admin = wallet signature**, no passwords: the manager signs a message (domain, nonce, expiry) to open an admin
  session (short-lived, httpOnly cookie). Money actions (pause, approve wallets, limits, fee) are contract calls signed
  in the wallet. "Settle now" posts a message signed by the manager or owner, at most 10 minutes old.
* **Bounded approvals** from the treasury: approve the router, reactor and vault for about twice the pool, topped up by
  the bot, not unlimited.
* **Site:** bundle viem and every library into `web/vendor/` (no runtime CDN scripts), strict CSP
  (`script-src 'self'`; `connect-src` only the API, the Base RPCs and the wallet relays you need), `X-Frame-Options
  DENY`, `Referrer-Policy no-referrer`, `X-Content-Type-Options nosniff`, `X-Robots-Tag noindex`.
* **Alerts on anything that could precede theft:** any queued vault change (section 10), a role that changed, treasury
  transfers to an unknown address, share price −5% in 24 h.
* 2-factor login on GitHub, Vercel, Neon and Telegram. Keep a written incident plan: pause first (cannot move money),
  then investigate.

## 10. Keeper, jobs and alerts (Vercel Cron)

* `api/keeper` every hour at :07: read the treasury's balances, price them in USDC, subtract the pot, `reportValue`;
  at 20:07 UTC `settle` (only when the bot is not mid-trade and the treasury has enough USDC for the payouts; otherwise
  record "settlement waiting" and retry).
* `api/pool-sync` every 2 minutes: read new treasury transfers from the RPC into `pool_tx`. Base RPCs limit `getLogs`
  ranges: read in chunks, keep a cursor, handle 429 with backoff, and fall back to the stored history when the node is
  busy (flag it as stale). Never wipe stored history because a source failed.
* `api/alerts` every 10 minutes: Telegram. Checks: bot silent > 20 min or failing 3 cycles; share price −5% in 24 h;
  keeper stale or failing; settlement missed or waiting; keeper gas < 10 days ("Urgent" < 3); bot gas low; fund paused;
  **any queued vault change** (name, new value, when it takes effect; "if you did not queue it, cancel it and pause");
  daily summary after settlement; a message when the bot buys gas or the pot pays a withdrawal. Each alert once, then
  every 6 hours, then "OK again".
* Telegram webhook: accept only requests carrying the secret header derived from the bot token; answer only the linked
  chat (first private `/start`). Commands: /dashboard (buttons), /status, /today, /queue, /keeper, /smart, /schedule,
  /skipwait, /tight and their _on/_off switches. **No command can move money.** Strip the bot token from any error text.
* Bot interface for Mel's Kiyoshi: a heartbeat POST signed by the treasury wallet (reject stale or replayed ones); a
  settings GET (the switches); the pending pot withdrawal GET. Auto gas top-up from USDC when low; keeper top-up when its
  gas runs below ~10 days.

## 11. The site (port the Celo site's features)

Public dashboard:
* Header: logo, wordmark, fund naira rate, wallet button.
* Tiles: fund value, share price (with naira), profit today, trades today and win rate, costs pot.
* Share price chart; P&L and trade-count charts with an **hour / day / week** toggle; personal P&L for the connected wallet.
* **Naira spread monitor:** cNGN on Uniswap, cNGN on Textile, the fund naira rate, the gap in bps against the minimum
  needed to trade.
* **Execution log** (collapsible, remembers open/closed, 25 per page): time, route ("USDC → cNGN on Uniswap → USDC on
  Textile"), size, gap, profit, status (filled, skipped with the reason).
* Leaderboard (masked wallets, return after fees, manager not ranked); finished seasons with their final record.
* Risk disclosure, fees explained (rate, cap, 48-hour notice), "how it works" in three steps.

Account (connected wallet):
* Position: value with naira, profit after the fee, shares, % of fund, what you put in, fee if you redeemed today,
  **fees you have paid (realized)**; deposit and redeem forms with the price per share and shares received; queued
  items with cancel; "ready to collect" with claim; history from the vault's events with explorer links.
* Phone layout first; works inside wallet browsers (MetaMask, Coinbase Wallet, Rabby).

Admin (manager wallet, signed session):
* Pause/unpause, keeper status and "Settle now", approve/remove wallets (approved list hides revoked wallets that hold
  nothing), deposit limits, queue a fee change, queued changes with Apply/Cancel.
* **Fees earned:** totals (all time, 30 days, 7 days, building up), a row per fee-bearing redemption, copy as CSV.
* Costs pot admin and withdrawal form; holders table.

## 12. Reporting the manager expects

* **Weekly analysis** (weeks run Thursday to Wednesday from the season start): trades, win rate, volume, trading profit,
  profit a day, margin in bps, share price start → end, fund value; "this week against last"; best and worst hours
  (Lagos time), weekdays and trade sizes; costs against profit. Plain language, numbers checked against the chain.
* Season report on request: TL;DR first, then weekly table, daily record, drivers, members and fee ledger, market scan,
  forecast scenarios (Quiet / Typical / Busy / Spike, clearly not promises), next steps, sources and limits.

## 13. Build order

1. Confirm section 2 facts; write `config` with sources.
2. Contract + tests → Base Sepolia → a week of test settlements → mainnet with the Safe as owner → verify on Basescan.
3. Neon: create the project (one `main` branch, a `dev` branch for testing), run migrations.
4. Vercel: project linked to the repo, env vars (`DATABASE_URL`, `KEEPER_PRIVATE_KEY`, `TELEGRAM_BOT_TOKEN`,
   `BASE_RPC_URL`, admin session secret), `vercel.json` with headers, CSP and cron. Confirm auto-deploy actually fires
   on push (the Celo site's did not; check the Deployments list after the first push).
5. Keeper, pool sync, alerts, bot interface.
6. Site: dashboard, account, admin.
7. Security pass (section 9) and a phone test with a real wallet on testnet.
8. Go-live checklist: owner is the Safe, value-jump 25%, approvals bounded, alerts linked, opening shares done, deposit
   cap set, disclosure reviewed.

## 14. Lessons from the Celo fund (do not repeat)

* A source failing must never erase stored trade history. Read from the chain into the database and serve from there.
* The off-chain "unit price" broke when deposits arrived. The share price comes only from the vault's own settlements.
* Keep the pot out of the share price, and refuse to post when it cannot be read.
* RPC log ranges are limited (Celo's was 5,000 blocks); chunk, cursor and back off on 429.
* Node lag can make a swap revert right after the first leg: guard the leftover sale and log "skipped (nothing bought)"
  honestly.
* Mel's second-leg guard (hold instead of selling into a collapsed price) and the tight first-leg tolerance (switchable
  on Telegram) both stay.
* Margin per traded dollar is the number to watch; trades above about $600 lost margin against a thin pool.
* Say "Lagos time" wherever a time is shown to the manager; settlement is 20:07 UTC (21:07 Lagos).

## 15. Working with the manager

Plain language, no jargon, short answers with the numbers. Confirm before anything irreversible or public. Never print
a key or token, even partly. Money actions never go through Telegram. Keep tool and credit use lean: read only what is
needed, verify once, then report.
