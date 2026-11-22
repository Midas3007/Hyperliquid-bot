# Security review: Midas × Kiyoshi Fund (22 November 2026)

Six layers checked against the live code, the live database, the live contract and the chain. No system can be promised
"unhackable"; the aim is to know where the money can be lost, close what can be closed, and limit and detect the rest.

## Where the money is, and what controls it

| Role (on chain) | Address | What it can do | Where its key lives |
|---|---|---|---|
| Treasury **and** owner | bot wallet `0x6Cd3…81dC` (plain wallet) | Holds and trades all the fund's money; pauses; queues role changes (48 h) | Phone `.env` and Render env var |
| Operator (keeper) | `0x4489…d028` (plain wallet) | Reports the fund value (max ±50% a day) and settles | Supabase Vault |
| Manager and fee recipient | `0xf95E…3113` (plain wallet) | Pause, allow-list, deposit limits, fee (max 20%, 48 h); holds ~80% of shares | Your personal wallet |

USDT approvals from the bot wallet: **unlimited** to the vault, Uniswap's router, Textile's reactor and Permit2;
cNGN unlimited to the router and the reactor.

## Step 1: vault contract — sound

* The live contract is **byte-for-byte the reviewed code** (checked against the build, only compiler metadata differs).
* Shares cannot be transferred; redemptions pay only the owner of the shares; members can cancel queued deposits and
  redemptions, and collect payouts, even while paused; a blocked payee cannot stop settlement; reentrancy guarded.
* Fee capped at 20%, every role or fee change waits 48 hours. History: the only change ever made was the fee,
  10% → **15%**, queued 7 Nov and applied 10 Nov **from the manager wallet**. No owner, treasury, operator or manager
  change has ever been queued.
* Design limits (not bugs): the contract never holds the traded money (the bot wallet does), and it trusts the keeper's
  reported value within ±50% a day.

## Step 2: keys and wallets — the main risk

* **The bot wallet key is the crown jewel.** Whoever has it can take everything in the trading wallet at once; no
  contract rule can stop that, because the money must sit there to trade. It is also the vault's owner.
* The keeper key (Supabase) can mis-report the value by up to 50% a day. With an allow-listed accomplice that could move
  value between members. The ±50% limit is very wide now that daily moves are 0–3%.
* Unlimited approvals mean a hacked venue contract (Textile's reactor especially, if it is upgradeable) could pull the
  approved tokens.

## Step 3: database (Supabase) — good

* Row-level security is on for every table. Tables without policies are closed to the public; they are reached only
  through named functions.
* All 19 admin functions check for an admin; there is exactly one login (the admin).
* **Was:** the site took the vault address from the database first, so anyone with the admin password could repoint
  every member's deposit at a fake contract. **Fixed** (below).
* Advisor notes: leaked-password protection is off (turn on); `pg_net` sits in the public schema (low).

## Step 4: website — one fix made, two to do

* Clickjacking blocked (X-Frame-Options DENY); no referrer; no MIME sniffing.
* Libraries come from a public CDN (supabase-js, viem) without an integrity check, and there is no Content Security
  Policy. A compromised CDN could alter what the site asks wallets to sign. **To do:** bundle viem into the site and add
  an integrity hash and a CSP (needs a wallet test on phone after the change).

## Step 5: bot (Render) — good

* Runs as a background worker: no public address. Its local server only builds unsigned quotes; nothing signs or sends.
* Telegram: only messages carrying the secret header are accepted, and only the linked chat is answered; commands
  cannot move money. Treasury payouts are capped at $5–$10 once in 30 days, to an address the admin enters.
* No keys, tokens or secrets in git history; `.env` is ignored.

## Step 6: monitoring and response — one gap closed

* Already alerted: bot silent or failing, share price −5% in 24 h, settlement missed, keeper problems or low gas, pause.
* **Was missing:** an alert when a vault change is queued. The 48-hour delay only protects you if someone notices.
  **Fixed** (below).

## Changes made today

1. **The site uses only its own vault and USDT addresses.** If the database names different ones, the site shows a
   safety stop and refuses to send any transaction.
2. **New Telegram alert `vault_change`:** any queued change to owner, operator, manager, fee recipient, fee, daily
   value limit or treasury is reported every 6 hours until it is cancelled or applied.

## What you should do (in order)

1. **Turn on 2-factor login** (an authenticator app, not SMS) for GitHub, Render, Supabase, Vercel, Telegram and the
   email behind them. A Render login is as good as the bot key.
2. **Move the vault's owner role off the bot wallet** to a hardware wallet or a Safe multisig (queued from the bot
   wallet, 48 h). A stolen bot key then still empties the trading wallet but can no longer change roles.
3. **Lower the daily value limit** from 50% to 25% (owner, 48 h). Daily moves are now 0–3%; the spike day was +18.7%.
4. **Supabase:** turn on leaked-password protection, keep sign-ups disabled, use a long unique admin password.
5. **Approvals:** cap the bot's token approvals to about twice the pool instead of unlimited (bot change; I can do it).
6. **Manager wallet:** keep it on a hardware wallet. It holds most of the fund's shares and can raise the fee.
7. **Site:** bundle viem, add the integrity hash and a CSP (I can do it; needs a phone wallet test).
8. **Incident plan:** if anything looks wrong, pause the fund from the Admin page first (it cannot move money), then
   check the bot wallet and the queued changes.
