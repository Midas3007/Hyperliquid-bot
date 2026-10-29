// Telegram alerts for the fund, run by the database every 10 minutes (pg_cron).
//   - Linking: the first private chat that sends /start to the Telegram bot gets the alerts (TELEGRAM_BOT_TOKEN,
//     an Edge Function secret, comes from @BotFather).
//   - Heartbeat: the Kiyoshi bot posts {heartbeat: {message, signature}} every few minutes, signed by the bot
//     wallet (the vault's treasury), so nobody can fake it.
//   - Checks: bot silent or erroring, share price down sharply, settlement missed or waiting, keeper errors or
//     low on CELO, fund paused; and a short daily summary after the settlement.
// Each alert is sent once when it starts, reminded every 6 hours, and followed by an "OK again" message.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'
import { createPublicClient, defineChain, formatEther, http, isAddress, parseAbi } from 'npm:viem@2.21.0'
import { celo } from 'npm:viem@2.21.0/chains'

const SETTLE_HOUR_UTC = 20
const REMIND_MS = 6 * 3_600_000
const SILENT_MS = 20 * 60_000
const PRICE_DROP = 0.05 // share price 5% below its 24-hour high
const MIN_KEEPER_CELO = 0.1
const celoSepolia = defineChain({
  id: 11142220, name: 'Celo Sepolia', nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 },
  rpcUrls: { default: { http: ['https://forno.celo-sepolia.celo-testnet.org'] } },
})
const NETS: Record<string, any> = {
  mainnet: { chain: celo, rpc: 'https://forno.celo.org', explorer: 'https://celoscan.io' },
  testnet: { chain: celoSepolia, rpc: 'https://forno.celo-sepolia.celo-testnet.org', explorer: 'https://celo-sepolia.blockscout.com' },
}
const vaultAbi = parseAbi(['function paused() view returns (bool)', 'function treasury() view returns (address)', 'function queueLengths() view returns (uint256, uint256)'])
const SITE = 'https://midas-kiyoshi-fund.vercel.app'

