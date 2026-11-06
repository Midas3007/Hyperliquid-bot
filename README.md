# Hyperliquid Compounding Bot — Protocol v3.0

A Telegram bot that runs your Futures Challenge Protocol on Hyperliquid perpetual futures.

- **The rules decide.** A pure, unit-tested rules engine (`hlbot/rules/`) runs every step: the BTC gate,
  screener, hard filters, ranking, entry, SL, TP, sizing, circuit breakers and time windows. Every threshold is in
  one file, [`protocol.toml`](protocol.toml).
- **Claude only reads text.** It rates news headlines, writes the post-trade study as a hypothesis, and flags
  thesis-breaking news on an open position. It never picks the trade.
- **Every trade needs your `y`.** Entries are post-only limits with the stop-loss and take-profit attached as
  mark-price triggers on the exchange, so a position is protected even if the bot goes down.

The full spec, the changelog against v2.0/v2.1, the Hyperliquid mapping and the open questions are in
**[PROTOCOL.md](PROTOCOL.md)**.

```
you:  start ($100)
bot:  # Open Limit Order - Protocol v3.0 ... SOL long, $99.61 post-only, SL $98.115 (-1.50%), TP $105.78,
      16x isolated, risk ~$10.00 (10%), net R/R 1:3.77 ... Type "y" to confirm.
you:  y
bot:  Placed #1 ... (later) #1 entry filled ... (later) #1 CLOSED (tp) -> $+37.71 = +3.77R + study
```

## Before the first trade, read this

- **Token unlocks block every trade until you add data.** Module 2 skips a coin with an unlock of 1%+ of supply in
  the hold window + 24h, and missing data = FAIL. No free unlock feed exists, so every coin fails until
  [`calendar.toml`](calendar.toml) `[unlocks]` covers the coming days. See PROTOCOL.md open question Q1.
- **The macro calendar** (CPI / FOMC / NFP) in `calendar.toml` is filled in through 31 Dec 2026. Check the FOMC
  date and extend it before then.
- **Warm-up after deploying:**
  - OI 24h change is a hard filter, and Hyperliquid has no OI history, so the bot records its own. **No coin passes
    for the first 24 hours.**
  - The buy/sell ratio needs 30 minutes of trade-stream data after every restart.
- **Equity** is your perps account value. Use a dedicated sub-account as the challenge firewall if you can.

## Paper trading: prove it before you fund it

Set `PAPER_TRADING=true` and the bot runs exactly the same protocol on **live Hyperliquid prices**, but fills are
simulated and no order reaches the exchange. It needs no wallet: only `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`
and `PAPER_TRADING=true` (optionally `PAPER_BALANCE`, default $100).

- **Separate journal:** paper trades go to `paper.db`, so they never mix with real ones.
- **Conservative fills:**
  - entries are post-only, like live trading: a crossing price is rejected
  - a resting entry fills only when a 1-minute candle that started after the order trades through it
  - exits pay 0.05% slippage plus taker fees
  - a candle that touches both SL and TP counts as the SL
  - hourly funding is charged
  - a position without a stop is liquidated for its whole margin
- **`review`** gives the Module 11 track record in R, including the risk recalibration and whether there's an edge.
- **`/paperreset 100`** restarts the simulated account. Reviews and breakers count from the reset.

Run paper mode until `review` shows positive expectancy over 30+ trades.

## Run it from your phone (about 20 minutes, all in a mobile browser)

