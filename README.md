# Hyperliquid Compounding Bot

A Telegram bot that runs your 10-module compounding protocol on Hyperliquid perpetual futures.
Rule-based code does the BTC gate, screener, levels, entry/SL/TP and sizing. Claude
reads the data and news, then picks one of the already-checked setups, or passes. **Every trade
needs your `y`.** Stop-loss and take-profit orders sit on the exchange, so a position is protected even if the bot goes down.

```
you:  start ($11)
bot:  CONFIRMATION CARD ... SOL LONG, entry 142.35, SL 139.80, TP 150.00, 12x, -$1.65 at SL ... reply y
you:  y
bot:  Placed #1 ... (later) #1 entry filled ... (later) #1 CLOSED (tp) +$4.90 + post-trade study
```

## Paper trading: prove it before you fund it

Set `PAPER_TRADING=true` and the bot runs exactly the same analysis on **live Hyperliquid prices**, but fills
are simulated and no order reaches the exchange. It needs no wallet: only `TELEGRAM_BOT_TOKEN`,
`ANTHROPIC_API_KEY` and `PAPER_TRADING=true` (optionally `PAPER_BALANCE`, default $100).

- Paper trades go to their own journal (`paper.db`), so they never mix with real ones.
- Fills are modelled conservatively, so paper results flatter the strategy as little as possible:
  - a limit entry fills only when a 1-minute candle that started after the order trades through it
  - SL and TP exits pay 0.05% slippage plus taker fees
  - when a candle touches both SL and TP, the SL wins
  - the TP isn't counted in the minute the entry fills
  - hourly funding is charged
  - a position with no SL is liquidated and its whole margin is lost
- `/stats` gives the track record: win rate, expectancy per trade, average R-multiple, profit factor and max drawdown.
  It also says whether the sample is big enough (30+ trades) to trust.
- `/paperreset 100` restarts the simulated account.

Run paper mode until `/stats` shows positive expectancy over 30+ trades. Then switch `PAPER_TRADING` off and
add the wallet variables below.

## Run it from your phone (about 20 minutes, all in a mobile browser)

