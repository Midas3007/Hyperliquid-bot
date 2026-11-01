// Watches for new arbitrage corridors for the Kiyoshi bot, run by the database once a day (pg_cron).
// The bot needs the same currency on two venues: Textile's RFQ market and a DEX pool on Celo. Celo keeps adding
// local stablecoins (wCOP, wPEN, wMXN, wCLP, IDRX, ...), and Textile lists currencies before pools exist. This checks
// every currency Textile prices against the Celo pools DexScreener knows, and sends one Telegram message (through the
// fund-alerts chat) the first time a currency has both a live Textile price and a Celo pool with enough liquidity.
// Read-only: it never trades. Adding a corridor to the bot is a code change, made by hand.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'

const MIN_LIQUIDITY_USD = 5_000
// Already traded by the bot, or plain dollars/ETH/CELO
const KNOWN = new Set(['WBRL', 'WARS', 'CNGN', 'NGN', 'USDC', 'USDT', 'USD₮', 'USDM', 'CUSD', 'USAT', 'WETH', 'CELO', 'GD', 'TESOURO'])
// Textile ticker code -> token symbols the same currency goes by on Celo
const ALIAS: Record<string, string[]> = {
  COP: ['wCOP', 'COPm', 'COPM'], MXN: ['wMXN', 'MXNB', 'MXNe'], PEN: ['wPEN'], CLP: ['wCLP'], IDR: ['IDRX'],
  BRL: ['BRLA', 'BRLm'], BRLA: ['BRLA'], ZARU: ['ZARU', 'ZARP'], NTZS: ['NTZS'], KES: ['KESm'], GHS: ['GHSm'], PHP: ['PHPm'],
}

Deno.serve(async () => {
  const reply = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
  const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const tickers: any[] = await (await fetch('https://api.textilecredit.com/tickers')).json()
  const found: { code: string; textile: string; pools: string[] }[] = []
  for (const t of tickers) {
    const usdBase = /^(USDT|USDC|USD₮)$/i.test(t.base_currency)
    const code = String(usdBase ? t.target_currency : t.base_currency).toUpperCase()
    if (KNOWN.has(code)) continue
    if (!(Number(t.bid) > 0 || Number(t.ask) > 0)) continue // no live Textile price
    const pools: string[] = []
    for (const sym of ALIAS[code] ?? [code]) {
      const r: any = await (await fetch(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(sym)}`)).json().catch(() => ({}))
      for (const p of r.pairs ?? []) {
        if (p.chainId !== 'celo' || (p.liquidity?.usd ?? 0) < MIN_LIQUIDITY_USD) continue
        const syms = [p.baseToken?.symbol, p.quoteToken?.symbol].map((s: string) => String(s).toLowerCase())
        if (!syms.includes(sym.toLowerCase())) continue
        pools.push(`${p.dexId} ${p.baseToken.symbol}/${p.quoteToken.symbol} ($${Math.round(p.liquidity.usd).toLocaleString('en-US')} liquidity) ${p.pairAddress}`)
      }
    }
    if (pools.length) found.push({ code, textile: `${t.ticker_id} bid ${t.bid} / ask ${t.ask}, 24h volume ${t.base_volume}`, pools: [...new Set(pools)] })
  }

  // Tell the fund's Telegram chat once per currency
  const token = Deno.env.get('TELEGRAM_BOT_TOKEN')
  const { data: ac } = await db.from('alert_config').select('chat_id').eq('id', 1).single()
  const sent: string[] = []
  for (const f of found) {
    const { data: seen } = await db.from('corridor_seen').select('code').eq('code', f.code).maybeSingle()
    if (seen) continue
    if (token && ac?.chat_id) {
      await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: ac.chat_id, disable_web_page_preview: true, text: `🆕 Possible new corridor for Kiyoshi: ${f.code}\nTextile: ${f.textile}\nCelo pools:\n${f.pools.map((p) => '• ' + p).join('\n')}\n\nAsk Claude to check it and add it to the bot.` }),
      })
    }
    await db.from('corridor_seen').insert({ code: f.code, detail: f })
    sent.push(f.code)
  }
  return reply({ checked: tickers.length, candidates: found.map((f) => f.code), sent })
})
