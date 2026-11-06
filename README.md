<p align="center"><img src="assets/hevi.webp" alt="HEVI" width="520"></p>

# HEVI — autonomous Hyperliquid trading bot (Protocol v3.1)

HEVI trades Hyperliquid perpetual futures **on its own** and reports to you on Telegram. It follows your Futures
Challenge Protocol to the letter:

- **The rules decide.** A pure, unit-tested rules engine (`hlbot/rules/`) runs every step: the BTC gate,
  screener, hard filters, ranking, entry, SL, TP, sizing, circuit breakers and time windows. Every threshold is in
  one file, [`protocol.toml`](protocol.toml).
- **Autopilot (default).** HEVI scans every 30 minutes, Mon–Thu UTC. It places any setup that passes every rule,
  manages it, closes it and studies it.
  - You get a message for every trade and a daily report.
  - `/pause` and `/resume` are your kill switch.
- **Protected on the exchange.** Entries are post-only limits with the stop-loss and take-profit attached as
  mark-price triggers. If HEVI's process dies, your position stays protected.
- **Prove, then live (default).** HEVI paper-trades on live prices until its paper record shows an edge: at
  least 20 trades, +0.10R per trade and wins at least 1.2× losses. Then it switches to real money by itself.
- **Claude only reads text.** It rates news headlines, writes the post-trade study, and flags thesis-breaking
  news. It never picks or sizes trades.

The full spec, changelog, Hyperliquid mapping and decisions are in **[PROTOCOL.md](PROTOCOL.md)**.

```
HEVI:  # HEVI - Open Limit Order - Protocol v3.1 (Hyperliquid) ... SOL long, $99.61 post-only,
       SL $98.115 (-1.50%), TP $105.78, 16x isolated, risk ~$10.00 (10%), net R/R 1:3.74
       HEVI autopilot: every rule passed - placing it now.  Placed #1 ...
HEVI:  #1 SOL entry filled at ~99.61.
HEVI:  #1 SOL LONG CLOSED (tp) at 105.7271 -> $+37.71 = +3.77R + study
HEVI:  HEVI daily report - Tue 10 Nov (UTC) | Mode: PROVING ... | go-live gate: 1/20 paper trades so far
```

## What to expect, honestly
- **HEVI will lose some trades.** The protocol caps each loss (10% of equity at the SL) and pauses after losing
  streaks. It can't make losses impossible, and nothing here guarantees a profit.
- **It trades rarely.** Most scans end in "no trade": a setup has to pass every filter, net 1:3 R/R and the
  time windows. That's by design.
- **Warm-up:**
  - Hyperliquid has no open-interest history, so HEVI records its own. **No coin passes for the first 24 hours
    after the first deploy.**
  - After every restart, HEVI waits 30 minutes for trade-flow data.
- **Proof takes time.** At a few trades a week, the 20-trade paper record takes weeks. `MODE=live` skips it, at
  your own risk.

## Run it on your phone with Termux

Termux is an Android terminal; install it from **F-Droid** (the Play Store version is outdated). The phone has to
stay on, charged and online for HEVI to work. For 24/7 reliability, use Railway (next section).

1. **Create the Telegram bot.** In Telegram, open **@BotFather** → `/newbot`, and copy the **token**. HEVI sets
   its own name, description and command menu when it starts.
