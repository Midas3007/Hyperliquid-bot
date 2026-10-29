# Midas × Kiyoshi Fund vault (Phase 1)

A Celo contract that replaces the Supabase ledger. Members deposit USDT and get fund shares (MKF); they redeem
shares for USDT. Deposits and redemptions wait in a queue and are settled together once a day at the fund value
the bot reports, so nobody can time the bot. The money is traded from the bot wallet, as today.

**Not audited.** Try everything on the testnet first, then start mainnet with a small deposit cap.

| Role | Wallet | Can |
|---|---|---|
| owner | bot wallet | pause / unpause (instant); replace any role (48-hour delay); cancel queued changes |
| treasury | bot wallet | receives deposits at settlement, pays redemptions (approves the vault once) |
| operator | keeper wallet (server) | reports the value hourly and settles once a day; holds no money |
| manager | personal wallet | pause (from the site), approve wallets, set the deposit cap; change the fee (48-hour delay, never above 20%) |
| fee recipient | personal wallet | receives the 10% fee as shares; pays no fee itself |

Settling is automatic: the `vault-keeper` function on Supabase runs every hour with its own wallet, created on
the server (its key never leaves Supabase). The site's Admin page shows its address; send it a little CELO.
A pause by the bot wallet can only be lifted by the bot wallet.

Rules the contract enforces: payouts go only to the member who owns the shares; shares cannot be transferred in
this version; the reported value may move at most 50% a day; queued deposits and redemptions can be cancelled
until they settle, even while paused.

## On the phone (Termux)

```sh
cd ~ && git clone -b ccr-bd86732d-umzid7 https://github.com/Midas3007/Hyperliquid-bot.git   # once
cd ~/Hyperliquid-bot/fund-vault && git pull
npm install --omit=dev        # viem, dotenv and the OpenZeppelin sources; no compiler needed
```

The commands sign with the bot wallet key. They read `AGENT_PRIVATE_KEY` from `fund-vault/.env`, or from
`~/kiyoshi/.env` / `~/midas-arb/.env` if you already keep it there. The key is never printed.

### 1. Testnet first (free)
1. Get test CELO for the bot wallet: https://faucet.celo.org/celo-sepolia
2. `npm run deploy -- --testnet --keeper <keeper address from the site's Admin page>`
3. `npm run vault -- approve`
4. `npm run vault -- mint <bot wallet> 117` then `npm run vault -- open live` (opening shares at $1.00)
5. `npm run vault -- limits 1000 1` and `npm run vault -- allow <a test wallet>`
6. On the site's Admin page (signed in), paste the new vault and tUSDT addresses under "On-chain fund contract".
7. Send the keeper wallet some test CELO (same faucet). Deposit from the approved wallet on the site; the keeper
   settles after 20:00 UTC.

### 2. Mainnet
Same steps with `--mainnet`, without `mint`. `open live` turns everything the bot wallet holds into your
opening shares, so run it when the bot is flat (no half-finished trade).

### Everyday
| Command | What it does |
|---|---|
| `npm run keeper -- --mainnet` | backup only: the server keeper normally does this (needs KEEPER_PRIVATE_KEY) |
| `npm run vault -- status --mainnet` | roles, value, share price, queues, queued changes |
| `npm run vault -- pause --mainnet` | emergency stop (instant) |
| `npm run vault -- set-manager 0x… --mainnet` | queue a new manager (48 h); also `set-operator`, `set-fee-recipient`, `set-owner`, `set-max-move` |
| `npm run vault -- execute manager --mainnet` | apply it after 48 h |
| `npm run vault -- cancel manager --mainnet` | cancel it |

Every command that sends a transaction shows what it will do and waits for `yes`.

## Development
`npm install` (with the dev tools), `npm run build` (compile), `npm test` (local chain).
