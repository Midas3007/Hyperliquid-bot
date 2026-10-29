// Midas × Kiyoshi Fund — member and admin dashboards.
// All numbers come from database functions (fund_summary, my_position, admin_overview), so what a member
// can see is enforced by the database, not by this page.
const C = window.FUND_CONFIG
const sb = window.supabase.createClient(C.supabaseUrl, C.supabaseAnonKey)
const $app = document.getElementById('app')
const savedRange = (() => { try { return localStorage.getItem('range') } catch { return null } })()
const state = { range: ['hour', 'day', 'week'].includes(savedRange) ? savedRange : 'day', session: null, me: null, fund: null, admin: null, wallet: null, preview: null, flash: null, lb: { caption: 0, names: 0 }, trades: null }

// ---------- formatting ----------
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const n = (v) => Number(v ?? 0)
const money = (v, sign = false) => {
  const x = n(v)
  const s = Math.abs(x).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (x < 0 ? '−$' : sign && x > 0 ? '+$' : '$') + s
}
const pct = (v, sign = false) => {
  const x = n(v)
  return (x < 0 ? '−' : sign && x > 0 ? '+' : '') + Math.abs(x).toFixed(2) + '%'
}
const tone = (v) => (n(v) > 0.000001 ? 'pos' : n(v) < -0.000001 ? 'neg' : '')
const units = (v) => n(v).toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })
const price = (v) => n(v).toFixed(6)
const day = (d) => (d ? new Date(String(d).length === 10 ? d + 'T12:00:00Z' : d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—')
const stamp = (d) => (d ? new Date(d).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—')
const today = () => new Date().toISOString().slice(0, 10)
const short = (a) => (a ? a.slice(0, 6) + '…' + a.slice(-4) : '')
const errText = (e) => (e?.message || String(e)).replace(/^.*?ERROR:\s*/, '')

// ---------- the fund mark (from the promo video) ----------
// MIDAS × KIY◠SHI in JetBrains Mono: the M is a 巾-style kanji built from four tapered fan blades, the O is
// Kiyoshi's fan in gold. Both are drawn as SVG so they stay sharp at any size.
function taper(ax, ay, bx, by, wa, wb) { // a long truncated triangle: narrow end at a, wide end at b
  const len = Math.hypot(bx - ax, by - ay) || 1, nx = -(by - ay) / len, ny = (bx - ax) / len
  return [[ax + nx * wa / 2, ay + ny * wa / 2], [bx + nx * wb / 2, by + ny * wb / 2], [bx - nx * wb / 2, by - ny * wb / 2], [ax - nx * wa / 2, ay - ny * wa / 2]]
    .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
}
const M_GLYPH = (() => {
  const w = 69, h = 72, P = (u, v) => [w * u, h * v] // cap height 72 units, baseline at y = 72
  const strokes = [[P(.20, .24), P(.05, 1.02)], [P(.86, .24), P(.14, .24)], [P(.5, 1.06), P(.5, -.08)], [P(.86, 1.0), P(.86, .20)]]
  const polys = strokes.map(([[ax, ay], [bx, by]]) => `<polygon points="${taper(ax, ay, bx, by, 5, 17)}"/>`).join('')
  return `<svg class="wm-m" viewBox="-2 -8 73 88" aria-hidden="true"><g fill="currentColor">${polys}</g></svg>`
})()
function fanSvg(cls) {
  const cx = 50, cy = 50, r = 48, A0 = Math.PI * 1.07, A1 = Math.PI * 1.93, N = 13, w = (A1 - A0) / N, gap = .011, pt = (a, rr) => `${(cx + Math.cos(a) * rr).toFixed(2)},${(cy + Math.sin(a) * rr).toFixed(2)}`
  let blades = ''
  for (let i = 0; i < N; i++) { const s = A0 + i * w + gap, e = A0 + (i + 1) * w - gap; blades += `<polygon points="${pt(s, r * .06)} ${pt(s, r)} ${pt(e, r)} ${pt(e, r * .06)}"/>` }
  const ha = A0 + (A1 - A0) * .82, hx = cx + Math.cos(ha) * r * .76, hy = cy + Math.sin(ha) * r * .76
  let low = ''
  const lr = r * .13, ly = cy + r * .03
  for (let i = 0; i < 6; i++) {
    const s = Math.PI * (.12 + .76 * i / 6) + .04, e = Math.PI * (.12 + .76 * (i + 1) / 6) - .04, q = (a, rr) => `${(cx + Math.cos(a) * rr).toFixed(2)},${(ly + Math.sin(a) * rr).toFixed(2)}`
    low += `<polygon points="${q(s, lr * .35)} ${q(s, lr)} ${q(e, lr)} ${q(e, lr * .35)}"/>`
  }
  const id = 'fh' + Math.random().toString(36).slice(2, 7)
  return `<svg class="${cls}" viewBox="0 0 100 60" aria-hidden="true"><defs><mask id="${id}"><rect width="100" height="60" fill="#fff"/><circle cx="${hx.toFixed(2)}" cy="${hy.toFixed(2)}" r="${(r * .075).toFixed(2)}" fill="#000"/></mask></defs>
    <g fill="currentColor"><g mask="url(#${id})">${blades}</g>${low}<circle cx="${cx}" cy="${ly}" r="${(lr * .3).toFixed(2)}" fill="none" stroke="currentColor" stroke-width="${(lr * .14).toFixed(2)}"/></g></svg>`
}
const brand = () => `<div class="brand wordmark" role="img" aria-label="Midas × Kiyoshi Fund"><span class="wm-gold">${M_GLYPH}IDAS</span><span class="wm-x">×</span><span>KIY${fanSvg('wm-o')}SHI</span></div>`

// ---------- disclaimer ----------
const DISCLAIMER = `
<div class="disclaimer">
  <h3>Risk disclosure &amp; limitation of liability</h3>
  <ol>
    <li><b>You can lose all of your money.</b> The pool trades digital assets with an automated arbitrage bot on the Celo blockchain. Trading, software and blockchain risks can cause partial or total loss of the amount you contribute.</li>
    <li><b>No guarantee of profit.</b> Past or displayed results do not predict future returns. Opportunities can disappear for long periods, and competing traders can take them first.</li>
    <li><b>Technology risk.</b> Losses can result from bugs in the bot, smart-contract failures, stablecoin de-pegs, exchange or RFQ-venue failures, network outages, or the theft or loss of private keys. The bot software is open-source third-party code that has not been independently audited.</li>
    <li><b>Not a regulated product.</b> This pool is a private arrangement. It is not registered with or approved by any securities regulator, is not a bank deposit, and is not insured or protected by any compensation scheme. Nothing on this site is financial, investment, legal or tax advice.</li>
    <li><b>Figures shown.</b> The fund value is read automatically from the pool wallet's on-chain balance, with any local-currency tokens priced at live Uniswap rates. It can move with those rates while trades are open, and the manager can correct it by hand.</li>
    <li><b>Fees.</b> The manager takes a performance fee (currently 10%) of profit above the capital you put in, charged when you withdraw and shown on your dashboard as it accrues. The fee stays in the pool as the manager's own units. No fee is charged on losses. An early-exit penalty may apply if you withdraw within the lock period shown on your dashboard.</li>
    <li><b>Withdrawals</b> are paid at the unit price at the time of withdrawal and may take time to process while funds are in open positions.</li>
    <li><b>Limitation of liability.</b> To the fullest extent permitted by law, the manager and anyone operating this site are not liable for any loss, including loss of capital or profit, arising from your participation, except where caused by their fraud or wilful misconduct.</li>
    <li><b>Your responsibility.</b> Only contribute money you can afford to lose entirely. You are responsible for any taxes on your gains, and for making sure participation is legal where you live.</li>
    <li><b>No affiliation.</b> This pool is independent and is not affiliated with, endorsed by, or operated by the creators of the Kiyoshi software, Celo, Uniswap or Textile.</li>
  </ol>
</div>`

// ---------- data ----------
async function rpc(name, args) {
  const { data, error } = await sb.rpc(name, args)
  if (error) throw error
  return data
}

async function adminUsers(body) {
  const { data, error } = await sb.functions.invoke('admin-users', { body })
  if (error) {
    let msg = error.message
    try { msg = (await error.context.json()).error || msg } catch {}
    throw new Error(msg)
  }
  return data
}

async function syncFn(body) {
  const { data, error } = await sb.functions.invoke('sync-value', { body })
  if (error) throw new Error('Could not read the pool wallet right now. Try again in a minute.')
  return data
}
// Live pool-wallet value in USD, read on the server from the chain. Saves nothing.
async function liveValue() {
  const r = await syncFn({ peek: true })
  if (typeof r?.value !== 'number') throw new Error('The pool wallet address is not set (Admin, Settings).')
  return r.value
}
// Ask the server to refresh the fund value from the wallet; re-render if it changed and the admin isn't mid-edit.
async function syncValue() {
  try {
    const r = await syncFn({})
    const note = document.getElementById('sync-note')
    if (note && r?.resumes_at) {
      note.textContent = `Auto-update paused until ${new Date(r.resumes_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })} (recent deposit or withdrawal)`
    }
    if (!r?.recorded || document.querySelector('.modal')) return
    const a = document.activeElement
    if (a && ['INPUT', 'SELECT', 'TEXTAREA'].includes(a.tagName)) return
    await load(true)
  } catch { /* the page still shows the last saved value */ }
}

async function load(skipSync) {
  try {
    const [fund, me] = await Promise.all([rpc('fund_summary'), rpc('my_position')])
    state.fund = fund
    state.me = me
    state.admin = me.is_admin ? await rpc('admin_overview') : null
  } catch (e) {
    if (/not a fund member/i.test(errText(e))) {
      await sb.auth.signOut()
      return renderLogin('This login is not linked to a fund member. Contact the fund manager.')
    }
    return renderError(errText(e))
  }
  render()
  if (!state.me.disclaimer_accepted_at && !document.getElementById('gate')) showDisclaimerGate()
  loadWallet()
  loadTrades()
  if (!skipSync) syncValue()
}

// Trade history rebuilt on the server from the pool wallet's on-chain transactions.
async function loadTrades() {
  try {
    const { data, error } = await sb.functions.invoke('trade-stats', { body: {} })
    state.trades = error ? { error: 'Trading activity is unavailable right now.' } : data
  } catch {
    state.trades = { error: 'Trading activity is unavailable right now.' }
  }
  const el = document.getElementById('trade-stats')
  if (el) el.innerHTML = tradeStatsView()
}

// Live on-chain balance of the pool wallet (read-only, straight from the Celo RPC).
async function loadWallet() {
  const addr = state.fund?.bot_wallet
  if (!addr || !/^0x[0-9a-fA-F]{40}$/.test(addr)) return
  const call = (method, params) => fetch(C.celoRpc, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  }).then((r) => r.json()).then((j) => { if (j.error) throw new Error(j.error.message); return BigInt(j.result) })
  try {
    const data = '0x70a08231' + addr.slice(2).toLowerCase().padStart(64, '0')
    const [celo, ...stables] = await Promise.all([
      call('eth_getBalance', [addr, 'latest']),
      ...C.stables.map((t) => call('eth_call', [{ to: t.address, data }, 'latest'])),
    ])
    const rows = C.stables.map((t, i) => ({ symbol: t.symbol, amount: Number(stables[i]) / 10 ** t.decimals }))
    state.wallet = { ok: true, celo: Number(celo) / 1e18, rows, usd: rows.reduce((s, r) => s + r.amount, 0), at: new Date() }
  } catch {
    state.wallet = { ok: false }
  }
  const el = document.getElementById('wallet-live')
  if (el) el.innerHTML = walletLive()
}

// ---------- views ----------
function renderLogin(error) {
  $app.innerHTML = `
  <div class="login"><div class="login-box">
    ${brand('Midas × Kiyoshi Fund')}
    <div class="tagline">Private pool · members only</div>
    <div class="card">
      <form data-form="login">
        <label class="field"><span>Username</span><input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
        <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>
        ${error ? `<div class="msg err">${esc(error)}</div>` : ''}
        <button class="btn" type="submit">Sign in</button>
        <p class="note" style="margin:0">Logins are issued by the fund manager after your contribution is received.</p>
      </form>
    </div>
    <p class="note" style="font-size:11.5px;text-align:center;margin-top:18px">
      Participation carries a high risk of loss, including the loss of all capital contributed. Not a regulated investment product.
      Not financial advice.
    </p>
  </div></div>`
}

function renderError(msg) {
  $app.innerHTML = `<div class="login"><div class="login-box"><div class="card">
    <div class="msg err">${esc(msg)}</div><br>
    <button class="btn plain" data-action="reload">Try again</button>
    <button class="btn plain" data-action="logout">Sign out</button></div></div></div>`
}

function tabName() {
  const t = location.hash.replace('#', '')
  if (t === 'admin' && state.me?.is_admin) return 'admin'
  if (t === 'account') return 'account'
  return 'dashboard'
}

function render() {
  const tab = tabName()
  const tabs = [['dashboard', 'Dashboard'], ...(state.me.is_admin ? [['admin', 'Admin']] : []), ['account', 'Account']]
  $app.innerHTML = `
  <header class="top"><div class="top-in">
    ${brand()}
    <nav class="tabs">${tabs.map(([k, v]) => `<button class="tab ${k === tab ? 'on' : ''}" data-tab="${k}">${v}</button>`).join('')}</nav>
    <div class="who"><span>${esc(state.me.username || state.me.name)}</span><button class="btn plain small" data-action="logout">Sign out</button></div>
  </div></header>
  <main>
    ${state.flash ? `<div class="msg ${state.flash.ok ? 'ok' : 'err'}" style="margin-bottom:16px">${esc(state.flash.text)}</div>` : ''}
    ${tab === 'admin' ? adminView() : tab === 'account' ? accountView() : dashboardView()}
    <footer>${DISCLAIMER}</footer>
  </main>`
  state.flash = null
  if (tab === 'admin') wireAdmin()
}

function stat(label, value, sub, cls = '', hero = false) {
  return `<div class="card ${hero ? 'hero' : ''}"><div class="label">${label}</div><div class="big ${cls}">${value}</div><div class="sub">${sub}</div></div>`
}

function dashboardView() {
  const f = state.fund, m = state.me
  const pnl = n(f.current_value) - n(f.capital)
  const growth = (n(f.unit_price) - 1) * 100
  const myRet = n(m.capital) > 0 ? (n(m.net_profit) / n(m.capital)) * 100 : 0
  const locked = m.lock_until && m.lock_until > today() && n(f.early_exit_pct) > 0
  return `
  <div class="section-title">The pool</div>
  <div class="grid">
    ${stat('Fund value', money(f.current_value), `<span id="sync-note">Live from pool wallet · ${stamp(f.last_updated)}</span>`, '', true)}
    ${stat('Unit price', '$' + n(f.unit_price).toFixed(4), `<span class="${tone(growth)}">${pct(growth, true)}</span> since start`)}
    ${stat('Fund P&amp;L', money(pnl, true), `${pct(n(f.capital) ? (pnl / n(f.capital)) * 100 : 0, true)} on ${money(f.capital)} invested`, tone(pnl))}
    ${stat('Members', `${f.members} / ${f.total_slots}`, `${Math.max(f.total_slots - f.members, 0)} slots open`)}
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">${chart(f.history)}</div>
    <div class="card">${walletCard()}</div>
  </div>

  <div class="section-title">Trading activity</div>
  <div id="trade-stats">${tradeStatsView()}</div>

  <div class="section-title">Your position · ${esc(m.name)}</div>
  <div class="grid">
    ${stat('Your value', money(m.net_value), `after ${m.fee_exempt ? 'no' : pct(m.fee_pct)} manager fee`, '', true)}
    ${stat('Your profit', money(m.net_profit, true), `${pct(myRet, true)} on your capital`, tone(m.net_profit))}
    ${stat('Your share', pct(n(m.share) * 100), `${units(m.units)} units`)}
    ${stat('Capital invested', money(m.capital), `Deposited ${money(m.total_deposited)} · paid out ${money(m.total_withdrawn)}`)}
  </div>
  <div class="card" style="margin-top:12px">${barChart('Your P&amp;L', personalBuckets(m, f), 'p', { money: true, cumulative: true, personal: true })}
    <p class="note" style="margin-bottom:0">Your units times the change in unit price, before the manager fee. Deposits and withdrawals are not counted as profit.</p></div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">
      <div class="label">How your value is worked out</div>
      <div class="preview" style="margin-top:12px;border-style:solid;border-color:var(--line)">
        <div><span>Units × unit price</span><span>${units(m.units)} × $${price(m.unit_price)}</span></div>
        <div><span>Value before fee</span><span>${money(m.gross_value)}</span></div>
        <div><span>Profit before fee</span><span class="${tone(m.gross_profit)}">${money(m.gross_profit, true)}</span></div>
        <div><span>Manager fee (${m.fee_exempt ? 'exempt' : pct(m.fee_pct) + ' of profit'})</span><span>−${money(m.fee).replace('−', '')}</span></div>
        <div class="total"><span>Your value</span><span>${money(m.net_value)}</span></div>
      </div>
      <p class="note">The fee is only taken from profit above the capital you put in, and only when you withdraw. If your position is at a loss, no fee is charged.</p>
      ${locked ? `<div class="msg">Early-exit penalty of ${pct(f.early_exit_pct)} applies to withdrawals before ${day(m.lock_until)}.</div>` : ''}
    </div>
    <div class="card">
      <div class="label">Your history</div>
      <div class="table-wrap" style="margin-top:12px;border:0">
        <table><thead><tr><th>Date</th><th>Type</th><th class="num">Amount</th><th class="num">Unit price</th><th class="num">Units</th></tr></thead>
        <tbody>${(m.ledger || []).map((r) => `<tr>
          <td>${day(r.date)}</td>
          <td><span class="pill ${r.kind === 'deposit' ? 'gold' : ''}">${r.kind}</span></td>
          <td class="num">${r.kind === 'deposit' ? money(r.gross) : money(r.net_payout) + (n(r.fee) + n(r.penalty) > 0 ? ` <span class="muted">(fee ${money(n(r.fee) + n(r.penalty))})</span>` : '')}</td>
          <td class="num">$${price(r.unit_price)}</td>
          <td class="num">${units(r.units)}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">No entries yet</td></tr>'}</tbody></table>
      </div>
    </div>
  </div>`
}

function chart(history, title = 'Unit price · value of $1 at season start') {
  const pts = (history || []).map((h) => ({ t: new Date(h.t).getTime(), v: n(h.unit_price), value: n(h.value) }))
  if (pts.length < 2) return `<div class="label">${title.split(' · ')[0]}</div><p class="note">The chart appears after the next fund value update.</p>`
  const W = 600, H = 200, pad = 6
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1
  let lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v))
  if (hi - lo < 0.002) { lo -= 0.001; hi += 0.001 }
  const x = (t) => pad + ((t - t0) / Math.max(t1 - t0, 1)) * (W - 2 * pad)
  const y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - 2 * pad)
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(t1).toFixed(1)},${H} L${x(t0).toFixed(1)},${H} Z`
  const last = pts[pts.length - 1]
  const id = chartId({ kind: 'line', pts: pts.map((p) => ({ ...p, fx: x(p.t) / W, fy: y(p.v) / H })) })
  return `
  <div class="chart-head"><div class="label">${title}</div><div class="mono" style="font-size:13px">$${last.v.toFixed(4)}</div></div>
  <div class="readout" id="${id}-r">${lineReadout(last)} <span class="muted">· tap or drag the chart</span></div>
  <div class="scrub" data-chart="${id}">
  <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Unit price history">
    <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#c9a84c" stop-opacity=".28"/><stop offset="1" stop-color="#c9a84c" stop-opacity="0"/></linearGradient></defs>
    ${lo < 1 && hi > 1 ? `<line x1="0" x2="${W}" y1="${y(1)}" y2="${y(1)}" stroke="#2a2a2e" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>` : ''}
    <path d="${area}" fill="url(#g)"/>
    <path d="${line}" fill="none" stroke="#e4c56a" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
  </svg><i class="cursor"></i><i class="dot"></i></div>
  <div class="row sub" style="justify-content:space-between"><span>${day(new Date(t0).toISOString())}</span><span>low $${lo.toFixed(4)} · high $${hi.toFixed(4)}</span><span>${day(new Date(t1).toISOString())}</span></div>`
}
const lineReadout = (p) => `<b>$${p.v.toFixed(4)}</b> · fund ${money(p.value)} · ${stamp(new Date(p.t).toISOString())}`

// ---------- tap / drag a chart for exact numbers ----------
const CHARTS = new Map()
let chartSeq = 0, scrubbing = null
function chartId(data) { const id = 'ch' + (++chartSeq); CHARTS.set(id, data); return id }
const exact = (v) => { const x = n(v); return Math.abs(x) > 0 && Math.abs(x) < 1 ? (x < 0 ? '−$' : '+$') + Math.abs(x).toFixed(4) : money(x, true) }
function scrubAt(el, clientX) {
  const c = CHARTS.get(el.dataset.chart), out = document.getElementById(el.dataset.chart + '-r')
  if (!c || !out) return
  const r = el.getBoundingClientRect(), fx = Math.min(Math.max((clientX - r.left) / r.width, 0), 1)
  const cur = el.querySelector('.cursor'), dot = el.querySelector('.dot')
  if (c.kind === 'line') {
    let best = c.pts[0]
    for (const p of c.pts) if (Math.abs(p.fx - fx) < Math.abs(best.fx - fx)) best = p
    cur.style.cssText = `display:block;left:${best.fx * 100}%;width:0`
    dot.style.cssText = `display:block;left:${best.fx * 100}%;top:${best.fy * 100}%`
    out.innerHTML = lineReadout(best)
    return
  }
  const i = Math.min(c.buckets.length - 1, Math.max(0, Math.floor((fx * c.W - c.pad) / c.bw)))
  const b = c.buckets[i], v = b[c.key]
  cur.style.cssText = `display:block;left:${((c.pad + i * c.bw) / c.W) * 100}%;width:${(c.bw / c.W) * 100}%`
  const cum = c.buckets.slice(0, i + 1).reduce((s, x) => s + x[c.key], 0)
  const parts = c.money ? [`<b class="${tone(v)}">${exact(v)}</b>`] : [`<b>${v} trade${v === 1 ? '' : 's'}</b>`]
  if (c.money && c.trades) parts.push(`${b.n} trade${b.n === 1 ? '' : 's'}`)
  if (!c.money) parts.push(`${exact(b.p)} profit`)
  if (c.cumulative) parts.push(`running total ${exact(cum)}`)
  out.innerHTML = `${parts.join(' · ')} · ${rangeText(b.start, c.range)}`
}
// The bucket's time span, in full: "Thu 29 Oct, 14:00–15:00", "Thu 29 Oct 2026", "26 Oct – 1 Nov"
function rangeText(ms, range) {
  const d = new Date(ms), unit = (RANGES[range] || RANGES.day).unit
  if (unit === 'hour') return `${d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })}, ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}–${new Date(ms + 3_600_000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`
  if (unit === 'day') return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })} – ${new Date(stepBack(ms, 'day', -6)).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}`
}
document.addEventListener('pointerdown', (e) => { const el = e.target.closest('.scrub'); if (el) { scrubbing = el; scrubAt(el, e.clientX) } })
document.addEventListener('pointermove', (e) => {
  const el = scrubbing || (e.pointerType === 'mouse' ? e.target.closest('.scrub') : null)
  if (el) scrubAt(el, e.clientX)
})
for (const t of ['pointerup', 'pointercancel']) document.addEventListener(t, () => { scrubbing = null })

function tradeStatsView() {
  const T = state.trades, f = state.fund
  if (!T) return `<div class="card"><div class="sub">Reading trades from the pool wallet\u2026</div></div>`
  if (T.error) return `<div class="card"><div class="sub">${esc(T.error)}</div></div>`
  const trades = T.trades || []
  const hours = Math.max(1, (T.now - T.since) / 3_600_000)
  const total = trades.reduce((s, x) => s + x.profit, 0)
  const memberProfit = f.members > 0 ? (n(f.current_value) - n(f.capital) - n(f.fees_accrued)) / f.members : 0
  const sinceLabel = day(new Date(T.since).toISOString())

  const wins = trades.filter((x) => x.profit > 0).length
  const volume = trades.reduce((s, x) => s + x.size, 0) * 2 // every trade is a buy and a sell
  const fundBuckets = bucketize(trades.map((x) => ({ t: x.t, p: x.profit, n: 1 })), state.range, T.now)

  return `
  <div class="grid">
    ${stat('Win rate', trades.length ? pct((wins / trades.length) * 100) : '—', `${wins} of ${trades.length} trades made a profit`)}
    ${stat('Traded volume', money(volume), `buys + sells since ${sinceLabel}`)}
    ${stat('Number of trades', String(trades.length), `${(trades.length / hours).toFixed(1)} per hour on average`)}
    ${stat('Trading profit', money(total, true), `${money(total / hours, true)} per hour · before gas`, tone(total))}
  </div>
  <div class="grid" style="margin-top:12px">
    ${stat('Avg profit per trade', trades.length ? money(total / trades.length, true) : '—', trades.length ? `on an average trade of ${money(trades.reduce((s, x) => s + x.size, 0) / trades.length)}` : 'no trades yet', tone(total))}
    ${stat('Avg profit per member', money(memberProfit, true), `after manager fee · ${f.members} member${f.members === 1 ? '' : 's'}`, tone(memberProfit))}
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">${barChart('Fund P&amp;L', fundBuckets, 'p', { money: true, cumulative: true })}</div>
    <div class="card">${barChart('Number of trades', fundBuckets, 'n', {})}</div>
  </div>
  <p class="note">Read from the pool wallet's transactions on the Celo blockchain. A trade is one buy and the sale that follows it; profit is the USDT that came back minus the USDT that went out, before gas. Times are in your phone's time zone.</p>`
}

// ---------- charts with an hourly / daily / weekly toggle ----------
const RANGES = { hour: { label: 'Hourly', count: 48, unit: 'hour' }, day: { label: 'Daily', count: 30, unit: 'day' }, week: { label: 'Weekly', count: 12, unit: 'week' } }
function bucketStart(t, unit) {
  const d = new Date(t)
  if (unit === 'hour') { d.setMinutes(0, 0, 0); return d.getTime() }
  d.setHours(0, 0, 0, 0)
  if (unit === 'week') d.setDate(d.getDate() - ((d.getDay() + 6) % 7)) // weeks start on Monday
  return d.getTime()
}
function stepBack(t, unit, k) {
  const d = new Date(t)
  if (unit === 'hour') d.setHours(d.getHours() - k); else d.setDate(d.getDate() - k * (unit === 'week' ? 7 : 1))
  return d.getTime()
}
// items: { t, p (profit), n (count) } -> the last `count` buckets of the range, oldest first
function bucketize(items, range, now = Date.now()) {
  const R = RANGES[range] || RANGES.day, end = bucketStart(now, R.unit)
  const buckets = Array.from({ length: R.count }, (_, i) => ({ start: stepBack(end, R.unit, R.count - 1 - i), p: 0, n: 0 }))
  const index = new Map(buckets.map((b, i) => [b.start, i]))
  for (const it of items) { const i = index.get(bucketStart(it.t, R.unit)); if (i !== undefined) { buckets[i].p += it.p; buckets[i].n += it.n } }
  return buckets
}
function rangeToggle() {
  return `<div class="seg">${Object.entries(RANGES).map(([k, r]) => `<button class="${k === state.range ? 'on' : ''}" data-action="range" data-range="${k}">${r.label}</button>`).join('')}</div>`
}
function bucketLabel(ms, long = true) {
  const unit = (RANGES[state.range] || RANGES.day).unit, d = new Date(ms)
  if (unit === 'hour') return d.toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  return (unit === 'week' && long ? 'Week of ' : '') + d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}
function barChart(title, buckets, key, opts) {
  const W = 600, H = 170, pad = 4, bw = (W - pad * 2) / buckets.length
  const vals = buckets.map((b) => b[key]), total = vals.reduce((a, b) => a + b, 0)
  const maxV = Math.max(opts.money ? 0.01 : 1, ...vals.map(Math.abs))
  const fmt = (v) => (opts.money ? money(v, true) : String(v))
  const bars = buckets.map((b, i) => {
    const v = b[key], h = Math.max(v ? 2 : 0, (Math.abs(v) / maxV) * (H - 24)), x = pad + i * bw + bw * .15
    return `<rect x="${x.toFixed(1)}" y="${(H - h).toFixed(1)}" width="${(bw * .7).toFixed(1)}" height="${h.toFixed(1)}" fill="${v >= 0 ? '#e4c56a' : '#8d8d94'}" rx="1"/>`
  }).join('')
  let line = ''
  if (opts.cumulative) { // running total, scaled to the chart
    let run = 0; const cum = vals.map((v) => (run += v)), lo = Math.min(0, ...cum), hi = Math.max(0.01, ...cum)
    const y = (v) => H - 4 - ((v - lo) / (hi - lo || 1)) * (H - 28)
    line = `<path d="${cum.map((v, i) => `${i ? 'L' : 'M'}${(pad + i * bw + bw / 2).toFixed(1)},${y(v).toFixed(1)}`).join(' ')}" fill="none" stroke="#f4f4f0" stroke-opacity=".55" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`
  }
  const id = chartId({ kind: 'bar', buckets, key, W, pad, bw, range: state.range, money: !!opts.money, cumulative: !!opts.cumulative, trades: !opts.personal })
  const every = Math.ceil(buckets.length / 4)
  const ticks = buckets.map((b, i) => (i % every === 0 ? `<span style="position:absolute;left:${((pad + i * bw) / W) * 100}%;white-space:nowrap">${bucketLabel(b.start, false)}</span>` : '')).join('')
  return `
  <div class="chart-head"><div class="label">${title} · ${RANGES[state.range].label.toLowerCase()}</div>${rangeToggle()}</div>
  <div class="sub" style="margin:-4px 0 8px">${fmt(opts.money ? Math.round(total * 100) / 100 : total)} over the last ${RANGES[state.range].count} ${RANGES[state.range].unit}s${opts.cumulative ? ' · line = running total' : ''}</div>
  <div class="readout" id="${id}-r"><span class="muted">Tap or drag across the bars for exact numbers</span></div>
  <div class="scrub" data-chart="${id}">
  <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:170px" role="img" aria-label="${title}">
    <line x1="0" x2="${W}" y1="${H - 0.5}" y2="${H - 0.5}" stroke="#2a2a2e" vector-effect="non-scaling-stroke"/>${bars}${line}
  </svg><i class="cursor"></i></div>
  <div class="sub" style="position:relative;height:16px;margin-top:6px;overflow:hidden">${ticks}</div>`
}
// A member's own P&L per bucket: for each fund value update, the member's units at the time multiplied by the
// change in unit price. Deposits and withdrawals change units, not P&L, so they are not counted as profit.
function personalBuckets(m, f) {
  const hist = (f.history || []).map((h) => ({ t: new Date(h.t).getTime(), price: n(h.unit_price) })).sort((a, b) => a.t - b.t)
  const rows = (m.ledger || []).map((r) => ({ t: new Date(String(r.date).length === 10 ? r.date + 'T00:00:00' : r.date).getTime(), u: n(r.units) })).sort((a, b) => a.t - b.t)
  const unitsAt = (t) => rows.reduce((s, r) => (r.t <= t ? s + r.u : s), 0)
  const items = []
  for (let i = 1; i < hist.length; i++) items.push({ t: hist[i].t, p: unitsAt(hist[i - 1].t) * (hist[i].price - hist[i - 1].price), n: 0 })
  return bucketize(items, state.range)
}

function walletCard() {
  const a = state.fund.bot_wallet
  if (!a) return `<div class="label">Pool wallet</div><p class="note">The manager has not published the pool wallet yet.</p>`
  return `
  <div class="label">Pool wallet · Celo</div>
  <div class="addr">${esc(a)}</div>
  <div class="row">
    <a class="btn ghost small" href="https://celoscan.io/address/${esc(a)}" target="_blank" rel="noopener">View on Celoscan ↗</a>
    <button class="btn plain small" data-action="copy" data-value="${esc(a)}">Copy</button>
  </div>
  <div id="wallet-live" style="margin-top:14px">${walletLive()}</div>`
}

function walletLive() {
  const w = state.wallet
  if (!w) return `<div class="sub">Reading live balance…</div>`
  if (!w.ok) return `<div class="sub">Live balance unavailable right now. Check it on Celoscan.</div>`
  return `
  <div class="label">Live on-chain balance</div>
  <div class="big" style="font-size:24px">${money(w.usd)}</div>
  <div class="sub">${w.rows.map((r) => `${r.amount.toLocaleString('en-US', { maximumFractionDigits: 2 })} ${r.symbol}`).join(' · ')} · ${w.celo.toFixed(3)} CELO</div>
  <p class="note" style="margin-bottom:0">Stablecoins held right now. This can differ from the fund value while trades are open or before new money is added to the pool.</p>`
}

function accountView() {
  return `
  <div class="section-title">Account</div>
  <div class="grid two">
    <div class="card">
      <div class="label">Change password</div><br>
      <form data-form="password">
        <label class="field"><span>New password</span><input name="p1" type="password" minlength="8" autocomplete="new-password" required></label>
        <label class="field"><span>Repeat new password</span><input name="p2" type="password" minlength="8" autocomplete="new-password" required></label>
        <button class="btn" type="submit">Update password</button>
      </form>
    </div>
    <div class="card">
      <div class="label">Your login</div>
      <p class="mono">${esc(state.me.username || '—')}</p>
      <p class="note">Disclaimer accepted ${stamp(state.me.disclaimer_accepted_at)}.</p>
    </div>
  </div>`
}

// ---------- admin ----------
function memberOptions(filter = () => true) {
  return state.admin.members.filter(filter).map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join('')
}

function adminView() {
  const f = state.fund, A = state.admin
  const holders = A.members.filter((m) => n(m.units) > 0)
  return `
  <div class="section-title">Fund value</div>
  <div class="grid two">
    <div class="card">
      <form data-form="value">
        <div class="label">Manual override</div>
        <label class="field"><span>Current total fund value (USD)</span><input name="value" type="number" step="0.01" min="0" value="${n(f.current_value).toFixed(2)}" required></label>
        <label class="field"><span>Note (optional)</span><input name="note" placeholder="e.g. Daily update"></label>
        <button class="btn" type="submit">Save fund value</button>
        <p class="note" style="margin:0">The fund value updates itself from the pool wallet whenever someone opens the site (at most every 2 minutes, and not within 5 minutes of a deposit or withdrawal). Use this only to correct it. The next automatic update will replace it.</p>
      </form>
    </div>
    <div class="card">
      <div class="label">Fees</div>
      <div class="big pos">${money(f.fees_accrued)}</div>
      <div class="sub">accrued on open positions (${pct(f.fee_pct)} of profit)</div><br>
      <div class="label">Collected (kept as your units)</div>
      <div class="big" style="font-size:22px">${money(f.fees_collected)}</div>
      <div class="sub">from withdrawals so far</div>
    </div>
  </div>
  <div class="table-wrap" style="margin-top:12px"><table>
    <thead><tr><th>When</th><th class="num">Value</th><th>Note</th><th></th></tr></thead>
    <tbody>${A.snapshots.map((s) => `<tr><td>${stamp(s.recorded_at)}</td><td class="num">${money(s.value)}</td><td>${esc(s.note || '')}</td>
      <td class="num">${A.snapshots.length > 1 ? `<button class="btn plain small" data-action="del-snap" data-id="${s.id}">Delete</button>` : ''}</td></tr>`).join('')}</tbody>
  </table></div>
  <p class="note">To remove a wrong deposit or withdrawal, use <b>Undo latest entry</b> in the Ledger section below. It also takes the money back out of the fund value.</p>

  <div class="section-title">Members</div>
  <div class="table-wrap"><table>
    <thead><tr><th>Name</th><th>Login</th><th class="num">Units</th><th class="num">Share</th><th class="num">Capital</th><th class="num">Value</th><th class="num">Fee</th><th class="num">After fee</th><th></th></tr></thead>
    <tbody>${A.members.map((m) => `<tr>
      <td>${esc(m.name)} ${m.is_admin ? '<span class="pill gold">admin</span>' : ''} ${m.fee_exempt ? '<span class="pill">no fee</span>' : ''}</td>
      <td>${m.has_login ? esc(m.username) + (m.disclaimer_accepted_at ? '' : ' <span class="pill">not signed in yet</span>') : '<span class="muted">none</span>'}</td>
      <td class="num">${units(m.units)}</td><td class="num">${pct(n(m.share) * 100)}</td><td class="num">${money(m.capital)}</td>
      <td class="num">${money(m.gross_value)}</td><td class="num">${money(m.fee)}</td><td class="num">${money(m.net_value)}</td>
      <td class="num"><div class="row" style="justify-content:flex-end;flex-wrap:nowrap">
        ${m.has_login
          ? `<button class="btn plain small" data-action="reset" data-id="${m.id}" data-name="${esc(m.name)}">Reset password</button>
             ${m.is_admin ? '' : `<button class="btn plain small" data-action="unlink" data-id="${m.id}" data-name="${esc(m.name)}">Remove login</button>`}`
          : `<button class="btn ghost small" data-action="login" data-id="${m.id}" data-name="${esc(m.name)}">Create login</button>`}
        ${!m.has_login && !m.is_admin && n(m.total_deposited) === 0 ? `<button class="btn plain small" data-action="del-member" data-id="${m.id}">Delete</button>` : ''}
      </div></td></tr>`).join('')}</tbody>
  </table></div>

  <div class="grid two" style="margin-top:12px">
    <div class="card">
      <div class="label">Add member</div><br>
      <form data-form="member">
        <label class="field"><span>Name</span><input name="name" required></label>
        <label class="field"><span>Username (optional)</span><input name="username" autocapitalize="none" spellcheck="false" placeholder="e.g. stephen"></label>
        <label class="field"><span>Password (optional, 8+ characters)</span><input name="password" type="text" autocomplete="off"></label>
        <label class="check"><input type="checkbox" name="fee_exempt"> No manager fee for this member</label>
        <button class="btn" type="submit">Add member</button>
        <p class="note" style="margin:0">Then record their deposit below. You can create the login later from the table above.</p>
      </form>
    </div>
    <div class="card">
      <div class="label">Record deposit</div><br>
      <form data-form="deposit">
        <label class="field"><span>Member</span><select name="member" required>${memberOptions()}</select></label>
        <label class="field"><span>Amount (USD)</span><input name="amount" type="number" step="0.01" min="0.01" required></label>
        <label class="field"><span>Date received</span><input name="date" type="date" value="${today()}"></label>
        <label class="check"><input type="checkbox" name="inwallet" checked> The money is already in the pool wallet</label>
        <label class="check"><input type="checkbox" name="founder"> Founder deposit (buys at $1.00)</label>
        <button class="btn" type="submit">Record deposit</button>
        <p class="note" style="margin:0">The site reads the pool wallet live and works out the fund value just before this money arrived, so the member buys units at the right price. If the money is not in the wallet yet, untick the first box and send it within 5 minutes.</p>
      </form>
    </div>
  </div>

  <div class="section-title">Withdrawal</div>
  <div class="grid two">
    <div class="card">
      <form data-form="withdraw">
        <label class="field"><span>Member</span><select name="member" required>${memberOptions((m) => n(m.units) > 0)}</select></label>
        <label class="field"><span>Amount to take out, before fees (USD)</span><input name="amount" type="number" step="0.01" min="0.01"></label>
        <label class="check"><input type="checkbox" name="all"> Withdraw everything</label>
        <label class="field"><span>Date paid</span><input name="date" type="date" value="${today()}"></label>
        <button class="btn ghost" type="submit">Preview</button>
        <p class="note" style="margin:0">Uses the pool wallet's live value. Confirm here first, then send the money within 5 minutes.</p>
      </form>
    </div>
    <div class="card" id="wd-preview">${withdrawPreview()}</div>
  </div>

  <div class="section-title">Ledger</div>
  <div class="row" style="margin-bottom:10px"><button class="btn plain small" data-action="undo" ${A.ledger.length ? '' : 'disabled'}>Undo latest entry</button>
    <span class="note">Removes the newest row. Tap again to remove the one before it.</span></div>
  <div class="table-wrap"><table>
    <thead><tr><th>#</th><th>Date</th><th>Member</th><th>Type</th><th class="num">Amount</th><th class="num">Fee</th><th class="num">Penalty</th><th class="num">Paid out</th><th class="num">Unit price</th><th class="num">Units</th></tr></thead>
    <tbody>${A.ledger.map((r) => `<tr><td>${r.seq}</td><td>${day(r.date)}</td><td>${esc(r.member)}</td>
      <td><span class="pill ${r.kind === 'deposit' ? 'gold' : ''}">${r.note && r.note.includes('Fee') ? 'fee \u2192 units' : r.kind}</span></td>
      <td class="num">${money(r.gross)}</td><td class="num">${r.kind === 'withdrawal' ? money(r.fee) : ''}</td>
      <td class="num">${r.kind === 'withdrawal' ? money(r.penalty) : ''}</td><td class="num">${r.kind === 'withdrawal' ? money(r.net_payout) : ''}</td>
      <td class="num">$${price(r.unit_price)}</td><td class="num">${units(r.units)}</td></tr>`).join('') || '<tr><td colspan="10" class="muted">No entries</td></tr>'}</tbody>
  </table></div>

  <div class="section-title">Leaderboard</div>
  <div class="card">
    <canvas id="lb" width="1080" height="1350" style="width:100%;max-width:440px;height:auto;display:block;margin:0 auto;border:1px solid var(--line)" role="img" aria-label="Member leaderboard image"></canvas>
    <div class="row" style="justify-content:center;margin-top:14px">
      <button class="btn" data-action="lb-download">Download image</button>
      ${navigator.canShare ? '<button class="btn ghost" data-action="lb-share">Share</button>' : ''}
      <button class="btn plain" data-action="lb-caption">New caption</button>
      <button class="btn plain" data-action="lb-names">Names: ${LB_NAMES[state.lb.names]}</button>
    </div>
    <p class="note" style="text-align:center">ROI is each member's profit after the manager fee, divided by the money they put in. Only you can see this card; the image shows whatever names option you pick.</p>
  </div>

  <div class="section-title">Settings</div>
  <div class="card">
    <form data-form="settings" class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label class="field"><span>Fund name</span><input name="fund_name" value="${esc(f.fund_name)}" required></label>
      <label class="field"><span>Manager fee (% of profit)</span><input name="fee_pct" type="number" step="0.01" min="0" max="99" value="${n(f.fee_pct)}" required></label>
      <label class="field"><span>Total slots</span><input name="total_slots" type="number" min="1" value="${f.total_slots}" required></label>
      <label class="field"><span>Lock period (days)</span><input name="lock_days" type="number" min="0" value="${f.lock_days}" required></label>
      <label class="field"><span>Early-exit penalty (%)</span><input name="early_exit_pct" type="number" step="0.01" min="0" max="99" value="${n(f.early_exit_pct)}" required></label>
      <label class="field"><span>Start date</span><input name="started_at" type="date" value="${f.started_at || ''}"></label>
      <label class="field" style="grid-column:1/-1"><span>Pool wallet address (Celo)</span><input name="bot_wallet" value="${esc(f.bot_wallet || '')}" pattern="^0x[0-9a-fA-F]{40}$" placeholder="0x…"></label>
      <div><button class="btn" type="submit">Save settings</button></div>
    </form>
    <p class="note">The early-exit penalty applies when a member withdraws less than the lock period after their first deposit. It stays in the fund for the remaining members. Set either to 0 to switch it off.</p>
  </div>`
}

function withdrawPreview() {
  const p = state.preview
  if (!p) return `<div class="label">Preview</div><p class="note">Fill in the form and tap Preview to see the payout, the fee and any penalty before anything is saved.</p>`
  return `
  <div class="label">Preview · ${esc(p.member)}</div>
  <div class="preview" style="margin-top:12px">
    <div><span>Units sold</span><span>${units(p.units_sold)} × $${price(p.unit_price)}</span></div>
    <div><span>Value of units sold</span><span>${money(p.gross)}</span></div>
    <div><span>Their capital in those units</span><span>${money(p.cost)}</span></div>
    <div><span>Profit</span><span class="${tone(p.profit)}">${money(p.profit, true)}</span></div>
    <div><span>Manager fee (${pct(p.fee_pct)})</span><span>${money(p.fee)}</span></div>
    <div><span>Early-exit penalty${p.early ? ` (${pct(p.penalty_pct)})` : ''}</span><span>${money(p.penalty)}</span></div>
    <div class="total"><span>Send to member</span><span>${money(p.net_payout)}</span></div>
    <div><span>Fund value after</span><span>${money(p.fund_value_after)}</span></div>
    <div><span>Units left</span><span>${units(p.units_left)}${p.full_exit ? ' (full exit)' : ''}</span></div>
  </div><br>
  <div class="row"><button class="btn" data-action="confirm-wd">Confirm withdrawal</button><button class="btn plain" data-action="cancel-wd">Cancel</button></div>
  <p class="note">Send ${money(p.net_payout)} to the member. Your ${money(p.fee)} fee stays in the pool wallet and is added to your units automatically.</p>`
}

// ---------- leaderboard image ----------
const LB_NAMES = ['Full names', 'Initials', 'Hidden']
const LB_CAPTIONS = [
  "While you were \u201cthinking about it\u201d, we were compounding.",
  'Some people bought the dip. Some people bought excuses.',
  'Imagine getting paid while you sleep. Could\u2019ve been you.',
  'Ask your bank what \u201cinterest\u201d means. Then ask us.',
  'This could have been your name. Just saying.',
  'Slots are limited. Your FOMO is not.',
  'Missed the boat? It\u2019s still sailing. Without you.',
  'The bot never sleeps. Neither do our gains.',
]

function lbName(name, i) {
  if (state.lb.names === 1) return String(name).trim().charAt(0).toUpperCase() + '.'
  if (state.lb.names === 2) return `Member #${i + 1}`
  return String(name)
}