2. **Create an Anthropic key.** [console.anthropic.com](https://console.anthropic.com) → API Keys → Create, then add
   about $5 of credit.
3. **Get the code** in Termux. The repo is private, so log in with GitHub first:
   ```
   pkg install -y git gh
   gh auth login                      # GitHub.com -> HTTPS -> login with a web browser
   gh repo clone midas3007/hyperliquid-bot
   cd hyperliquid-bot
   git checkout ccr-7f54350e-hxg4x6   # the HEVI branch (skip once it's merged into main)
   ```
4. **Install.** This builds everything on the phone and takes 20–40 minutes the first time:
   ```
   bash scripts/termux-setup.sh
   ```
5. **Configure.** Run `nano .env`. Fill in `TELEGRAM_BOT_TOKEN` and `ANTHROPIC_API_KEY`, keep `MODE=prove`, then
   save (Ctrl+O, Enter) and exit (Ctrl+X).
6. **Start HEVI:**
   ```
   bash scripts/termux-run.sh
   ```
   Message your bot in Telegram. It replies with your **chat id**. Put it in `.env` as `TELEGRAM_CHAT_ID`, then
   restart (Ctrl+C twice, then run the start command again). Only that chat can control HEVI.
7. **Keep it alive:**
   - Android Settings → Apps → Termux → Battery → **Unrestricted**.
   - Leave the Termux notification on.
   - `termux-run.sh` holds a wake lock and restarts HEVI if it crashes.
8. **When HEVI may go live:** add `HL_ACCOUNT_ADDRESS` and `HL_API_PRIVATE_KEY` (see Hyperliquid setup below).
   - With `MODE=prove`, nothing changes until the paper record passes the gate; then HEVI switches by itself and
     tells you.
   - Without the keys, it keeps paper trading and tells you when the record is ready.

## Run it on Railway (24/7, about $5/month)
1. Sign in to railway.app with GitHub → **New Project → Deploy from GitHub repo** → pick this repo.
2. **Variables:** add `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `MODE=prove`, `DATA_DIR=/data`, plus the two
   `HL_*` keys if you want the automatic switch to live.
3. **Settings → Volumes → Add volume**, mount path `/data`. This keeps the journal and market history across
   restarts.
4. Deploy, message the bot, add the `TELEGRAM_CHAT_ID` it replies with, and redeploy.

## Hyperliquid setup (needed for real money)
1. Deposit USDC to Hyperliquid (minimum 5 USDC, via Arbitrum). If it lands in **Spot**: Portfolio → Transfer →
   **Spot → Perps**.
2. **app.hyperliquid.xyz/API** → **Generate** an API wallet → Authorize, with the longest validity it allows.
   Copy its **private key** into `HL_API_PRIVATE_KEY`. An API wallet can trade but **cannot withdraw**.
3. Copy your **main wallet address** (the 0x… address holding the USDC) into `HL_ACCOUNT_ADDRESS`.

Never put your main wallet's private key anywhere. A dedicated Hyperliquid sub-account is the best firewall: HEVI
can never lose more than what's in it.

## Telegram commands
You don't need any of these; HEVI runs by itself.

| Command | What it does |
|---|---|
| `/status` | Mode, equity, peak, drawdown, breakers, positions, autopilot state |
| `/pause` · `/resume` | Stop / restart opening new trades. Open positions stay protected and managed |
| `how is it going` | Live price, uPnL in $ and R, distance to TP/SL, funding, time left |
| `review` (`/review`) | Track record in R: hit rate, expectancy, drawdown, exit types, risk recalibration |
| `/journal` | Recent trades in R |
| `/close SOL [reason]` | Close a position yourself (logged as discretionary and compared with the bracket) |
| `/cancel` | Cancel resting entry orders (TP/SL are never removed) |
| `/lessons` · `/approve N` · `/reject N` | Hypotheses from post-trade studies |
| `/pump` | BTC post-pump check now |
| `/paperreset 100` | Restart the simulated account (also restarts the go-live proof) |
| `risk 7%` | Risk per trade (max 15%) |
| `start ($100)` → `y` | Manual trading: a confirmation card, placed only on your `y` (use with `/pause`) |

## The protocol at a glance

| | |
|---|---|
| Risk | 10% of equity at the SL (half 5%, ceiling 15%). `notional = equity × risk ÷ (SL% + C)`; C = maker + taker + 0.05% |
| Leverage | `floor(1 ÷ (SL% + MMR + 2%))`, MMR from Hyperliquid's margin tables, max 25x; liquidation ≥ 1.5% beyond the SL |
| SL / TP | SL beyond 4H structure + 0.25×ATR, never under 1.5%. TP in front of the first 4H level, ≤ 1.2× ADR, net R/R ≥ 1:3 after fees, slippage and funding |
| Direction | Long needs 4 of 5 triggers; shorts need 2+ trend or reversal triggers. Sweeps cover buy flow, momentum and sell flow |
| BTC gate | ≤ −4% no trades · −4 to −2% half risk · ≥ +3% post-pump check, never a size-up |
| Window | Entries Mon–Thu UTC, flat by Fri 20:00 UTC, 48h time stop, unfilled entries cancel after 4h |
| Breakers | SL → done for the day · 2 SLs → half risk · 3 SLs → 48h pause + review · −30% from peak → half risk · −50% → 7-day stop (needs you) |
| Data | Missing or stale (> 15 min) data = no trade. News: crypto RSS + Google News. Macro calendar: ForexFactory feed + `calendar.toml` |

## Development
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q   # offline: rules engine, worked examples, paper fills, autopilot, full start -> close flows
cp .env.example .env && python main.py
```
Change thresholds in `protocol.toml`. The loader rejects unknown or missing keys, so a typo fails at startup
instead of silently using another number.