You need four things: a Telegram bot, a Hyperliquid API wallet, an Anthropic API key, and a
host that runs the bot 24/7. The host is [Railway](https://railway.app), about $5/month.

### 1. Telegram bot
1. In Telegram, open **@BotFather** → `/newbot` → choose a name.
2. Copy the **token** it gives you.

### 2. Hyperliquid
1. Deposit USDC to your Hyperliquid account (minimum 5 USDC, via Arbitrum).
   If it lands in **Spot**, go to Portfolio → Transfer → **Spot → Perps**.
2. Open **app.hyperliquid.xyz/API** (in your wallet's browser) → **Generate** an API wallet → Authorize.
   Set the longest validity it allows and note the expiry date.
   Copy the API wallet's **private key**. An API wallet can trade but **cannot withdraw**.
3. Copy your **main wallet address** (the 0x… address that holds the USDC).

Never put your main wallet's private key anywhere. The bot only gets the API wallet key.

### 3. Anthropic API key
[console.anthropic.com](https://console.anthropic.com) → API Keys → Create. Add $5 of credit.
Each `start` costs a few cents.

### 4. Deploy on Railway
1. Sign in to railway.app with GitHub → **New Project → Deploy from GitHub repo** → pick this repo.
2. **Variables** tab: add `TELEGRAM_BOT_TOKEN`, `HL_ACCOUNT_ADDRESS`, `HL_API_PRIVATE_KEY`,
   `ANTHROPIC_API_KEY`, and `TIMEZONE` (e.g. `Africa/Lagos`). Other options are listed in `.env.example`.
3. **Settings → Volumes → Add volume**, mount path `/data`. This keeps the journal across restarts.
4. Deploy. In Telegram, message your bot anything. It replies with your **chat id**.
   Add it as `TELEGRAM_CHAT_ID` and redeploy. Only that chat can control the bot.
5. When the bot says *"Bot online – MAINNET"*, send `start ($11)`.

## Commands

| Command | What it does |
|---|---|
| `start ($14)` | Full autopilot run → confirmation card |
| `start ($14) margin $5` | Same, with a custom margin |
| `y` / `y 1` / `y 2` / `n` | Place the trade / pick long or short when both are shown / discard |
| `/status` | Balance, positions, pending entries |
| `/journal` | Recent trades and realized PnL |
| `/stats` | Track record: win rate, expectancy, R-multiples, profit factor, drawdown |
| `/close SOL` | Market-close a position |
| `/cancel` | Cancel all resting orders (the bot re-attaches SL/TP to any open position) |
| `/mode A` · `/mode B` | Protocol sizing vs capped-risk sizing |
| `/risk 15` | Mode B: % of account lost if the SL hits |
| `/size 80` | Mode A: % of balance used as margin |
| `/lessons` · `/approve N` · `/reject N` | Rules proposed by post-trade studies |
| `/pump` | BTC post-pump reversal check now |
| `/paperreset 100` | Paper mode: restart the simulated account at $100 |

## How each module maps to Hyperliquid

| Module | Implementation |
|---|---|
| 1 BTC gate | 24h BTC change: < −4% skip · −4 to −2% margin capped at $0.50 (skipped if that's under the $10 order minimum) · > +3% size up. Above +2%, the post-pump check runs every 15 min and alerts you |
| 2 Screener | Perps with 24h volume > $50M, ranked by **taker buy/sell ratio** from the live trade stream (order-book depth until 10 min of trades are collected). Skips TAO, coins with EXTREME IV, coins down more than 8% on the day, and coins already lost on today |
| 3 Deep dive | Whale L/S = live positions of the **top 60 leaderboard accounts**. OI from Hyperliquid, with 24h/30d trend from hourly snapshots the bot records itself. Funding shown as an 8h-equivalent. IV rank = Deribit DVOL percentile (BTC/ETH) or realized-volatility percentile. News from public RSS feeds, rated by Claude. Levels on 5m/4h/1d using Bollinger, Fibonacci pivots, VWAP and volume profile |
| 4 Direction | Protocol triggers. 2+ trend-short triggers → short. 2+ reversal-short triggers → card shows long **and** short |
| 5 Entry | Limit order at the 5m level where 2+ methods agree. Never a market entry |
| 6 Risk | SL at the nearest 4h structure, never closer than 1.5%. Leverage `floor(1/(SL+0.5%))`, max 25x, **then lowered further if Hyperliquid's liquidation price would hit before the SL** (see below). Minimum R/R 1:3. Isolated margin. SL and TP are always attached |
| 7 Sizing | **Mode A**: 80–85% of balance as margin, 15–25x. **Mode B (default)**: leverage set so a stop-out loses `/risk`% of the account |
| 8 Management | Mon–Fri only. Max 48h hold. Friday: no entries after 16:00, alarm at 15:00, auto-close at 20:00 (your `TIMEZONE`), min R/R 1:1.5. Unfilled entries cancel after 4h |
| 9 Post-trade study | On every close, Claude compares the plan with what price did afterwards and may propose a rule. You approve it with `/approve` |
| 10 Autopilot | `start ($X)` → card → `y` |

**Liquidation on Hyperliquid vs Bybit.** Hyperliquid's maintenance margin is half the initial margin at the coin's max leverage.
That is bigger than the 0.5% the protocol's formula assumes. Example: a 3% stop at the formula's 25x on a 40x-max coin
gets liquidated around −2.8%, before the stop. The bot checks both formulas and steps leverage down until the liquidation price is beyond the SL.

## Honest risk note
Mode A loses roughly 30–75% of the account on a single stop-out. Mode B (default, 15%) survives a run of losses.
Claude's analysis does not make losses impossible. Only trade money you can lose.

## Development
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q          # offline tests: risk math, levels, paper fills, stats, full start->close flow
cp .env.example .env && python main.py
```
