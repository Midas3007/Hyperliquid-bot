// Telegram alerts and commands for the fund. Three kinds of request reach it:
//   - The database's schedule (pg_cron, every 10 minutes): runs the checks below, and makes sure Telegram
//     delivers chat messages here (webhook) with the command menu set.
//   - Telegram, the moment someone messages the bot: carries a secret header derived from TELEGRAM_BOT_TOKEN
//     (an Edge Function secret from @BotFather). The first private chat that sends /start gets the alerts;
//     only that chat gets answers. Commands are read-only, except /skipwait_on|off and /smart_on|off, bot
//     settings that cannot move money: nothing here can move money. /dashboard sends every command as buttons.
//   - The Kiyoshi bot's heartbeat {heartbeat: {message, signature}}, signed by the bot wallet (the vault's
//     treasury), so nobody can fake it.
// Checks: bot silent or erroring, share price down sharply, settlement missed or waiting, keeper errors or low on
// CELO, fund paused; and a short daily summary after the settlement. Each alert is sent once when it starts,
// reminded every 6 hours, and followed by an "OK again" message.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'
import { createPublicClient, defineChain, erc20Abi, formatEther, formatUnits, http, isAddress, parseAbi } from 'npm:viem@2.21.0'
import { celo } from 'npm:viem@2.21.0/chains'

const SETTLE_HOUR_UTC = 20
const REMIND_MS = 6 * 3_600_000
const SILENT_MS = 20 * 60_000
const PRICE_DROP = 0.05 // share price 5% below its 24-hour high
const MIN_KEEPER_CELO = 0.1
const KEEPER_CELO_PER_DAY = 6.8 / 30 // the keeper burns about 6.8 CELO a month (Midas, 13 Nov)
const KEEPER_WARN_DAYS = 10 // remind when it has fewer than this many days left
const MIN_BOT_CELO = 1 // below 0.5 the bot buys CELO with $0.50 of USDT by itself
const MIN_CHECK_GAP_MS = 2 * 60_000
const LAGOS_MS = 3_600_000 // UTC+1, for "today"
const celoSepolia = defineChain({
  id: 11142220, name: 'Celo Sepolia', nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 },
  rpcUrls: { default: { http: ['https://forno.celo-sepolia.celo-testnet.org'] } },
})
const NETS: Record<string, any> = {
  mainnet: { chain: celo, rpc: 'https://forno.celo.org', explorer: 'https://celoscan.io' },
  testnet: { chain: celoSepolia, rpc: 'https://forno.celo-sepolia.celo-testnet.org', explorer: 'https://celo-sepolia.blockscout.com' },
}
const vaultAbi = parseAbi([
  'function paused() view returns (bool)', 'function treasury() view returns (address)', 'function operator() view returns (address)',
  'function queueLengths() view returns (uint256, uint256)', 'function totalPendingDeposits() view returns (uint256)',
  'function balanceOf(address) view returns (uint256)', 'function sharePrice() view returns (uint256)', 'function nav() view returns (uint256)',
])
const SITE = 'https://midas-kiyoshi-fund.vercel.app'
const COMMANDS = [
  { command: 'dashboard', description: 'Every command as buttons' },
  { command: 'status', description: 'Bot, fund value and keeper at a glance' },
  { command: 'today', description: 'Trades and trading profit today' },
  { command: 'queue', description: 'Deposits and withdrawals waiting to settle' },
  { command: 'keeper', description: 'Keeper gas, last run and last settlement' },
  { command: 'skipwait', description: 'Skipping trades Textile makes wait for: on/off' },
  { command: 'smart', description: 'Smart sizing by time of day: on/off' },
  { command: 'schedule', description: 'The smart sizing schedule for the week' },
  { command: 'tight', description: 'Tight first-leg tolerance: on/off' },
  { command: 'help', description: 'What this bot does' },
]
const HELP = `Midas × Kiyoshi Fund alerts. I message you when something needs attention: the bot stops or keeps failing, the share price drops sharply, settlement is missed or waiting, the keeper has a problem or runs low on gas, or the fund is paused. After each daily settlement you get a summary.

Commands (nothing here moves money):
/dashboard: every command as buttons
/status: bot, fund value and keeper
/today: trades and profit today (Lagos time)
/queue: deposits and withdrawals waiting to settle
/keeper: keeper gas, last run, last settlement (the bot's own gas is in /status)
/skipwait: is the bot skipping trades Textile makes wait for? /skipwait_off and /skipwait_on switch it
/smart: is smart sizing on? It sets the max trade and minimum edge by time of day. /smart_on and /smart_off switch it
/schedule: the smart sizing schedule for the week
/tight: is the tight first-leg tolerance on? /tight_on and /tight_off switch it
/help: this message

Pause, settle and approvals stay on the site (signed with your wallet) and on the phone.`

