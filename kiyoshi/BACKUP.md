# Kiyoshi backup

Copy of https://github.com/MELcodes99/kiyoshi at commit `22874cc`, updated on 2026-10-29, plus Midas's additions below.

The original repository has no licence file, so its code is all rights reserved by its author (Mel Okoye).
This copy is a private backup for running the bot; do not republish it.

To run it on a new device, follow README.md, but clone this repository instead and `cd kiyoshi`.

## Midas's additions
- `docs/FUND_VAULT_PLAN.md`: the on-chain fund plan.
- `src/settle-pause.ts` (+ 7 lines in `src/desk.ts`, `test/settle-pause.test.ts`): no new trades from 20:00 UTC
  until the fund's daily settlement is done. Only active when `FUND_SUPABASE_URL` / `FUND_SUPABASE_KEY` are set.
- `src/heartbeat.ts` (+ 2 lines in `src/desk.ts`): a signed report to the fund's Telegram alerts every 5 minutes.
- `src/gas.ts`, `src/gas-math.ts` (+ 4 lines in `src/desk.ts`): buys 5 CELO with USDT when gas falls below 0.5 CELO.
- `HOSTING.md` and `../render.yaml`: running the bot on Render, alerts, and updating from Mel's.

To refresh the bot code from Mel's repo later, ask Claude to "update the bot from Mel's" (see HOSTING.md).