function wrapText(ctx, text, maxWidth) {
  const lines = []
  let line = ''
  for (const word of text.split(' ')) {
    const test = line ? line + ' ' + word : word
    if (ctx.measureText(test).width > maxWidth && line) { lines.push(line); line = word } else line = test
  }
  if (line) lines.push(line)
  return lines
}

async function drawLeaderboard() {
  const cv = document.getElementById('lb')
  if (!cv) return
  try {
    await Promise.all([document.fonts.load('600 80px "Cormorant Garamond"'), document.fonts.load('italic 600 44px "Cormorant Garamond"'),
      document.fonts.load('500 30px "JetBrains Mono"'), document.fonts.load('700 30px "JetBrains Mono"')])
  } catch { /* fallback fonts */ }
  const ctx = cv.getContext('2d')
  const W = 1080, H = 1350
  const f = state.fund
  const GOLD = '#c9a84c', GOLD_HI = '#e4c56a', WHITE = '#f5f5f2', GREY = '#8d8d94', LINE = '#2a2a2e'
  const MONO = '"JetBrains Mono", ui-monospace, monospace', SERIF = '"Cormorant Garamond", Georgia, serif'
  const spaced = (px) => { if ('letterSpacing' in ctx) ctx.letterSpacing = px }

  const rows = state.admin.members
    .filter((m) => n(m.units) > 0 && n(m.capital) > 0)
    .map((m) => ({ name: m.name, roi: ((n(m.net_value) - n(m.capital)) / n(m.capital)) * 100 }))
    .sort((a, b) => b.roi - a.roi)
    .slice(0, 8)
    .map((r, i) => ({ ...r, name: lbName(r.name, i) }))

  // background, glow and double frame
  ctx.fillStyle = '#0a0a0b'; ctx.fillRect(0, 0, W, H)
  const glow = ctx.createRadialGradient(W / 2, 0, 40, W / 2, 0, 950)
  glow.addColorStop(0, 'rgba(201,168,76,0.24)'); glow.addColorStop(1, 'rgba(201,168,76,0)')
  ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H)
  ctx.strokeStyle = GOLD; ctx.lineWidth = 3; ctx.strokeRect(36, 36, W - 72, H - 72)
  ctx.strokeStyle = 'rgba(201,168,76,0.35)'; ctx.lineWidth = 1; ctx.strokeRect(50, 50, W - 100, H - 100)

  // header
  ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic'
  spaced('10px'); ctx.fillStyle = GREY; ctx.font = `500 24px ${MONO}`
  ctx.fillText('THE LEADERBOARD', W / 2, 130)
  spaced('2px'); ctx.fillStyle = WHITE; ctx.font = `600 76px ${SERIF}`
  const [a, b] = String(f.fund_name).split('×')
  if (b !== undefined) {
    const wa = ctx.measureText(a).width, wx = ctx.measureText('×').width, wb = ctx.measureText(b).width
    let x = W / 2 - (wa + wx + wb) / 2
    ctx.textAlign = 'left'
    ctx.fillText(a, x, 215); x += wa
    ctx.fillStyle = GOLD; ctx.fillText('×', x, 215); x += wx
    ctx.fillStyle = WHITE; ctx.fillText(b, x, 215)
    ctx.textAlign = 'center'
  } else ctx.fillText(f.fund_name, W / 2, 215)

  // headline fund return
  const growth = (n(f.unit_price) - 1) * 100
  const startT = f.history?.[0]?.t || f.started_at
  const days = startT ? Math.max(1, Math.round((Date.now() - new Date(startT).getTime()) / 86400000)) : null
  spaced('0px'); ctx.fillStyle = growth >= 0 ? GOLD_HI : WHITE; ctx.font = `700 132px ${MONO}`
  ctx.fillText(pct(growth, true), W / 2, 380)
  spaced('4px'); ctx.fillStyle = GREY; ctx.font = `500 26px ${MONO}`
  ctx.fillText(`FUND RETURN${days ? ` · ${days} DAY${days === 1 ? '' : 'S'}` : ''} · ${f.members} MEMBER${f.members === 1 ? '' : 'S'}`, W / 2, 430)

  // divider
  ctx.strokeStyle = LINE; ctx.lineWidth = 2
  ctx.beginPath(); ctx.moveTo(120, 480); ctx.lineTo(W - 120, 480); ctx.stroke()

  // rows
  const top = 520, bottom = 1060
  const rowH = Math.min(118, (bottom - top) / Math.max(rows.length, 1))
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.roi)))
  const left = 120, right = W - 120
  if (!rows.length) {
    ctx.fillStyle = GREY; ctx.font = `500 30px ${MONO}`; ctx.textAlign = 'center'
    ctx.fillText('No members on the board yet', W / 2, 760)
  }
  rows.forEach((r, i) => {
    const y = top + i * rowH
    const medal = ['#e4c56a', '#c8c8cc', '#c08a5a'][i]
    // rank badge
    ctx.beginPath(); ctx.arc(left + 30, y + 38, 30, 0, Math.PI * 2)
    ctx.fillStyle = medal ? medal : '#18181b'; ctx.fill()
    if (!medal) { ctx.strokeStyle = LINE; ctx.lineWidth = 2; ctx.stroke() }
    ctx.fillStyle = medal ? '#111' : WHITE; ctx.font = `700 28px ${MONO}`; ctx.textAlign = 'center'; spaced('0px')
    ctx.fillText(String(i + 1), left + 30, y + 48)
    // name and ROI
    ctx.textAlign = 'left'; ctx.fillStyle = WHITE; ctx.font = `600 ${i === 0 ? 50 : 44}px ${SERIF}`
    let name = r.name
    while (ctx.measureText(name).width > 520 && name.length > 3) name = name.slice(0, -2)
    if (name !== r.name) name = name.trimEnd() + '…'
    ctx.fillText(name, left + 82, y + 52)
    ctx.textAlign = 'right'; ctx.fillStyle = r.roi >= 0 ? GOLD_HI : '#b6b6bc'; ctx.font = `700 ${i === 0 ? 46 : 40}px ${MONO}`
    ctx.fillText(pct(r.roi, true), right, y + 52)
    // bar
    const barW = (right - left - 82) * Math.max(0.02, Math.abs(r.roi) / maxAbs)
    ctx.fillStyle = '#18181b'; ctx.fillRect(left + 82, y + 70, right - left - 82, 8)
    const bar = ctx.createLinearGradient(left + 82, 0, left + 82 + barW, 0)
    bar.addColorStop(0, r.roi >= 0 ? '#8a6d24' : '#3a3a3e'); bar.addColorStop(1, r.roi >= 0 ? GOLD_HI : '#8d8d94')
    ctx.fillStyle = bar; ctx.fillRect(left + 82, y + 70, barW, 8)
  })

  // caption
  const caption = LB_CAPTIONS[state.lb.caption % LB_CAPTIONS.length]
  ctx.textAlign = 'center'; ctx.fillStyle = WHITE; ctx.font = `italic 600 46px ${SERIF}`; spaced('0px')
  const lines = wrapText(ctx, caption, W - 260)
  lines.forEach((l, i) => ctx.fillText(l, W / 2, 1140 + i * 54 - (lines.length - 1) * 27))
  ctx.strokeStyle = GOLD; ctx.lineWidth = 2
  ctx.beginPath(); ctx.moveTo(W / 2 - 40, 1205); ctx.lineTo(W / 2 + 40, 1205); ctx.stroke()

  // footnote
  ctx.fillStyle = GREY; ctx.font = `400 17px ${MONO}`; spaced('1px')
  ctx.fillText(`ROI after fees as of ${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}.`, W / 2, 1250)
  ctx.fillText('Past returns don\u2019t guarantee future returns. Not financial advice.', W / 2, 1278)
  spaced('0px')
}

