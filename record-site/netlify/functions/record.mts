// GET /api/record - the public track record, read with the read-only hevi_reader role (NEON_READER_URL).
// Trades and equity before the account's last reset (status.reset_at, epoch seconds) are left out.
import { neon } from "@neondatabase/serverless"
import type { Config } from "@netlify/functions"

export default async () => {
  const sql = neon(Netlify.env.get("NEON_READER_URL")!)
  const accounts = await sql`SELECT account, updated_at, status FROM accounts ORDER BY account`
  const out = []
  for (const a of accounts) {
    const reset = Number(a.status?.reset_at) || 0
    const trades = await sql`
      SELECT id, coin, side, strategy, status, exit_type, entry, fill_px, exit_px, sl, tp, result_r, pnl, risk_usd,
             extract(epoch FROM created_at)::float8 AS created_at, extract(epoch FROM filled_at)::float8 AS filled_at,
             extract(epoch FROM closed_at)::float8 AS closed_at, version
      FROM trades
      WHERE account = ${a.account} AND coalesce(closed_at, created_at) >= to_timestamp(${reset})
      ORDER BY coalesce(closed_at, filled_at, created_at) DESC
      LIMIT 1000`
    const equity = await sql`
      SELECT extract(epoch FROM ts)::float8 AS ts, equity FROM equity
      WHERE account = ${a.account} AND ts >= to_timestamp(${reset})
      ORDER BY ts
      LIMIT 10000`
    out.push({ account: a.account, updated_at: a.updated_at, status: a.status, trades,
               equity: equity.map((e: any) => [Number(e.ts), e.equity]) })
  }
  return Response.json({ accounts: out }, { headers: { "Cache-Control": "public, max-age=60" } })
}

export const config: Config = { path: "/api/record", method: "GET" }
