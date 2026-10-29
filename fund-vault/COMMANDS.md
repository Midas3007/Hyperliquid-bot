# Midas × Kiyoshi Fund · command sheet

Everything runs in Termux on the phone. Type one command at a time. Commands that send a transaction show
what they will do and wait for you to type `yes`.

Tips
- Keep Termux awake: pull down the Termux notification and tap **Acquire wakelock**.
- Two things at once: swipe from the left edge in Termux, **New session** (one for the bot, one for commands).
- Stop a running program: **Ctrl + C** (Termux extra keys row: CTRL, then C).
- `< >` in this sheet means "put your value here". Type the value without the brackets.
- Never paste a private key into a chat or a website.

---

## 1. The bot (Kiyoshi) · `cd ~/kiyoshi`

**The bot now runs on Render** (setup and daily use: `kiyoshi/HOSTING.md`). Logs, suspend and resume are on
render.com. Only run it on the phone while the Render service is suspended: two copies must never trade at once.

| What | Command |
|---|---|
| Go to the bot | `cd ~/kiyoshi` |
| Get the latest code | `git pull` then `npm install` |
| Balances, rates and a preview quote (sends nothing) | `npm run check` |
| Dry run: scans and reports, sends nothing | `npm run desk` |
| **Trade for real** | `LIVE=1 npm run desk` |
| Trade with a bigger size per cycle | `LIVE=1 MAX_TRADE_USD=50 npm run desk` |
| Only trade when profit is at least 2 cents | `LIVE=1 MIN_EDGE_USD=0.02 npm run desk` |
| Stop the bot | Ctrl + C (it finishes the current cycle first) |

Settings live in `~/kiyoshi/.env` (open with `nano .env`, save with Ctrl+O, Enter, exit with Ctrl+X):
`AGENT_PRIVATE_KEY` (the bot wallet key, never share it), `MAX_TRADE_USD` (default 20), `MIN_EDGE_USD`
(0.01), `INTERVAL_SEC` (60), `MAX_FLATTEN_LOSS_BPS` (50), `MAX_HOLD_MIN` (30).

Note: members' deposits land in the bot wallet when they settle, so the bot's own "P&L" counts them as gains.
The fund's real performance is the share price on the site.

---

## 2. The fund contract · `cd ~/Hyperliquid-bot/fund-vault`

Add `--mainnet` for the real fund. Without it, commands use the testnet.

| What | Command |
|---|---|
| Go to the fund tools | `cd ~/Hyperliquid-bot/fund-vault` |
| Get the latest code | `git pull` then `npm install --omit=dev` |
| **Status**: roles, value, share price, queues, queued changes | `npm run vault -- status --mainnet` |
| **Emergency pause** (instant; nobody's money moves) | `npm run vault -- pause --mainnet` |
| Unpause | `npm run vault -- unpause --mainnet` |
| Allow a wallet to deposit | `npm run vault -- allow <0x…> --mainnet` |
| Stop a wallet depositing (it can still redeem) | `npm run vault -- disallow <0x…> --mainnet` |
| Deposit cap and minimum (USDT) | `npm run vault -- limits <cap> <minimum> --mainnet` e.g. `limits 5000 5` |
| Let settlements pay redemptions from the bot wallet, up to an amount | `npm run vault -- approve <amount> --mainnet` e.g. `approve 2000` |

Role changes (queued for 48 hours, shown on the site while they wait):

| What | Command |
|---|---|
| New manager (your personal wallet's role) | `npm run vault -- set-manager <0x…> --mainnet` |
| New keeper / operator | `npm run vault -- set-operator <0x…> --mainnet` |
| New treasury (where deposits go, payouts come from) | `npm run vault -- set-treasury <0x…> --mainnet` |
| New fee recipient | `npm run vault -- set-fee-recipient <0x…> --mainnet` |
| New owner (the bot wallet's role) | `npm run vault -- set-owner <0x…> --mainnet` |
| How far the value may move in a day (%) | `npm run vault -- set-max-move <percent> --mainnet` |
| Apply a queued change after 48 hours | `npm run vault -- execute <manager / operator / treasury / fee-recipient / owner / max-move> --mainnet` |
| Cancel a queued change | `npm run vault -- cancel <same names> --mainnet` |

One-time and rarely used:

| What | Command |
|---|---|
| Deploy a new vault | `npm run deploy -- --mainnet --keeper 0x4489235601E079988E24F10B866f7D8766d3d028` |
| Opening shares for the personal wallet (once, right after deploying) | `npm run vault -- open live --mainnet` |
| Post the value by hand (only if the keeper is down and you are the operator) | `npm run vault -- report live --mainnet` |
| Settle by hand (same) | `npm run vault -- settle live --mainnet` |
| Testnet only: test USDT for a wallet | `npm run vault -- mint <0x…> <amount>` |

---

## 3. On the website · https://midas-kiyoshi-fund.vercel.app

Open it inside MetaMask or Rabby, then tap **Join with your wallet**.

| Who | Where | What |
|---|---|---|
| Member | Dashboard | Deposit, redeem, cancel a waiting request, collect a payout |
| Member | Account | Wallet, history, P&L card (download / share), disconnect |
| You (personal wallet) | Admin | Pause / unpause, **Settle now** (early settlement when the bot is not in a trade), approve or remove wallets, deposit limits, queue a fee change (48 h), holders list, keeper status |
| You (admin sign-in, bottom of Admin) | Admin | Vault address, fund name, slots, pool wallet |

Automatic: the keeper posts the value every hour (at :07) and settles deposits and redemptions once a day at
**20:07 UTC (9:07 pm Lagos)**. Keeper wallet: `0x4489235601E079988E24F10B866f7D8766d3d028` (top it up with a
little CELO now and then; the Admin page says when it runs out).

---

## 4. If something goes wrong

| Situation | Do this |
|---|---|
| Anything looks wrong with the fund | **Pause** (Admin page, or `npm run vault -- pause --mainnet`), then look |
| Your personal wallet is lost or stolen | `pause`, then `set-manager <new wallet>` and `set-fee-recipient <new wallet>`, wait 48 h, `execute manager`, `execute fee-recipient`, `unpause` |
| A change you did not make is queued | `npm run vault -- cancel <name> --mainnet` |
| Settlement says "waiting" | The bot wallet needs more USDT for payouts: let trades close, or stop the bot briefly |
| Settlements stopped after a while | The payout approval ran out: `npm run vault -- approve <amount> --mainnet` |
| "Value moved more than the daily limit" | Check the bot wallet; if the move is real, `set-max-move <bigger %>` (48 h) |
| Keeper says it needs CELO | Send ~0.5 CELO to the keeper wallet |