function lbBlob() {
  return new Promise((resolve) => document.getElementById('lb').toBlob(resolve, 'image/png'))
}

function wireAdmin() {
  drawLeaderboard()
  const all = document.querySelector('form[data-form="withdraw"] input[name="all"]')
  const amt = document.querySelector('form[data-form="withdraw"] input[name="amount"]')
  if (all) all.addEventListener('change', () => { amt.disabled = all.checked; if (all.checked) amt.value = '' })
  const founder = document.querySelector('form[data-form="deposit"] input[name="founder"]')
  const inwallet = document.querySelector('form[data-form="deposit"] input[name="inwallet"]')
  if (founder) founder.addEventListener('change', () => { inwallet.disabled = founder.checked })
}

// ---------- modal ----------
function modal(html) {
  const el = document.createElement('div')
  el.className = 'modal'
  el.innerHTML = `<div class="modal-box">${html}</div>`
  document.body.appendChild(el)
  return el
}

function showDisclaimerGate() {
  const el = modal(`${DISCLAIMER}
    <form data-form="accept">
      <label class="check"><input type="checkbox" name="ok" required> I have read and understood this disclosure. I accept the risks, including the possible loss of all the money I contribute, and the fee terms.</label>
      <div class="row"><button class="btn" type="submit">I agree</button><button class="btn plain" type="button" data-action="logout">Sign out</button></div>
    </form>`)
  el.id = 'gate'
}

