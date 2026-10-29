# Running Kiyoshi on Render

The bot runs as a Render **background worker**. It's always on, restarts itself if it crashes, and redeploys
when the code in `kiyoshi/` changes. The settings are in `render.yaml` at the repository root. It costs about
$7.25 a month: the Starter worker plus a 1 GB disk that keeps the trade log across restarts.

**Never run two copies.** Two copies trading the same wallet collide. Stop the bot on the phone before the cloud
one goes live.

## Setup (once, in the phone's browser)

1. Sign up at https://render.com with GitHub, and give Render access to the **Hyperliquid-bot** repository only.
   Add a card under Billing.
2. Go to **New → Blueprint**, pick **Midas3007/Hyperliquid-bot**, and set the branch to `ccr-bd86732d-umzid7`.
   Render reads `render.yaml` and shows one service, **kiyoshi**, a worker in Frankfurt.
3. It asks for two values:
   - `AGENT_PRIVATE_KEY`: the bot wallet key, copied from `~/kiyoshi/.env` on the phone (`cat ~/kiyoshi/.env`).
     Paste only the key, with no spaces. Render stores it encrypted. Don't paste it anywhere else.
   - `MAX_TRADE_USD`: the same as on the phone (`grep MAX_TRADE ~/kiyoshi/.env`; 20 if it's not there).
4. **First stop the phone bot** (Ctrl + C in its Termux session, and wait for "exiting cleanly"). Then tap
   **Apply**. The first build takes a few minutes. The bot then waits 60 seconds and starts trading.
5. Check it: **kiyoshi → Logs** should show `Kiyoshi LIVE | wallet 0x6cd3…` followed by scan lines every
   minute. The site's trading tiles keep updating, because they read the wallet on-chain.

## Every day: the settlement pause

From **20:00 UTC (9 pm Lagos)** the bot opens no new trades. It only sells leftovers, under the usual loss guard,
so the wallet is all USDT when the fund settles at 20:07. When the keeper reports the day settled (about 20:08),
trading resumes on its own. If settlement is waiting, for example because withdrawals need more USDT, the pause
lasts until it settles, and never past midnight UTC. The logs show `settlement pause: …` while this happens.

## Everyday tasks

| What | Where |
|---|---|
| See what it's doing | Render → kiyoshi → **Logs** |
| Stop trading now | Render → kiyoshi → **Settings → Suspend Service** (it finishes the current cycle first) |
| Start again | **Resume Service** |
| Change a setting (e.g. `MAX_TRADE_USD`) | **Environment** → edit → **Save, rebuild and deploy** |
| Restart | **Manual Deploy → Restart service** (or Deploy latest commit) |
| Emergency on the fund itself | Still the phone: `npm run vault -- pause --mainnet` (see COMMANDS.md) |

The phone keeps the bot wallet key for the fund commands (pause, role changes). Don't start `npm run desk` on
the phone again unless the Render service is suspended.

## Moving back to the phone

Suspend the Render service first, then `cd ~/kiyoshi && LIVE=1 npm run desk` as before.