Deno.serve(async (req) => {
  const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type', 'Access-Control-Allow-Methods': 'POST, OPTIONS' }
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })
  const reply = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json', ...cors } })
  const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const { data: vc } = await db.from('vault_config').select('*').eq('id', 1).single()
  const net = NETS[vc?.network] ?? NETS.mainnet
  const pub = createPublicClient({ chain: net.chain, transport: http(net.rpc) })
  const vault = vc?.vault_address as `0x${string}` | undefined
  const body = req.method === 'POST' ? await req.json().catch(() => null) : null

  // ---------- heartbeat from the bot ----------
  if (body?.heartbeat) {
    try {
      const { message, signature } = body.heartbeat
      const hb = JSON.parse(String(message))
      if (hb.kind !== 'kiyoshi-heartbeat' || !(Math.abs(Date.now() - Date.parse(hb.at)) < 10 * 60_000)) throw new Error('stale or malformed')
      if (!vault || !isAddress(vault)) throw new Error('no vault configured')
      const treasury = await pub.readContract({ address: vault, abi: vaultAbi, functionName: 'treasury' })
      if (!(await pub.verifyMessage({ address: treasury, message: String(message), signature }).catch(() => false))) throw new Error('not signed by the bot wallet')
      const { data: last } = await db.from('bot_heartbeat').select('at').eq('id', 1).single()
      if (last?.at && Date.parse(hb.at) <= Date.parse(last.at)) throw new Error('replayed')
      await db.from('bot_heartbeat').update({ at: hb.at, equity: hb.equity ?? null, error: hb.error ?? null, error_streak: hb.errorStreak ?? 0, pause: hb.pause ?? null, holding: !!hb.holding, received_at: new Date().toISOString() }).eq('id', 1)
      return reply({ ok: true })
    } catch (e) { return reply({ error: `heartbeat: ${(e as Error).message.split('\n')[0]}` }) }
  }

  // ---------- Telegram ----------
  const token = Deno.env.get('TELEGRAM_BOT_TOKEN')
  if (!token) return reply({ skipped: 'TELEGRAM_BOT_TOKEN is not set' })
  const tg = async (method: string, params: Record<string, unknown>) => {
    const r = await fetch(`https://api.telegram.org/bot${token}/${method}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) })
    return r.json()
  }
  const { data: ac } = await db.from('alert_config').select('*').eq('id', 1).single()
  let chat = ac?.chat_id as number | null
  // Link the first private chat that sends /start
  const upd = await tg('getUpdates', { offset: Number(ac?.tg_offset ?? 0), timeout: 0, allowed_updates: ['message'] })
  if (upd.ok && upd.result.length) {
    let offset = Number(ac?.tg_offset ?? 0)
    for (const u of upd.result) {
      offset = Math.max(offset, u.update_id + 1)
      const m = u.message
      if (!m || m.chat?.type !== 'private') continue
      if (!chat && /^\/start/.test(m.text ?? '')) {
        chat = m.chat.id
        await db.from('alert_config').update({ chat_id: chat, chat_name: m.from?.first_name ?? null, linked_at: new Date().toISOString() }).eq('id', 1)
        await tg('sendMessage', { chat_id: chat, text: `Alerts connected. This chat will get the Midas × Kiyoshi Fund alerts: bot stopped or erroring, share price down sharply, settlement missed, keeper problems, fund paused, and a daily settlement summary.` })
      } else if (chat && m.chat.id === chat && /^\/status/.test(m.text ?? '')) {
        await tg('sendMessage', { chat_id: chat, text: await statusText() })
      }
    }
    await db.from('alert_config').update({ tg_offset: offset }).eq('id', 1)
  }
  if (!chat) return reply({ skipped: 'no chat linked yet: send /start to the Telegram bot' })
  const send = (text: string) => tg('sendMessage', { chat_id: chat, text, disable_web_page_preview: true })

  async function statusText() {
    const { data: hb } = await db.from('bot_heartbeat').select('*').eq('id', 1).single()
    const { data: v } = await db.from('vault_config').select('*').eq('id', 1).single()
    const { data: h } = await db.from('vault_history').select('nav, price, at').eq('vault_address', v?.vault_address ?? '').order('at', { ascending: false }).limit(1).maybeSingle()
    const ago = (t?: string | null) => (t ? `${Math.round((Date.now() - Date.parse(t)) / 60_000)} min ago` : 'never')
    return [
      `Bot: last heartbeat ${ago(hb?.at)}${hb?.pause ? ` · ${hb.pause}` : ''}${hb?.error ? ` · error: ${hb.error}` : ''}`,
      h ? `Fund: $${Number(h.nav).toFixed(2)} · share price $${Number(h.price).toFixed(4)}` : 'Fund: no value posted yet',
      `Keeper: ${ago(v?.last_run_at)} · ${v?.last_result ?? ''}`,
      SITE,
    ].join('\n')
  }

  // ---------- checks ----------
  const found: Record<string, string | null> = {}
  const now = new Date(), today = now.toISOString().slice(0, 10)
  const { data: hb } = await db.from('bot_heartbeat').select('*').eq('id', 1).single()
  found.bot_silent = hb?.at && Date.now() - Date.parse(hb.at) > SILENT_MS
    ? `The bot has not reported for ${Math.round((Date.now() - Date.parse(hb.at)) / 60_000)} minutes. Check Render → kiyoshi → Logs (is it suspended or crashing?).` : null
  found.bot_error = hb?.error && hb.error_streak >= 3 ? `The bot has failed ${hb.error_streak} cycles in a row: ${hb.error}` : null
  if (vc?.vault_address) {
    const since = new Date(Date.now() - 24 * 3_600_000).toISOString()
    const { data: rows } = await db.from('vault_history').select('price, at').eq('vault_address', vc.vault_address).gte('at', since).order('at')
    if (rows && rows.length > 1) {
      const high = Math.max(...rows.map((r: any) => Number(r.price))), last = Number(rows[rows.length - 1].price)
      found.price_drop = last < high * (1 - PRICE_DROP) ? `The share price fell ${(((high - last) / high) * 100).toFixed(1)}% in 24 hours ($${high.toFixed(4)} → $${last.toFixed(4)}). Check the bot wallet; pause the fund if it looks wrong.` : null
    }
    const res = String(vc.last_result ?? '')
    found.keeper_stale = vc.last_run_at && Date.now() - Date.parse(vc.last_run_at) > 90 * 60_000 ? `The keeper has not run for ${Math.round((Date.now() - Date.parse(vc.last_run_at)) / 60_000)} minutes.` : null
    found.settle_waiting = /settlement waiting/.test(res) ? `Settlement is waiting: ${res.match(/settlement waiting[^·]*/)?.[0]?.trim()}. Let trades close, or add USDT to the bot wallet.` : null
    found.keeper_error = /vault: |daily limit/.test(res) && !/vault: paused/.test(res) ? `Keeper problem: ${res}` : null
    found.settle_missed = now.getUTCHours() >= SETTLE_HOUR_UTC + 1 && vc.last_settle_day !== today && !found.settle_waiting ? `Today's settlement has not happened yet (due 20:07 UTC). Keeper: ${res || 'no result'}` : null
    try {
      const v = vc.vault_address as `0x${string}`
      found.paused = (await pub.readContract({ address: v, abi: vaultAbi, functionName: 'paused' })) ? 'The fund is paused: deposits, redemptions and settlements are stopped.' : null
      if (vc.keeper_address && isAddress(vc.keeper_address)) {
        const bal = Number(formatEther(await pub.getBalance({ address: vc.keeper_address as `0x${string}` })))
        found.keeper_gas = bal < MIN_KEEPER_CELO ? `The keeper wallet has ${bal.toFixed(3)} CELO left. Send it about 0.5 CELO: ${vc.keeper_address}` : null
      }
    } catch { /* chain unreachable this time; keep the previous state */ }
  }

  const { data: states } = await db.from('alert_state').select('*')
  const prev = new Map((states ?? []).map((s: any) => [s.key, s]))
  const sent: string[] = []
  for (const [key, detail] of Object.entries(found)) {
    const s: any = prev.get(key)
    if (detail) {
      const due = !s?.active || !s.last_sent_at || Date.now() - Date.parse(s.last_sent_at) > REMIND_MS
      if (due) { await send(`⚠️ ${s?.active ? 'Still: ' : ''}${detail}`); sent.push(key) }
      await db.from('alert_state').upsert({ key, active: true, detail, since: s?.active ? s.since : now.toISOString(), last_sent_at: due ? now.toISOString() : s.last_sent_at })
    } else if (s?.active) {
      await send(`✅ OK again: ${s.detail.split('.')[0]}.`)
      await db.from('alert_state').update({ active: false, detail: null }).eq('key', key)
      sent.push(key + ' ok')
    }
  }

  // Daily summary, once the day's settlement is done
  if (vc?.last_settle_day === today && ac?.summary_day !== today) {
    const { data: h } = await db.from('vault_history').select('nav, price').eq('vault_address', vc.vault_address).order('at', { ascending: false }).limit(1).maybeSingle()
    const settled = String(vc.last_result ?? '').match(/settled [^·]*/)?.[0]?.trim()
    await send(`📊 Daily settlement done: ${settled ?? 'nothing was waiting today'}.${h ? `\nFund $${Number(h.nav).toFixed(2)} · share price $${Number(h.price).toFixed(4)}` : ''}\n${SITE}`)
    await db.from('alert_config').update({ summary_day: today }).eq('id', 1)
    sent.push('summary')
  }
  return reply({ checked: Object.keys(found).length, active: Object.entries(found).filter(([, v]) => v).map(([k]) => k), sent })
})