function loginModal(id, name, reset) {
  modal(`
    <div class="label">${reset ? 'Reset password' : 'Create login'} · ${esc(name)}</div>
    <form data-form="${reset ? 'reset' : 'create-login'}" data-id="${id}">
      ${reset ? '' : `<label class="field"><span>Username</span><input name="username" autocapitalize="none" spellcheck="false" required pattern="[A-Za-z0-9._\\-]{3,30}"></label>`}
      <label class="field"><span>${reset ? 'New password' : 'Password'} (8+ characters)</span><input name="password" type="text" minlength="8" required autocomplete="off" value="${genPassword()}"></label>
      <p class="note" style="margin:0">Copy these details and send them to ${esc(name)} privately. Ask them to change the password under Account after signing in.</p>
      <div class="row"><button class="btn" type="submit">${reset ? 'Set password' : 'Create login'}</button><button class="btn plain" type="button" data-action="close-modal">Cancel</button></div>
    </form>`)
}

// In-page yes/no box: some phone browsers silently block window.confirm().
function ask(text) {
  return new Promise((resolve) => {
    const el = modal(`<p class="note" style="margin:0;font-size:15px;color:var(--white)">${esc(text)}</p>
      <div class="row"><button class="btn" type="button" data-answer="yes">Yes, continue</button><button class="btn plain" type="button" data-answer="no">Cancel</button></div>`)
    el.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-answer]')
      if (!b) return
      ev.stopPropagation()
      el.remove()
      resolve(b.dataset.answer === 'yes')
    })
  })
}