const usd = (v: bigint) => '$' + Number(formatUnits(v, 6)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const ago = (t?: string | null) => {
  if (!t) return 'never'
  const m = Math.round((Date.now() - Date.parse(t)) / 60_000)
  return m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`
}
async function secretFor(token: string) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('fund-alerts:' + token)))
  return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('').slice(0, 48)
}

Deno.serve(async (req) => {
  const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type', 'Access-Control-Allow-Methods': 'POST, OPTIONS' }
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })
  const reply = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json', ...cors } })
  const SUPA = Deno.env.get('SUPABASE_URL')!
  const db = createClient(SUPA, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const { data: vc } = await db.from('vault_config').select('*').eq('id', 1).single()
  const net = NETS[vc?.network] ?? NETS.mainnet
  const pub = createPublicClient({ chain: net.chain, transport: http(net.rpc) })
  const vault = (vc?.vault_address && isAddress(vc.vault_address) ? vc.vault_address : null) as `0x${string}` | null
  const readV = (fn: string, args: unknown[] = []) => pub.readContract({ address: vault!, abi: vaultAbi, functionName: fn as any, args: args as any }) as Promise<any>
  const body = req.method === 'POST' ? await req.json().catch(() => null) : null

  // ---------- heartbeat from the bot ----------
  if (body?.heartbeat) {
    try {
      const { message, signature } = body.heartbeat
      const hb = JSON.parse(String(message))
      if (hb.kind !== 'kiyoshi-heartbeat' || !(Math.abs(Date.now() - Date.parse(hb.at)) < 10 * 60_000)) throw new Error('stale or malformed')
      if (!vault) throw new Error('no vault configured')
      const treasury = await readV('treasury')
      if (!(await pub.verifyMessage({ address: treasury, message: String(message), signature }).catch(() => false))) throw new Error('not signed by the bot wallet')
      const { data: last } = await db.from('bot_heartbeat').select('at').eq('id', 1).single()
      if (last?.at && Date.parse(hb.at) <= Date.parse(last.at)) throw new Error('replayed')
      await db.from('bot_heartbeat').update({ at: hb.at, equity: hb.equity ?? null, error: hb.error ?? null, error_streak: hb.errorStreak ?? 0, pause: hb.pause ?? null, holding: !!hb.holding,
        celo: hb.celo ?? null, last_topup: hb.lastTopUp ?? null, gas_error: hb.gasError ?? null, skip_summary: hb.skipSummary ?? null,
        smart_summary: hb.smart ?? null, smart_schedule: hb.schedule ?? null, received_at: new Date().toISOString() }).eq('id', 1)
      return reply({ ok: true })
    } catch (e) { return reply({ error: `heartbeat: ${(e as Error).message.split('\n')[0]}` }) }
  }

  // ---------- Telegram ----------
  const token = Deno.env.get('TELEGRAM_BOT_TOKEN')
  if (!token) return reply({ skipped: 'TELEGRAM_BOT_TOKEN is not set' })
  // A network error from fetch carries the request URL in its text, and that URL contains the bot token. An
  // uncaught error is written to the function log, so the token ended up there (seen 9 Nov). Fail with a message
  // that has the token removed: the run still stops and is retried, but nothing secret is logged.
  const tg = async (method: string, params: Record<string, unknown>) => {
    try {
      const r = await fetch(`https://api.telegram.org/bot${token}/${method}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) })
      return await r.json()
    } catch (e) {
      const why = String((e as Error)?.message ?? e).split(token).join('<token>').replace(/bot\d+:[\w-]+/g, 'bot<token>').split('\n')[0].slice(0, 160)
      throw new Error(`Telegram did not answer (${method}): ${why}`)
    }
  }
  const secret = await secretFor(token)
  const { data: ac } = await db.from('alert_config').select('*').eq('id', 1).single()
  let chat = ac?.chat_id as number | null

  // A chat message, delivered by Telegram the moment it is sent
  if (req.headers.get('x-telegram-bot-api-secret-token') !== null) {
    if (req.headers.get('x-telegram-bot-api-secret-token') !== secret) return new Response('forbidden', { status: 403 })
    // A button on the dashboard: run its command for the linked chat, then refresh the buttons (their labels show state)
    const cq = body?.callback_query
    if (cq) {
      await tg('answerCallbackQuery', { callback_query_id: cq.id })
      if (!chat || cq.message?.chat?.id !== chat) return reply({ ok: true })
      const c = String(cq.data ?? '')
      if (!/^\/[a-z_]+$/.test(c)) return reply({ ok: true })
      let text = ''
      try { text = await run(c) } catch (e) { text = `Could not read that right now: ${(e as Error).message.split('\n')[0]}` }
      if (c !== '/dashboard') await tg('sendMessage', { chat_id: chat, text, disable_web_page_preview: true })
      await tg('editMessageReplyMarkup', { chat_id: chat, message_id: cq.message.message_id, reply_markup: await dashboardKeys() })
      return reply({ ok: true })
    }
    const m = body?.message
    if (!m || m.chat?.type !== 'private') return reply({ ok: true })
    const cmd = String(m.text ?? '').trim().split(/[\s@]/)[0].toLowerCase()
    if (!chat) {
      if (cmd === '/start') {
        chat = m.chat.id
        await db.from('alert_config').update({ chat_id: chat, chat_name: m.from?.first_name ?? null, linked_at: new Date().toISOString() }).eq('id', 1)
        await tg('sendMessage', { chat_id: chat, text: `Alerts connected to this chat.\n\n${HELP}` })
      }
      return reply({ ok: true })
    }
    if (m.chat.id !== chat) return reply({ ok: true }) // only the linked chat gets answers
    let text = ''
    try { text = await run(cmd) } catch (e) { text = `Could not read that right now: ${(e as Error).message.split('\n')[0]}` }
    const dash = cmd === '/dashboard' || cmd === '/menu'
    await tg('sendMessage', { chat_id: chat, text, disable_web_page_preview: true, ...(dash ? { reply_markup: await dashboardKeys() } : {}) })
    return reply({ ok: true })
  }

  async function run(cmd: string) {
    return cmd === '/status' ? await statusText() : cmd === '/today' ? await todayText() : cmd === '/queue' ? await queueText()
      : cmd === '/keeper' ? await keeperText() : cmd === '/skipwait' ? await skipText()
      : cmd === '/skipwait_on' || cmd === '/skipwait_off' ? await setSkip(cmd === '/skipwait_on')
      : cmd === '/smart' ? await smartText() : cmd === '/schedule' ? await scheduleText()
      : cmd === '/smart_on' || cmd === '/smart_off' ? await setSmart(cmd === '/smart_on')
      : cmd === '/tight' ? await tightText() : cmd === '/tight_on' || cmd === '/tight_off' ? await setTight(cmd === '/tight_on')
      : cmd === '/dashboard' || cmd === '/menu' ? 'Command dashboard. Tap a button; the switches show their current state. Nothing here moves money.'
      : cmd === '/start' ? 'Alerts are already connected to this chat.' : HELP
  }

  // The dashboard's buttons. Switch labels show the current state; tapping one flips it.
  async function dashboardKeys() {
    const { data: st } = await db.from('bot_settings').select('*').eq('id', 1).single()
    const skip = st?.skip_reserved !== false, smart = st?.smart_sizing === true, tight = st?.tight_slip !== false
    const b = (text: string, data: string) => ({ text, callback_data: data })
    return { inline_keyboard: [
      [b('Status', '/status'), b('Today', '/today')],
      [b('Queue', '/queue'), b('Keeper', '/keeper')],
      [b(`Smart sizing: ${smart ? 'ON' : 'OFF'} (tap to turn ${smart ? 'off' : 'on'})`, smart ? '/smart_off' : '/smart_on')],
      [b('Smart sizing now', '/smart'), b('Weekly schedule', '/schedule')],
      [b(`Skip waits: ${skip ? 'ON' : 'OFF'} (tap to turn ${skip ? 'off' : 'on'})`, skip ? '/skipwait_off' : '/skipwait_on')],
      [b(`Tight first leg: ${tight ? 'ON' : 'OFF'} (tap to turn ${tight ? 'off' : 'on'})`, tight ? '/tight_off' : '/tight_on')],
      [{ text: 'Open the site', url: SITE }, b('Help', '/help')],
    ] }
  }

  async function smartText() {
    const [{ data: st }, { data: hb }] = await Promise.all([db.from('bot_settings').select('*').eq('id', 1).single(), db.from('bot_heartbeat').select('smart_summary, at').eq('id', 1).single()])
    const on = st?.smart_sizing === true
    return [
      `Smart sizing: ${on ? 'ON' : 'OFF'} (changed ${ago(st?.smart_updated_at)})`,
      on ? 'The max trade and minimum edge follow the time of day: bigger trades 13:00–14:59 Lagos, a higher minimum edge in quiet hours. /schedule shows the week.'
        : 'The bot uses its fixed max trade and minimum edge all day.',
      hb?.smart_summary ? `Bot now (reported ${ago(hb.at)}): ${hb.smart_summary}` : '',
      on ? '/smart_off to go back to fixed sizing' : '/smart_on to turn it on',
    ].filter(Boolean).join('\n')
  }
  async function setSmart(on: boolean) {
    await db.from('bot_settings').update({ smart_sizing: on, smart_updated_at: new Date().toISOString() }).eq('id', 1)
    return `Done: smart sizing is now ${on ? 'ON' : 'OFF'}. The bot picks this up within 2 minutes.`
  }
  async function scheduleText() {
    const [{ data: st }, { data: hb }] = await Promise.all([db.from('bot_settings').select('smart_sizing').eq('id', 1).single(), db.from('bot_heartbeat').select('smart_schedule').eq('id', 1).single()])
    return `Smart sizing schedule, Lagos time (${st?.smart_sizing === true ? 'ON' : 'OFF'}):\n${hb?.smart_schedule ?? 'The bot has not sent its schedule yet; it does within 5 minutes of its next start.'}\nA trade never uses more than 90% of the bot's USDT, so a bigger peak size only applies when the cash is there.`
  }

  async function tightText() {
    const { data: st } = await db.from('bot_settings').select('*').eq('id', 1).single()
    const on = st?.tight_slip !== false
    return `Tight first-leg tolerance: ${on ? 'ON' : 'OFF'} (changed ${ago(st?.tight_updated_at)})\n${on ? "The first swap may slip only as far as still leaves the minimum edge; a trade whose first leg would eat the profit is not started." : 'The first swap may fill up to 0.3% worse than scanned (the old rule).'}\n${on ? '/tight_off to go back' : '/tight_on to turn it on'}`
  }
  async function setTight(on: boolean) {
    await db.from('bot_settings').update({ tight_slip: on, tight_updated_at: new Date().toISOString() }).eq('id', 1)
    return `Done: tight first-leg tolerance is now ${on ? 'ON' : 'OFF'}. The bot picks this up within 2 minutes.`
  }

  async function skipText() {
    const [{ data: st }, { data: hb }] = await Promise.all([db.from('bot_settings').select('*').eq('id', 1).single(), db.from('bot_heartbeat').select('skip_summary').eq('id', 1).single()])
    const on = st?.skip_reserved !== false
    return `Skipping trades Textile makes wait for: ${on ? 'ON' : 'OFF'} (changed ${ago(st?.updated_at)})\n${on ? 'When Textile still reserves funds from the last trade, the bot skips the trade and rescans with fresh prices.' : 'The bot waits up to two minutes for Textile, then trades at the new prices.'}${hb?.skip_summary ? `\nBot count ${hb.skip_summary}` : ''}\n${on ? '/skipwait_off to go back to waiting' : '/skipwait_on to skip again'}`
  }
  async function setSkip(on: boolean) {
    await db.from('bot_settings').update({ skip_reserved: on, updated_at: new Date().toISOString() }).eq('id', 1)
    return `Done: skipping is now ${on ? 'ON' : 'OFF'}. The bot picks this up within 2 minutes.`
  }

  async function statusText() {
    const { data: hb } = await db.from('bot_heartbeat').select('*').eq('id', 1).single()
    let fund = 'Fund: no vault configured'
    if (vault) {
      const [nav, price, paused] = await Promise.all([readV('nav'), readV('sharePrice'), readV('paused')])
      fund = `Fund: ${usd(nav)} · share price $${Number(formatUnits(price, 6)).toFixed(4)}${paused ? ' · PAUSED' : ''}`
    }
    return [
      `Bot: reported ${ago(hb?.at)}${hb?.equity != null ? ` · wallet ~$${Number(hb.equity).toFixed(2)}` : ''}${hb?.celo != null ? ` · gas ${Number(hb.celo).toFixed(2)} CELO` : ''}${hb?.pause ? `\n  ${hb.pause}` : ''}${hb?.holding ? '\n  holding leftover inventory' : ''}${hb?.error ? `\n  last error: ${hb.error}` : ''}`,
      fund,
      `Keeper: ran ${ago(vc?.last_run_at)} · ${vc?.last_result ?? ''}`,
      SITE,
    ].join('\n')
  }

  async function todayText() {
    const r = await fetch(`${SUPA}/functions/v1/trade-stats`, { method: 'POST', headers: { Authorization: `Bearer ${Deno.env.get('SUPABASE_ANON_KEY')}`, 'Content-Type': 'application/json' }, body: '{}' })
    const j = await r.json()
    if (j.error) throw new Error(j.error)
    const start = Math.floor((Date.now() + LAGOS_MS) / 86_400_000) * 86_400_000 - LAGOS_MS
    const t = (j.trades ?? []).filter((x: any) => x.t >= start)
    const profit = t.reduce((a: number, x: any) => a + x.profit, 0)
    const vol = t.reduce((a: number, x: any) => a + x.size, 0)
    const wins = t.filter((x: any) => x.profit > 0).length
    // trade-stats answers from the stored history (flagged `stale`) when the chain node cannot be reached, so
    // /today shows the most recent numbers instead of an error; say how old they are.
    const stale = j.stale ? `\n(as of ${Math.max(1, Math.round((j.staleAgeMs ?? 0) / 60_000))} min ago; the chain node is busy)` : ''
    if (!t.length) return `Today (Lagos time): no trades yet.${stale}`
    const lastT = new Date(t[t.length - 1].t + LAGOS_MS).toISOString().slice(11, 16)
    return `Today (Lagos time): ${t.length} trade${t.length === 1 ? '' : 's'}, ${wins} profitable (${Math.round((wins / t.length) * 100)}%)\nProfit ${profit >= 0 ? '+' : ''}$${profit.toFixed(2)} before gas · traded $${vol.toFixed(2)}\nLast trade ${lastT}${stale}`
  }

  async function queueText() {
    if (!vault) return 'No vault configured.'
    const [q, pending, escrow, price, treasury, paused] = await Promise.all([readV('queueLengths'), readV('totalPendingDeposits'), readV('balanceOf', [vault]), readV('sharePrice'), readV('treasury'), readV('paused')])
    const payouts = (escrow * price) / 10n ** 18n
    const cash = await pub.readContract({ address: vc.usdt_address as `0x${string}`, abi: erc20Abi, functionName: 'balanceOf', args: [treasury] })
    const now = new Date(), today = now.toISOString().slice(0, 10)
    const next = now.getUTCHours() < SETTLE_HOUR_UTC || vc?.last_settle_day !== today ? 'today 20:07 UTC (21:07 Lagos)' : 'tomorrow 20:07 UTC (21:07 Lagos)'
    if (!q[0] && !q[1]) return `Nothing waiting to settle. Next settlement ${next}.${paused ? '\nThe fund is PAUSED.' : ''}`
    return [
      `Waiting to settle: ${q[0]} deposit${q[0] === 1n ? '' : 's'} (${usd(pending)}), ${q[1]} withdrawal${q[1] === 1n ? '' : 's'} (about ${usd(payouts)} before fees)`,
      `Next settlement ${next}${paused ? ' · the fund is PAUSED, nothing settles until it is unpaused' : ''}`,
      q[1] ? `Bot wallet USDT ${usd(cash)}${cash + pending < payouts ? ' · not enough for the withdrawals yet' : ' · enough for the withdrawals'}` : '',
    ].filter(Boolean).join('\n')
  }

  async function keeperText() {
    const k = vc?.keeper_address
    const bal = k && isAddress(k) ? Number(formatEther(await pub.getBalance({ address: k as `0x${string}` }))) : null
    const op = vault ? await readV('operator') : null
    return [
      `Keeper ${k ? `${k.slice(0, 6)}…${k.slice(-4)}` : 'not created'}${bal != null ? ` · ${bal.toFixed(3)} CELO${` · about ${(bal / KEEPER_CELO_PER_DAY).toFixed(0)} days left`}${bal / KEEPER_CELO_PER_DAY < KEEPER_WARN_DAYS ? ' (top it up: send ~7 CELO)' : ''}` : ''}`,
      op && k ? (op.toLowerCase() === k.toLowerCase() ? 'Is the vault operator: yes' : `Is the vault operator: NO (operator is ${op})`) : '',
      `Last run ${ago(vc?.last_run_at)}: ${vc?.last_result ?? ''}`,
      `Last daily settlement: ${vc?.last_settle_day ?? 'none yet'} · runs hourly at :07, settles at 20:07 UTC`,
    ].filter(Boolean).join('\n')
  }

  // ---------- scheduled run ----------
  if (ac?.last_check_at && Date.now() - Date.parse(ac.last_check_at) < MIN_CHECK_GAP_MS) return reply({ skipped: 'checked recently' })
  await db.from('alert_config').update({ last_check_at: new Date().toISOString() }).eq('id', 1)
  // Make sure Telegram delivers messages here, instantly, with the secret header; and set the "/" menu
  const hook = `${SUPA}/functions/v1/fund-alerts`
  const info = await tg('getWebhookInfo', {})
  if (info.ok && (info.result?.url !== hook || !(info.result?.allowed_updates ?? []).includes('callback_query')))
    await tg('setWebhook', { url: hook, secret_token: secret, allowed_updates: ['message', 'callback_query'] })
  await tg('setMyCommands', { commands: COMMANDS }) // keeps the "/" menu in step with COMMANDS
  if (!chat) return reply({ skipped: 'no chat linked yet: send /start to the Telegram bot' })
  const send = (text: string) => tg('sendMessage', { chat_id: chat, text, disable_web_page_preview: true })

  const found: Record<string, string | null> = {}
  const now = new Date(), today = now.toISOString().slice(0, 10)
  const { data: hb } = await db.from('bot_heartbeat').select('*').eq('id', 1).single()
  found.bot_silent = hb?.at && Date.now() - Date.parse(hb.at) > SILENT_MS
    ? `The bot has not reported for ${Math.round((Date.now() - Date.parse(hb.at)) / 60_000)} minutes. Check Render → kiyoshi → Logs (is it suspended or crashing?).` : null
  found.bot_gas = hb?.celo != null && Number(hb.celo) < MIN_BOT_CELO
    ? `The bot wallet is down to ${Number(hb.celo).toFixed(3)} CELO for gas. Below 0.5 it buys CELO with $0.50 of USDT by itself${hb.gas_error ? `, but the last try failed: ${hb.gas_error}. Send it a few CELO` : ''}.` : null
  found.bot_error = hb?.error && hb.error_streak >= 3 ? `The bot has failed ${hb.error_streak} cycles in a row: ${hb.error}` : null
  if (vault) {
    const since = new Date(Date.now() - 24 * 3_600_000).toISOString()
    const { data: rows } = await db.from('vault_history').select('price, at').eq('vault_address', vc.vault_address).gte('at', since).order('at')
    if (rows && rows.length > 1) {
      const high = Math.max(...rows.map((r: any) => Number(r.price))), last = Number(rows[rows.length - 1].price)
      found.price_drop = last < high * (1 - PRICE_DROP) ? `The share price fell ${(((high - last) / high) * 100).toFixed(1)}% in 24 hours ($${high.toFixed(4)} → $${last.toFixed(4)}). Check the bot wallet; pause the fund if it looks wrong.` : null
    }
    const res = String(vc.last_result ?? '')
    found.keeper_stale = vc.last_run_at && Date.now() - Date.parse(vc.last_run_at) > 90 * 60_000 ? `The keeper has not run for ${Math.round((Date.now() - Date.parse(vc.last_run_at)) / 60_000)} minutes.` : null
    found.settle_waiting = /settlement waiting/.test(res) ? `Settlement is waiting: ${res.match(/settlement waiting[^·]*/)?.[0]?.trim()}. Let trades close, or add USDT to the bot wallet.` : null
    // one-off network errors are skipped: a real outage shows up as keeper_stale or settle_missed
    found.keeper_error = /vault: |daily limit/.test(res) && !/vault: paused|HTTP request failed|timed out|fetch failed/i.test(res) ? `Keeper problem: ${res}` : null
    found.settle_missed = now.getUTCHours() >= SETTLE_HOUR_UTC + 1 && vc.last_settle_day !== today && !found.settle_waiting ? `Today's settlement has not happened yet (due 20:07 UTC). Keeper: ${res || 'no result'}` : null
    try {
      found.paused = (await readV('paused')) ? 'The fund is paused: deposits, redemptions and settlements are stopped.' : null
      if (vc.keeper_address && isAddress(vc.keeper_address)) {
        const bal = Number(formatEther(await pub.getBalance({ address: vc.keeper_address as `0x${string}` })))
        const days = bal / KEEPER_CELO_PER_DAY
        found.keeper_gas = days < KEEPER_WARN_DAYS ? `${days < 3 ? 'Urgent: ' : ''}the keeper wallet has ${bal.toFixed(2)} CELO, about ${Math.max(0, days).toFixed(1)} days at 6.8 CELO a month. Send it about 7 CELO (a month): ${vc.keeper_address}` : null
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
      await send(`✅ OK again: ${String(s.detail).split(/\.\s/)[0].replace(/\.$/, '')}.`)
      await db.from('alert_state').update({ active: false, detail: null }).eq('key', key)
      sent.push(key + ' ok')
    }
  }

  // A new gas top-up by the bot
  const t = hb?.last_topup as any
  if (t?.tx && t.tx !== ac?.last_topup_tx) {
    await send(`⛽ The bot bought ${Number(t.celo).toFixed(2)} CELO for gas with $${Number(t.usdt).toFixed(2)} of USDT. The cost comes out of the fund's value, shared by all shareholders.\n${net.explorer}/tx/${t.tx}`)
    await db.from('alert_config').update({ last_topup_tx: t.tx }).eq('id', 1)
    sent.push('topup')
  }

  // A treasury withdrawal the bot has paid (confirmed on the chain)
  const { data: po } = await db.from('treasury_entries').select('id, amount, peer, tx').eq('kind', 'render').eq('status', 'done').order('id', { ascending: false }).limit(1).maybeSingle()
  if (po && po.id !== ac?.last_payout_id) {
    await send(`💸 Treasury withdrawal paid: $${Number(po.amount).toFixed(2)} to ${po.peer}. It came out of the running-costs pot, not the share price.${po.tx ? `\n${net.explorer}/tx/${po.tx}` : ''}`)
    await db.from('alert_config').update({ last_payout_id: po.id }).eq('id', 1)
    sent.push('payout')
  }

  // Daily summary, once the day's settlement is done
  if (vault && vc?.last_settle_day === today && ac?.summary_day !== today) {
    const [nav, price] = await Promise.all([readV('nav'), readV('sharePrice')]).catch(() => [null, null])
    const settled = String(vc.last_result ?? '').match(/settled [^·]*/)?.[0]?.trim()
    await send(`📊 Daily settlement done: ${settled ?? 'nothing was waiting today'}.${nav != null ? `\nFund ${usd(nav)} · share price $${Number(formatUnits(price, 6)).toFixed(4)}` : ''}\n${SITE}`)
    await db.from('alert_config').update({ summary_day: today }).eq('id', 1)
    sent.push('summary')
  }
  return reply({ checked: Object.keys(found).length, active: Object.entries(found).filter(([, v]) => v).map(([k]) => k), sent })
})
