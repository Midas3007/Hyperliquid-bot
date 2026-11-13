// POST /api/ingest - HEVI pushes its journal here with "Authorization: Bearer <RECORD_TOKEN>".
// The token itself is stored nowhere on this site: Neon keeps only its SHA-256 (table ingest_key, which the writer
// role can read but not change). Writes use the least-privilege hevi_writer role (NEON_WRITER_URL), and only
// whitelisted fields are stored.
import { neon } from "@neondatabase/serverless"
import type { Config } from "@netlify/functions"
import { createHash, timingSafeEqual } from "node:crypto"

const TEXT = ["coin", "strategy", "status", "exit_type", "version"] as const
const NUM = ["entry", "fill_px", "exit_px", "sl", "tp", "result_r", "pnl", "risk_usd", "created_at", "filled_at", "closed_at"] as const
const STATUS = new Set(["pending", "open", "closed", "cancelled"])
// status.note can be a few sentences (demo / backtest explanations)

const sha256 = (s: string) => createHash("sha256").update(s).digest()
const matches = (given: string, storedHex: string | undefined) =>
  given.length >= 32 && /^[0-9a-f]{64}$/.test(storedHex ?? "") &&
  timingSafeEqual(sha256(given), Buffer.from(storedHex as string, "hex"))
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null)
const text = (v: unknown, max = 40) => (typeof v === "string" && v.length <= max ? v : null)
const bad = (msg: string) => Response.json({ ok: false, error: msg }, { status: 400 })

function cleanTrade(t: any) {
  if (!t || typeof t !== "object") return null
  const id = t.id
  if (!Number.isInteger(id) || id < 1) return null
  if (t.side !== "long" && t.side !== "short") return null
  const out: Record<string, unknown> = { id, side: t.side }
  for (const k of TEXT) out[k] = text(t[k])
  for (const k of NUM) out[k] = num(t[k])
  if (!out.coin || !STATUS.has(out.status as string)) return null
  return out
}

function cleanStatus(s: any) {
  // a flat object of short strings / numbers / booleans only
  const out: Record<string, unknown> = {}
  if (!s || typeof s !== "object") return out
  for (const [k, v] of Object.entries(s).slice(0, 30)) {
    if (k.length > 30) continue
    if (typeof v === "string") out[k] = v.slice(0, k === "note" ? 600 : 200)
    else if (typeof v === "boolean" || num(v) !== null) out[k] = v
    else if (v === null) out[k] = null
  }
  return out
}

export default async (req: Request) => {
  const auth = req.headers.get("authorization") ?? ""
  const given = auth.startsWith("Bearer ") ? auth.slice(7).trim() : ""
  if (!given) return new Response("Unauthorized", { status: 401 })
  const sql = neon(Netlify.env.get("NEON_WRITER_URL")!)
  const key = await sql`SELECT sha256 FROM ingest_key WHERE id = 1`
  if (!matches(given, key[0]?.sha256)) return new Response("Unauthorized", { status: 401 })

  let body: any
  try {
    body = await req.json()
  } catch {
    return bad("body must be JSON")
  }
  const account = body?.account
  if (!["paper", "live", "demo", "backtest"].includes(account)) return bad("account must be paper, live, demo or backtest")
  // a backtest run replaces the previous one; the real accounts (paper, live) are only ever upserted
  const replace = body.replace === true
  if (replace && account !== "backtest") return bad("replace is only allowed for the backtest account")
  if (!Array.isArray(body.trades) || body.trades.length > 2000) return bad("trades must be a list (max 2000)")
  if (!Array.isArray(body.equity) || body.equity.length > 10000) return bad("equity must be a list (max 10000)")
  const trades = body.trades.map(cleanTrade)
  if (trades.some((t: unknown) => t === null)) return bad("a trade has missing or invalid fields")
  const equity = body.equity
    .filter((p: any) => Array.isArray(p) && num(p[0]) !== null && num(p[1]) !== null)
    .map((p: number[]) => ({ ts: p[0], equity: p[1] }))
  const status = cleanStatus(body.status)

  const wipe = replace
    ? [sql`DELETE FROM trades WHERE account = 'backtest'`, sql`DELETE FROM equity WHERE account = 'backtest'`]
    : []
  await sql.transaction([
    ...wipe,
    sql`INSERT INTO accounts (account, updated_at, status) VALUES (${account}, now(), ${JSON.stringify(status)}::jsonb)
        ON CONFLICT (account) DO UPDATE SET updated_at = now(), status = EXCLUDED.status`,
    sql`INSERT INTO trades (account, id, coin, side, strategy, status, exit_type, entry, fill_px, exit_px, sl, tp,
                            result_r, pnl, risk_usd, created_at, filled_at, closed_at, version, updated_at)
        SELECT ${account}, t.id, t.coin, t.side, t.strategy, t.status, t.exit_type, t.entry, t.fill_px, t.exit_px, t.sl,
               t.tp, t.result_r, t.pnl, t.risk_usd, to_timestamp(t.created_at), to_timestamp(t.filled_at),
               to_timestamp(t.closed_at), t.version, now()
        FROM jsonb_to_recordset(${JSON.stringify(trades)}::jsonb) AS t(id int, coin text, side text, strategy text,
             status text, exit_type text, entry float8, fill_px float8, exit_px float8, sl float8, tp float8,
             result_r float8, pnl float8, risk_usd float8, created_at float8, filled_at float8, closed_at float8,
             version text)
        ON CONFLICT (account, id) DO UPDATE SET coin = EXCLUDED.coin, side = EXCLUDED.side,
             strategy = EXCLUDED.strategy, status = EXCLUDED.status, exit_type = EXCLUDED.exit_type,
             entry = EXCLUDED.entry, fill_px = EXCLUDED.fill_px, exit_px = EXCLUDED.exit_px, sl = EXCLUDED.sl,
             tp = EXCLUDED.tp, result_r = EXCLUDED.result_r, pnl = EXCLUDED.pnl, risk_usd = EXCLUDED.risk_usd,
             created_at = EXCLUDED.created_at, filled_at = EXCLUDED.filled_at, closed_at = EXCLUDED.closed_at,
             version = EXCLUDED.version, updated_at = now()`,
    sql`INSERT INTO equity (account, ts, equity)
        SELECT ${account}, to_timestamp(e.ts), e.equity
        FROM jsonb_to_recordset(${JSON.stringify(equity)}::jsonb) AS e(ts float8, equity float8)
        ON CONFLICT (account, ts) DO NOTHING`,
  ])
  return Response.json({ ok: true, trades: trades.length, equity: equity.length })
}

export const config: Config = { path: "/api/ingest", method: "POST" }
