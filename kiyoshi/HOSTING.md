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

## Gas (CELO) for the bot

The bot pays for every transaction in CELO from the bot wallet. When it drops below **1 CELO** you get a Telegram
alert. Below **0.5 CELO** the bot buys **5 CELO** with USDT on Uniswap by itself. It spends at most **$0.50** per
top-up (if 5 CELO costs more, it buys what $0.50 gets), at most once every 6 hours, and never while it holds leftover inventory or during the settlement pause.
Telegram tells you each time, with the transaction link. The USDT spent leaves the fund's value (CELO is not
counted), so the cost is shared by every shareholder in proportion to their shares. `/status` shows the bot's
gas. Settings (Render → Environment, all optional): `GAS_TOPUP_BELOW` (0.5), `GAS_TOPUP_CELO` (5),
`GAS_TOPUP_MAX_USD` (0.5).

## Telegram alerts

The fund's `fund-alerts` function (Supabase) checks every 10 minutes and messages you on Telegram when:
the bot stops reporting (20+ minutes) or fails 3 cycles in a row, the share price drops 5% below its 24-hour
high, settlement is waiting or hasn't happened by 21:00 UTC, the keeper errors or hasn't run for 90 minutes, the
keeper wallet is below 0.1 CELO, the bot wallet is below 1 CELO, or the fund is paused. Each alert comes once, is repeated every 6 hours while it
lasts, and is followed by "✅ OK again". After each daily settlement you also get a one-line summary. The bot on
Render reports in every 5 minutes, and signs each report with the bot wallet key, so nobody else can fake it.

Commands. They answer instantly and are read-only (pause, settle and approvals stay on the site and the phone).
Tap "/" in the chat for the menu:

| Command | Answers with |
|---|---|
| `/status` | When the bot last reported (and whether it is paused, holding or erroring), fund value and share price, keeper's last run |
| `/today` | Today's trades (Lagos time): count, win rate, profit before gas, volume, last trade |
| `/queue` | Deposits and withdrawals waiting, next settlement time, and whether the bot wallet has enough USDT for the withdrawals |
| `/keeper` | Keeper gas (CELO), whether it is still the vault operator, last run and last daily settlement |
| `/help` | What the bot does and the command list |

Only the linked chat gets answers. Messages from anyone else are ignored.

Setup (once):
1. In Telegram, open **@BotFather**, send `/newbot`, and pick a name and a username ending in `bot`. BotFather
   replies with a **token** (`123456:ABC…`). Keep it private.
2. In Supabase: **Edge Functions → Secrets → Add new secret**. Name: `TELEGRAM_BOT_TOKEN`, value: the token.
   Save.
3. Open your new bot in Telegram (BotFather sends its link) and tap **Start**. Within 10 minutes it replies
   "Alerts connected". Only the first chat that sends /start is linked.

To link a different chat later, ask Claude to reset the alert chat.

## Updating the bot from Mel's repository

Mel keeps improving Kiyoshi at https://github.com/MELcodes99/kiyoshi. To bring those changes in, ask Claude to
"update the bot from Mel's". Claude then:
1. reads what Mel changed,
2. merges it into `kiyoshi/` and keeps Midas's additions (settlement pause, heartbeat, these docs),
3. runs the type check and tests, and
4. pushes outside 20:00–20:10 UTC.

Render then redeploys by itself: the old copy finishes its cycle, and the new one waits 60 seconds before
trading. Check the Logs for the `Kiyoshi LIVE` line afterwards. If the new version misbehaves, go to **Manual
Deploy → Deploy a specific commit** and pick the previous one.

Mel's changes are not pulled in automatically on purpose. This bot trades the fund's money, so each update is
read before it goes live.