You need four things: a Telegram bot, a Hyperliquid API wallet, an Anthropic API key, and a host that runs the
bot 24/7. The host is [Railway](https://railway.app), about $5/month.

### 1. Telegram bot
1. In Telegram, open **@BotFather** → `/newbot` → choose a name.
2. Copy the **token** it gives you.

### 2. Hyperliquid (skip for paper trading)
1. Deposit USDC to your Hyperliquid account (minimum 5 USDC, via Arbitrum).
   If it lands in **Spot**, go to Portfolio → Transfer → **Spot → Perps**.
2. Open **app.hyperliquid.xyz/API** (in your wallet's browser) → **Generate** an API wallet → Authorize.
   Set the longest validity it allows and note the expiry date.
   Copy the API wallet's **private key**. An API wallet can trade but **cannot withdraw**.
3. Copy your **main wallet address** (the 0x… address that holds the USDC).

Never put your main wallet's private key anywhere. The bot only gets the API wallet key.

### 3. Anthropic API key
[console.anthropic.com](https://console.anthropic.com) → API Keys → Create. Add $5 of credit.
Each `start` costs a few cents (one news-rating call), plus one study per closed trade.

### 4. Deploy on Railway
1. Sign in to railway.app with GitHub → **New Project → Deploy from GitHub repo** → pick this repo.
2. **Variables** tab: add `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, and either `PAPER_TRADING=true` or
   `HL_ACCOUNT_ADDRESS` + `HL_API_PRIVATE_KEY`. Other options are listed in `.env.example`.
3. **Settings → Volumes → Add volume**, mount path `/data`. This keeps the journal and the OI/whale history across
   restarts. To edit the calendar without redeploying, copy `calendar.toml` there and set
   `CALENDAR_FILE=/data/calendar.toml`.
4. Deploy. In Telegram, message your bot anything. It replies with your **chat id**.
   Add it as `TELEGRAM_CHAT_ID` and redeploy. Only that chat can control the bot.
5. When the bot says *"Bot online"*, wait out the warm-up (see above), then send `start ($your equity)`.

## Commands

| Command | What it does |
|---|---|
| `start ($100)` | Pre-flight → BTC gate → sweeps → deep dive → filters → ranking → confirmation card |
| `y` / `y 1` / `y 2` | Place the trade after re-checking everything (1 = long, 2 = short when both are shown) |
| `nah` / `skip` / `try another` | Re-sweep fresh candidates, max 2 per session; standards never drop |
| `n` | Discard the card |
| `how is it going` | Price, uPnL in $ and R, distance to TP/SL, funding paid, time left |
| `review` | Module 11 stats in R, the 20-trade review and the risk recalibration |
| `risk 7%` | Risk per trade (max 15%; half-risk states use min(5%, half of it)) |
| `override: [reason]` | Take the closest setup blocked only by soft rules. Hard rules never move |
| `/status` | Equity, peak, drawdown, breakers, positions |
| `/journal` | Recent trades in R |
| `/close SOL [reason]` | Market-close (logged as discretionary and compared with the bracket) |
| `/cancel` | Cancel resting entry orders (TP/SL are never removed) |
| `/lessons` · `/approve N` · `/reject N` | Hypotheses from post-trade studies; promoting one needs 5+ supporting trades and 20 closed |
| `/pump` | BTC post-pump check now |
| `/paperreset 100` | Paper mode: restart the simulated account |

## The protocol at a glance

| | |
|---|---|
| Risk | 10% of equity at the SL (half 5%, ceiling 15%), sized only by `notional = equity × risk ÷ (SL% + C)`, C = maker + taker + 0.05% (0.11% at base tier) |
| Leverage | `floor(1 ÷ (SL% + MMR + 2%))`, MMR from Hyperliquid's margin tables, capped at 25x and the coin's max; liquidation ≥ 1.5% beyond the SL |
| SL / TP | SL beyond 4H structure + 0.25×ATR, never under 1.5%. TP in front of the first 4H level, ≤ 1.2× ADR, net R/R ≥ 1:3 |
| BTC gate | ≤ −4% no trades · −4 to −2% half risk · ≥ +3% post-pump check, never a size-up |
| Window | Entries Mon–Thu UTC, flat by Fri 20:00 UTC, 48h time stop, unfilled entries cancel after 4h |
| Breakers | SL → done for the day · 2 SLs → half risk · 3 SLs → 48h pause + review · −30% from peak → half risk · −50% → 7-day stop |

Small accounts: Hyperliquid's minimum order is $10. Below $6 equity, risk may rise only as far as that minimum
needs (max 15%). Nothing is tradeable at $1 (worked examples in PROTOCOL.md).

## Honest risk note
A 10% risk per trade loses about 27% of the account over three straight stop-outs; the breakers exist for that.
No rule set makes losses impossible. Only trade money you can lose.

## Development
```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q   # offline: rules engine, worked examples, paper fills, full start -> close flows
cp .env.example .env && python main.py
```
Change thresholds in `protocol.toml`. The loader rejects unknown or missing keys, so a typo fails at startup
instead of silently using another number.