function genPassword() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
  const a = crypto.getRandomValues(new Uint32Array(12))
  return Array.from(a, (x) => chars[x % chars.length]).join('')
}

// ---------- events ----------
async function busy(btn, fn) {
  if (btn) btn.disabled = true
  try { await fn() } catch (e) {
    const box = btn?.closest('form, .modal-box, .card') || document.querySelector('main')
    box?.querySelector('.msg.err')?.remove()
    box?.insertAdjacentHTML('beforeend', `<div class="msg err">${esc(errText(e))}</div>`)
  } finally { if (btn) btn.disabled = false }
}
const flash = (text, ok = true) => { state.flash = { text, ok } }

document.addEventListener('submit', (ev) => {
  const form = ev.target.closest('form[data-form]')
  if (!form) return
  ev.preventDefault()
  const d = Object.fromEntries(new FormData(form))
  const btn = form.querySelector('button[type="submit"]')
  const kind = form.dataset.form
  busy(btn, async () => {
    if (kind === 'login') {
      const email = `${String(d.username).trim().toLowerCase()}@${C.emailDomain}`
      const { error } = await sb.auth.signInWithPassword({ email, password: d.password })
      if (error) return renderLogin('Wrong username or password.')
      return
    }
    if (kind === 'accept') {
      await rpc('accept_disclaimer')
      document.getElementById('gate')?.remove()
      state.me.disclaimer_accepted_at = new Date().toISOString()
      return
    }
    if (kind === 'password') {
      if (d.p1 !== d.p2) throw new Error('The two passwords do not match')
      const { error } = await sb.auth.updateUser({ password: d.p1 })
      if (error) throw error
      flash('Password updated.'); return render()
    }
    if (kind === 'value') {
      await rpc('admin_set_value', { p_value: Number(d.value), p_note: d.note || null })
      flash('Fund value saved.'); return load(true)
    }
    if (kind === 'member') {
      if (!!d.username !== !!d.password) throw new Error('Give both a username and a password, or neither')
      const id = await rpc('admin_add_member', { p_name: d.name, p_fee_exempt: !!d.fee_exempt })
      if (d.username) await adminUsers({ action: 'create_login', member_id: id, username: d.username, password: d.password })
      flash(`${d.name} added${d.username ? ` with login "${d.username.toLowerCase()}"` : ''}. Now record their deposit.`); return load()
    }
    if (kind === 'deposit') {
      let before = null
      if (!d.founder) {
        const live = await liveValue()
        before = Math.round((d.inwallet ? live - Number(d.amount) : live) * 100) / 100
        if (before < 0) throw new Error(`The pool wallet holds ${money(live)}, less than this deposit. Untick "already in the pool wallet" if the money has not arrived.`)
      }
      const r = await rpc('admin_add_deposit', { p_member: d.member, p_amount: Number(d.amount), p_on: d.date || null, p_value_before: before })
      flash(`Deposit recorded: ${units(r.units)} units at $${price(r.unit_price)}${before === null ? '' : ` (fund value before: ${money(before)})`}.`); return load(true)
    }
    if (kind === 'withdraw') {
      if (!d.all && !(Number(d.amount) > 0)) throw new Error('Enter an amount, or tick "Withdraw everything"')
      const args = { p_member: d.member, p_amount: d.all ? null : Number(d.amount), p_on: d.date || null, p_value_before: await liveValue() }
      state.preview = { ...(await rpc('admin_preview_withdrawal', args)), args }
      document.getElementById('wd-preview').innerHTML = withdrawPreview()
      document.getElementById('wd-preview').scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    if (kind === 'settings') {
      const { error } = await sb.from('settings').update({
        fund_name: d.fund_name, fee_pct: Number(d.fee_pct), total_slots: Number(d.total_slots),
        lock_days: Number(d.lock_days), early_exit_pct: Number(d.early_exit_pct),
        started_at: d.started_at || null, bot_wallet: d.bot_wallet || null, updated_at: new Date().toISOString(),
      }).eq('id', 1)
      if (error) throw error
      flash('Settings saved.'); return load()
    }
    if (kind === 'create-login' || kind === 'reset') {
      const id = form.dataset.id
      if (kind === 'create-login') await adminUsers({ action: 'create_login', member_id: id, username: d.username, password: d.password })
      else await adminUsers({ action: 'reset_password', member_id: id, password: d.password })
      form.closest('.modal').remove()
      flash(kind === 'reset' ? `New password set: ${d.password}` : `Login created. Username: ${d.username.toLowerCase()}  Password: ${d.password}`)
      return load()
    }
  })
})

document.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-tab], [data-action]')
  if (!t) return
  if (t.dataset.tab) { location.hash = t.dataset.tab; return }
  const a = t.dataset.action
  if (a === 'logout') { sb.auth.signOut(); document.querySelectorAll('.modal').forEach((m) => m.remove()); return }
  if (a === 'range') { state.range = t.dataset.range; try { localStorage.setItem('range', state.range) } catch { /* optional */ } render(); return }
  if (a === 'reload') return load()
  if (a === 'close-modal') return t.closest('.modal').remove()
  if (a === 'copy') { navigator.clipboard?.writeText(t.dataset.value); t.textContent = 'Copied'; return }
  if (a === 'login') return loginModal(t.dataset.id, t.dataset.name, false)
  if (a === 'reset') return loginModal(t.dataset.id, t.dataset.name, true)
  if (a === 'lb-caption') { state.lb.caption = (state.lb.caption + 1) % LB_CAPTIONS.length; drawLeaderboard(); return }
  if (a === 'lb-names') { state.lb.names = (state.lb.names + 1) % LB_NAMES.length; t.textContent = `Names: ${LB_NAMES[state.lb.names]}`; drawLeaderboard(); return }
  if (a === 'lb-download') {
    lbBlob().then((blob) => {
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = 'midas-kiyoshi-leaderboard.png'
      document.body.appendChild(link); link.click(); link.remove()
      setTimeout(() => URL.revokeObjectURL(link.href), 5000)
    })
    return
  }
  if (a === 'lb-share') {
    lbBlob().then((blob) => {
      const file = new File([blob], 'midas-kiyoshi-leaderboard.png', { type: 'image/png' })
      if (navigator.canShare?.({ files: [file] })) navigator.share({ files: [file], text: LB_CAPTIONS[state.lb.caption] }).catch(() => {})
    })
    return
  }
  if (a === 'cancel-wd') { state.preview = null; document.getElementById('wd-preview').innerHTML = withdrawPreview(); return }
  busy(t, async () => {
    if (a === 'confirm-wd') {
      const p = state.preview
      const r = await rpc('admin_withdraw', p.args)
      state.preview = null
      flash(`Withdrawal saved. Now send ${money(r.net_payout)} to ${r.member} within 5 minutes. Your ${money(r.fee)} fee stays in the pool as your units.`); return load(true)
    }
    if (a === 'unlink') {
      if (!await ask(`Remove ${t.dataset.name}'s login? Their money stays in the fund.`)) return
      await adminUsers({ action: 'remove_login', member_id: t.dataset.id })
      flash('Login removed.'); return load()
    }
    if (a === 'undo') {
      const last = state.admin.ledger[0]
      if (!await ask(`Undo the latest entry: ${last.kind} by ${last.member} for ${money(last.gross)}?`)) return
      await rpc('admin_undo_last'); flash('Latest entry undone.'); return load(true)
    }
    if (a === 'del-snap') {
      if (!await ask('Delete this fund value entry?')) return
      await rpc('admin_delete_snapshot', { p_id: Number(t.dataset.id) }); return load()
    }
    if (a === 'del-member') {
      if (!await ask('Delete this member?')) return
      await rpc('admin_delete_member', { p_member: t.dataset.id }); return load()
    }
  })
})

window.addEventListener('hashchange', () => { if (state.me) render() })

sb.auth.onAuthStateChange((event, session) => {
  const was = state.session?.user?.id
  state.session = session
  if (!session) { state.me = null; state.fund = null; state.admin = null; renderLogin() }
  else if (session.user.id !== was) setTimeout(load, 0)
})
sb.auth.getSession().then(({ data }) => { if (!data.session) renderLogin() })
