// Midas × Kiyoshi Fund site.
// Everyone sees Overview (track record), Dashboard (the fund, trading, your position) and Account (your wallet):
// a member's connected wallet is their identity (vault.js). Admin adds the manager's on-chain controls and, behind
// the admin sign-in, the off-chain tools (seasons, settings, leaderboard image).
const C = window.FUND_CONFIG
const sb = window.supabase.createClient(C.supabaseUrl, C.supabaseAnonKey)
const $app = document.getElementById('app')
const savedRange = (() => { try { return localStorage.getItem('range') } catch { return null } })()
const state = {
  range: ['hour', 'day', 'week'].includes(savedRange) ? savedRange : 'day',
  session: null, me: null, fund: null, admin: null, // admin sign-in (off-chain tools)
  pub: null, trades: null, wallet: null, flash: null, adminTab: false,
  lb: { caption: 0, names: 0 }, card: { amounts: true, name: 1 },
}

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
// Live pool-wallet value in USD, read on the server from the chain (admin: ending a season).
async function liveValue() {
  const { data, error } = await sb.functions.invoke('sync-value', { body: { peek: true } })
  if (error || typeof data?.value !== 'number') throw new Error('Could not read the pool wallet right now. Try again in a minute.')
  return data.value
}

async function loadPublic() {
  try { state.pub = await rpc('public_overview') } catch { state.pub = state.pub || { error: true } }
}

// The admin's off-chain data, when signed in. Members no longer sign in here: they connect a wallet.
async function loadAdmin() {
  state.me = state.fund = state.admin = null
  if (!state.session) return
  try {
    const [fund, me] = await Promise.all([rpc('fund_summary'), rpc('my_position')])
    if (!me.is_admin) {
      await sb.auth.signOut()
      state.flash = { text: 'Only the fund manager signs in here. Members connect a wallet instead.', ok: false }
      return
    }
    state.fund = fund; state.me = me
    state.admin = await rpc('admin_overview')
  } catch (e) {
    if (/not a fund member/i.test(errText(e))) await sb.auth.signOut()
    state.flash = { text: errText(e), ok: false }
  }
}

// Trade history rebuilt on the server from the pool wallet's on-chain transactions (public).
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
  const addr = state.pub?.bot_wallet
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

// ---------- shell ----------
// Without a connected wallet (or the admin sign-in) only the Overview is open.
const walletConnected = () => !!(window.vaultState?.account && !window.vaultState?.hidden)
const canEnter = () => walletConnected() || !!state.me?.is_admin
function tabName() {
  const t = location.hash.replace('#', '')
  const want = t === 'signin' ? 'admin' : ['dashboard', 'account', 'admin'].includes(t) ? t : 'overview'
  if (t === 'signin') return 'admin' // the admin sign-in form stays reachable at #signin
  return canEnter() ? want : 'overview'
}
const adminVisible = () => !!state.me?.is_admin || !!(window.vaultState?.d?.isManager || window.vaultState?.d?.isOwner)

function render() {
  const tab = tabName()
  state.adminTab = adminVisible()
  state.entered = canEnter()
  const tabs = [['overview', 'Overview'], ...(state.entered ? [['dashboard', 'Dashboard'], ['account', 'Account']] : []), ...(state.adminTab ? [['admin', 'Admin']] : [])]
  const view = { overview: overviewView, dashboard: dashboardView, account: accountView, admin: adminView }[tab]
  $app.innerHTML = `
  <header class="top"><div class="top-in">
    ${brand()}
    <nav class="tabs">${tabs.map(([k, v]) => `<button class="tab ${k === tab ? 'on' : ''}" data-tab="${k}">${v}</button>`).join('')}</nav>
    <div class="who" data-vx="connect"></div>
  </div></header>
  <main class="${tab === 'overview' ? 'landing' : ''}">
    ${state.flash ? `<div class="msg ${state.flash.ok ? 'ok' : 'err'}" style="margin-bottom:16px">${esc(state.flash.text)}</div>` : ''}
    ${state.pub?.error ? '<div class="msg err">The fund data could not be loaded right now. Try again in a minute.</div>' : view()}
    <footer>${DISCLAIMER}
      ${state.me?.is_admin ? `<p class="note" style="margin-top:18px">Admin: ${esc(state.me.username)} · <a href="#" data-action="logout">Sign out</a></p>` : ''}</footer>
  </main>`
  state.flash = null
  window.mountVault?.()
  if (tab === 'admin' && state.admin) drawLeaderboard()
  if (tab === 'dashboard' && !state.wallet) loadWallet()
}
// The vault's data arrives after the page is drawn: show the Admin tab once the manager wallet is recognised.
window.onVaultExtras = () => {
  const el = document.getElementById('my-pnl')
  if (el) el.innerHTML = myPnlView()
  if (adminVisible() !== state.adminTab || canEnter() !== state.entered) render()
}

function stat(label, value, sub, cls = '', hero = false) {
  return `<div class="card ${hero ? 'hero' : ''}"><div class="label">${label}</div><div class="big ${cls}">${value}</div><div class="sub">${sub}</div></div>`
}

// ---------- Overview (public track record) ----------
function overviewView() {
  const P = state.pub
  if (!P) return '<div class="boot">Loading…</div>'
  const past = (P.seasons || [])[0]
  const live = (n(P.unit_price) - 1) * 100
  const open = Math.max(n(P.total_slots) - n(P.members), 0)
  const T = past?.trades
  const best = past?.leaderboard?.[0]
  const hero = past
    ? { label: `${esc(past.name)} return`, value: pct(past.return_pct, true), cls: tone(past.return_pct),
        sub: `${day(past.started_at)} – ${day(past.ended_at)} · every $1 became $${n(past.end_unit_price).toFixed(2)}` }
    : { label: `Season ${P.season} so far`, value: pct(live, true), cls: tone(live), sub: `since ${day(P.season_started_at)}` }
  return `
    <section class="hero-band">
      <div class="tagline" style="text-align:left;margin:0 0 14px">Arbitrage fund · Celo</div>
      <h1>A bot that trades the gaps between stablecoin markets, <span class="pos">around the clock.</span></h1>
      <div class="hero-num">
        <div class="label">${hero.label}</div>
        <div class="giant ${hero.cls}">${hero.value}</div>
        <div class="sub">${hero.sub}</div>
      </div>
      <div class="row" style="margin-top:18px">
        ${walletConnected() ? '<button class="btn" data-tab="dashboard">Open your dashboard</button>' : '<button class="btn" data-vault="connect">Join with your wallet</button>'}
        <button class="btn plain" data-action="scroll" data-to="how">How it works</button>
      </div>
    </section>

    <video class="promo" src="media/fund-wide.mp4" poster="media/end-card.jpg" controls playsinline preload="none"></video>

    ${past ? `
    <div class="section-title">${esc(past.name)} · track record</div>
    <div class="grid">
      ${stat('Every $1 became', '$' + n(past.end_unit_price).toFixed(2), `${day(past.started_at)} – ${day(past.ended_at)}`, tone(past.return_pct), true)}
      ${stat('Paid to members', money(past.paid_out), `${past.members_paid_out} of ${past.members} members cashed out`)}
      ${best ? stat('Best member', pct(best.roi, true), 'return after fees', tone(best.roi)) : ''}
      ${T ? stat('Trades', String(T.count), `${T.count ? pct((T.wins / T.count) * 100) : '—'} win rate · ${money(T.volume)} traded`) : ''}
    </div>
    <div class="grid two" style="margin-top:12px">
      <div class="card">${chart(past.history, `Unit price · ${esc(past.name)}`)}</div>
      <div class="card"><div class="label" style="margin-bottom:10px">Leaderboard · ${esc(past.name)}</div>${boardView(past.leaderboard, 'No members that season.')}</div>
    </div>` : ''}

    <div class="section-title">Season ${P.season} · live</div>
    <div class="grid">
      ${stat('Return so far', pct(live, true), `since ${day(P.season_started_at)}`, tone(live), !past)}
      ${stat('Unit price', '$' + n(P.unit_price).toFixed(4), 'value of $1 at the start of the season')}
      ${stat('Fund value', money(P.current_value), `live from the pool wallet · ${stamp(P.last_updated)}`)}
      ${stat('Slots open', `${open} / ${P.total_slots}`, `${P.members} in the fund now`)}
    </div>
    <div class="card" style="margin-top:12px">${chart(P.history, `Unit price · Season ${P.season}`)}</div>

    <div class="section-title" id="how">How it works</div>
    <div class="grid steps">
      <div class="card"><div class="step">01</div><b>Connect</b><p class="note">Connect your wallet on the Dashboard and send the fund manager your address. Once it is approved, deposit USDT on Celo.</p></div>
      <div class="card"><div class="step">02</div><b>The bot trades</b><p class="note">Kiyoshi buys a stablecoin where it is cheap and sells it where it is dearer, across Uniswap, Mento and RFQ desks, many times a day.</p></div>
      <div class="card"><div class="step">03</div><b>Watch it live</b><p class="note">Your shares live in a smart contract and the fund's money in a public wallet: your value, your P&amp;L and every trade are on the blockchain.</p></div>
      <div class="card"><div class="step">04</div><b>Cash out</b><p class="note">Redeem whenever you like; it settles automatically at that day's share price. A ${n(P.fee_pct)}% fee is taken only from profit, never from your deposit, and never on a loss.</p></div>
    </div>

    <div class="section-title">Join</div>
    <div class="card hero join">
      <div><div class="label">Season ${P.season} · ${open} of ${P.total_slots} slots open</div>
        <p class="note" style="font-size:15px;color:var(--white)">Connect your wallet, send the manager your address to be approved, then deposit. Slots are limited.</p></div>
      ${walletConnected() ? '<button class="btn" data-tab="dashboard">Open your dashboard</button>' : '<button class="btn" data-vault="connect">Connect wallet</button>'}
    </div>`
}

// ---------- Dashboard ----------
function dashboardView() {
  const P = state.pub
  if (!P) return '<div class="boot">Loading…</div>'
  const v = P.vault || {}
  const live = (n(P.unit_price) - 1) * 100
  return `
  <div class="section-title">Your position${v.network === 'testnet' ? ' · testnet' : ''}</div>
  <div data-vx="wallet"></div>
  ${v.address ? `
  <div class="section-title">On-chain fund${v.network === 'testnet' ? ' · testnet' : ''}</div>
  <div data-vx="tiles"></div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">${chart(P.vault_history, 'Share price · on-chain fund')}</div>
    <div class="card" id="my-pnl">${myPnlView()}</div>
  </div>` : ''}

  <div class="section-title">Season ${P.season} · the pool</div>
  <div class="grid">
    ${stat('Fund value', money(P.current_value), `live from the pool wallet · ${stamp(P.last_updated)}`, '', true)}
    ${stat('Unit price', '$' + n(P.unit_price).toFixed(4), `<span class="${tone(live)}">${pct(live, true)}</span> since ${day(P.season_started_at)}`)}
    ${stat('Members', `${P.members} / ${P.total_slots}`, `${Math.max(n(P.total_slots) - n(P.members), 0)} slots open`)}
    ${stat('Fee', `${n(P.fee_pct)}%`, 'of profit only, never on a loss')}
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">${chart(P.history, `Unit price · Season ${P.season}`)}</div>
    <div class="card">${walletCard()}</div>
  </div>

  <div class="section-title">Trading activity</div>
  <div id="trade-stats">${tradeStatsView()}</div>

  <div class="section-title">Leaderboard · Season ${P.season}</div>
  <div class="card">${boardView(P.leaderboard, 'No members on the board yet this season.')}
    <p class="note" style="margin-bottom:0">Return after fees on what each member put in, counting payouts. Names are shortened; the manager is not ranked.</p></div>
  ${(P.seasons || []).map(seasonView).join('')}`
}

// The connected wallet's P&L per hour / day / week (on-chain fund).
function myPnlView() {
  const items = window.vaultPersonalItems?.()
  if (!items) return `<div class="label">Your P&amp;L</div><p class="note">Connect your wallet to see your P&amp;L. It appears after the fund's next value update.</p>`
  return barChart('Your P&amp;L', bucketize(items, state.range), 'p', { money: true, cumulative: true, personal: true }) +
    '<p class="note" style="margin-bottom:0">Your shares times the change in share price, before the fee. Deposits and redemptions are not counted as profit.</p>'
}

// ---------- Account ----------
function accountView() {
  return `
  <div class="section-title">Your account</div>
  <div data-vx="account"></div>
  ${state.me?.is_admin ? `
  <div class="section-title">Admin sign-in</div>
  <div class="card" style="max-width:520px">
    <form data-form="password">
      <label class="field"><span>New admin password</span><input name="p1" type="password" minlength="8" autocomplete="new-password" required></label>
      <label class="field"><span>Repeat new password</span><input name="p2" type="password" minlength="8" autocomplete="new-password" required></label>
      <button class="btn" type="submit">Update password</button>
    </form>
  </div>` : ''}`
}

// ---------- Admin ----------
function adminView() {
  return `
  <div class="section-title">On-chain fund · manager</div>
  <div data-vx="admin"></div>
  ${state.me?.is_admin ? offchainAdmin() : `
  <div class="section-title">Seasons and settings</div>
  <div class="card" style="max-width:420px">
    <form data-form="login">
      <label class="field"><span>Admin username</span><input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
      <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>
      <button class="btn" type="submit">Sign in</button>
      <p class="note" style="margin:0">Seasons, settings, the vault address and the leaderboard image need the admin sign-in. Pausing, approving wallets and limits only need your manager wallet.</p>
    </form>
  </div>`}`
}

function offchainAdmin() {
  const f = state.fund, v = state.pub?.vault || {}
  const growth = (n(f.unit_price) - 1) * 100
  return `
  <div class="section-title">Season ${f.season}</div>
  <div class="card">
    <div class="row" style="justify-content:space-between;align-items:flex-start">
      <div><div class="label">Season ${f.season} · since ${day(f.season_started_at)}</div>
        <div class="big ${tone(growth)}">${pct(growth, true)}</div>
        <div class="sub">unit price $${n(f.unit_price).toFixed(4)} · fund ${money(f.current_value)} · ${f.members} holder${f.members === 1 ? '' : 's'}</div></div>
      <button class="btn ghost" data-action="new-season">End Season ${f.season} · start Season ${f.season + 1}</button>
    </div>
    <p class="note" style="margin-bottom:0">Saves this season's results (return, payouts, trades and leaderboard), then restarts the unit price at $1.00 with everyone's value carried over. Uses the pool wallet's live value when you confirm.</p>
  </div>

  <div class="grid two" style="margin-top:12px">
    <form class="card" data-form="vault-config"><div class="label">On-chain fund contract</div>
      <label class="field" style="margin-top:10px"><span>Network</span><select name="network">
        <option value="testnet" ${v.network === 'testnet' ? 'selected' : ''}>Celo Sepolia (testnet)</option>
        <option value="mainnet" ${v.network === 'mainnet' ? 'selected' : ''}>Celo (mainnet)</option></select></label>
      <label class="field"><span>FundVault address</span><input name="vault" value="${esc(v.address || '')}" placeholder="0x…" autocapitalize="none" spellcheck="false"></label>
      <label class="field"><span>USDT address (tUSDT on testnet)</span><input name="usdt" value="${esc(v.usdt || '')}" placeholder="0x…" autocapitalize="none" spellcheck="false"></label>
      <button class="btn" type="submit">Save</button>
      <p class="note" style="margin:0">Paste what <span class="mono">npm run deploy</span> printed. The site and the keeper switch to it straight away.</p></form>
    <form class="card" data-form="value"><div class="label">Pool value · manual correction</div>
      <label class="field" style="margin-top:10px"><span>Current total fund value (USD)</span><input name="value" type="number" step="0.01" min="0" value="${n(f.current_value).toFixed(2)}" required></label>
      <label class="field"><span>Note (optional)</span><input name="note" placeholder="e.g. Correction"></label>
      <button class="btn plain" type="submit">Save fund value</button>
      <p class="note" style="margin:0">The keeper updates it from the pool wallet every hour; use this only to correct it.</p></form>
  </div>

  <div class="section-title">Leaderboard image</div>
  <div class="card">
    <canvas id="lb" width="1080" height="1350" style="width:100%;max-width:440px;height:auto;display:block;margin:0 auto;border:1px solid var(--line)" role="img" aria-label="Member leaderboard image"></canvas>
    <div class="row" style="justify-content:center;margin-top:14px">
      <button class="btn" data-action="lb-download">Download image</button>
      ${navigator.canShare ? '<button class="btn ghost" data-action="lb-share">Share</button>' : ''}
      <button class="btn plain" data-action="lb-caption">New caption</button>
      <button class="btn plain" data-action="lb-names">Names: ${LB_NAMES[state.lb.names]}</button>
    </div>
  </div>

  <div class="section-title">Settings</div>
  <div class="card">
    <form data-form="settings" class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label class="field"><span>Fund name</span><input name="fund_name" value="${esc(f.fund_name)}" required></label>
      <label class="field"><span>Manager fee shown (% of profit)</span><input name="fee_pct" type="number" step="0.01" min="0" max="99" value="${n(f.fee_pct)}" required></label>
      <label class="field"><span>Total slots</span><input name="total_slots" type="number" min="1" value="${f.total_slots}" required></label>
      <label class="field" style="grid-column:1/-1"><span>Pool wallet address (Celo)</span><input name="bot_wallet" value="${esc(f.bot_wallet || '')}" pattern="^0x[0-9a-fA-F]{40}$" placeholder="0x…"></label>
      <div><button class="btn" type="submit">Save settings</button></div>
    </form>
    <p class="note">The on-chain fee is set by your manager wallet in the panel above (48-hour delay); keep this one the same so the site shows the right number.</p>
  </div>`
}

// Ranked rows: masked names, return after fees, "You" on the viewer's row.
function boardView(rows, empty) {
  if (!rows?.length) return `<p class="note">${empty}</p>`
  return `<div class="board">${rows.map((r, i) => `<div class="board-row ${r.me ? 'me' : ''}">
    <span class="rank r${i + 1}">${i + 1}</span>
    <span class="who-name">${esc(r.label)}${r.me ? ' <span class="pill gold">you</span>' : ''}${r.active ? '' : ' <span class="pill">paid out</span>'}</span>
    <span class="roi ${tone(r.roi)}">${pct(r.roi, true)}</span></div>`).join('')}</div>`
}

// A finished season: its track record and final leaderboard.
function seasonView(z) {
  const T = z.trades
  return `
  <div class="section-title">${esc(z.name)} · final</div>
  <div class="grid">
    ${stat(`${esc(z.name)} return`, pct(z.return_pct, true), `${day(z.started_at)} – ${day(z.ended_at)} · unit price $1.00 → $${n(z.end_unit_price).toFixed(4)}`, tone(z.return_pct), true)}
    ${stat('Paid to members', money(z.paid_out), `${z.members_paid_out} of ${z.members} member${z.members === 1 ? '' : 's'} cashed out`)}
    ${T ? stat('Trades', String(T.count), `${T.count ? pct((T.wins / T.count) * 100) : '—'} win rate · ${money(T.volume)} volume`) : ''}
    ${T ? stat('Trading profit', money(T.profit, true), 'before gas, from the pool wallet\'s transactions', tone(T.profit)) : ''}
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">${chart(z.history, `Unit price · ${esc(z.name)}`)}</div>
    <div class="card"><div class="label" style="margin-bottom:10px">Leaderboard · ${esc(z.name)}</div>${boardView(z.leaderboard, 'No members that season.')}</div>
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
  <div class="row sub" style="justify-content:space-between;flex-wrap:nowrap;gap:6px;font-size:11px"><span>${new Date(t0).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</span><span>low $${lo.toFixed(4)} · high $${hi.toFixed(4)}</span><span>${new Date(t1).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</span></div>`
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
  const T = state.trades
  if (!T) return `<div class="card"><div class="sub">Reading trades from the pool wallet\u2026</div></div>`
  if (T.error) return `<div class="card"><div class="sub">${esc(T.error)}</div></div>`
  const trades = T.trades || []
  const hours = Math.max(1, (T.now - T.since) / 3_600_000)
  const total = trades.reduce((s, x) => s + x.profit, 0)
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
    ${stat('Avg profit per trade', trades.length ? money(total / trades.length, true) : '—', trades.length ? `on an average trade of ${money(trades.reduce((s, x) => s + x.size, 0) / trades.length)}` : 'no trades yet', tone(total))}
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">${barChart('Fund P&amp;L', fundBuckets, 'p', { money: true, cumulative: true })}</div>
    <div class="card">${barChart('Number of trades', fundBuckets, 'n', {})}</div>
  </div>
  <p class="note">Read from the pool wallet's transactions on the Celo blockchain. A trade is one buy and the sale that follows it; profit is the USDT that came back minus the USDT that went out, before gas. Times are in your device's time zone.</p>`
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
function walletCard() {
  const a = state.pub?.bot_wallet
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

function svgImg(svg, color) {
  return new Promise((resolve) => {
    const im = new Image()
    im.onload = () => resolve(im); im.onerror = () => resolve(null)
    im.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg.replace('<svg ', `<svg xmlns="http://www.w3.org/2000/svg" style="color:${color}" `))
  })
}
// The MIDAS × KIY◠SHI wordmark on a canvas, centred on cx with its baseline at y (same proportions as the page CSS).
async function drawWordmark(ctx, cx, y, size) {
  const [mi, fi] = await Promise.all([svgImg(M_GLYPH, '#e4c56a'), svgImg(fanSvg('x'), '#e4c56a')])
  ctx.font = `400 ${size}px "JetBrains Mono", ui-monospace, monospace`
  if ('letterSpacing' in ctx) ctx.letterSpacing = `${(size * 0.14).toFixed(1)}px`
  const mw = size * 0.89 * 73 / 88, fw = size * 0.68 * 100 / 60, w = (t) => ctx.measureText(t).width
  const parts = [['img', mi, mw, 0.89, 0.08, size * 0.1], ['txt', 'IDAS', '#e4c56a', size * 0.4], ['txt', '×', '#8d8d94', size * 0.55],
    ['txt', 'KIY', '#f5f5f2', 0], ['img', fi, fw, 0.68, 0.07, size * 0.12], ['txt', 'SHI', '#f5f5f2', 0]]
  const width = parts.reduce((a, p) => a + (p[0] === 'img' ? p[2] + p[5] : w(p[1]) + p[3]), 0)
  let x = cx - width / 2
  ctx.textAlign = 'left'
  for (const p of parts) {
    if (p[0] === 'img') { if (p[1]) ctx.drawImage(p[1], x, y + p[4] * size - p[3] * size, p[2], p[3] * size); x += p[2] + p[5] }
    else { ctx.fillStyle = p[2]; ctx.fillText(p[1], x, y); x += w(p[1]) + p[3] }
  }
  if ('letterSpacing' in ctx) ctx.letterSpacing = '0px'
}

function canvasBlob(id) {
  return new Promise((resolve) => document.getElementById(id).toBlob(resolve, 'image/png'))
}
function saveBlob(blob, name) {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = name
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(link.href), 5000)
}

function lbBlob() {
  return new Promise((resolve) => document.getElementById('lb').toBlob(resolve, 'image/png'))
}

function modal(html) {
  const el = document.createElement('div')
  el.className = 'modal'
  el.innerHTML = `<div class="modal-box">${html}</div>`
  document.body.appendChild(el)
  return el
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

async function busy(btn, fn) {
  if (btn) btn.disabled = true
  try { await fn() } catch (e) {
    const box = btn?.closest('form, .modal-box, .card') || document.querySelector('main')
    box?.querySelector('.msg.err')?.remove()
    box?.insertAdjacentHTML('beforeend', `<div class="msg err">${esc(errText(e))}</div>`)
  } finally { if (btn) btn.disabled = false }
}
const flash = (text, ok = true) => { state.flash = { text, ok } }

// ---------- events ----------
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
      if (error) throw new Error('Wrong username or password.')
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
      flash('Fund value saved.'); await Promise.all([loadPublic(), loadAdmin()]); return render()
    }
    if (kind === 'vault-config') {
      for (const a of [d.vault, d.usdt]) if (a && !/^0x[0-9a-fA-F]{40}$/.test(a.trim())) throw new Error('Check the addresses (0x followed by 40 characters)')
      await rpc('admin_set_vault', { p_network: d.network, p_vault: d.vault || null, p_usdt: d.usdt || null })
      flash('Saved. The site and the keeper now use this contract.'); await loadPublic(); return render()
    }
    if (kind === 'settings') {
      const { error } = await sb.from('settings').update({
        fund_name: d.fund_name, fee_pct: Number(d.fee_pct), total_slots: Number(d.total_slots),
        bot_wallet: d.bot_wallet || null, updated_at: new Date().toISOString(),
      }).eq('id', 1)
      if (error) throw error
      flash('Settings saved.'); await Promise.all([loadPublic(), loadAdmin()]); return render()
    }
  })
})

document.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-tab], [data-action]')
  if (!t) return
  if (t.dataset.tab) { ev.preventDefault(); location.hash = t.dataset.tab === 'overview' ? '' : t.dataset.tab; if (t.dataset.tab === 'overview') render(); return }
  const a = t.dataset.action
  if (a === 'logout') { ev.preventDefault(); sb.auth.signOut(); return }
  if (a === 'close-modal') { t.closest('.modal')?.remove(); return }
  if (a === 'scroll') { document.getElementById(t.dataset.to)?.scrollIntoView({ behavior: 'smooth' }); return }
  if (a === 'range') {
    state.range = t.dataset.range
    try { localStorage.setItem('range', state.range) } catch { /* optional */ }
    const y = window.scrollY; render(); window.scrollTo(0, y); return
  }
  if (a === 'copy') { navigator.clipboard?.writeText(t.dataset.value); t.textContent = 'Copied'; return }
  if (a === 'lb-caption') { state.lb.caption = (state.lb.caption + 1) % LB_CAPTIONS.length; drawLeaderboard(); return }
  if (a === 'lb-names') { state.lb.names = (state.lb.names + 1) % LB_NAMES.length; t.textContent = `Names: ${LB_NAMES[state.lb.names]}`; drawLeaderboard(); return }
  if (a === 'lb-download') { lbBlob().then((b) => saveBlob(b, 'midas-kiyoshi-leaderboard.png')); return }
  if (a === 'lb-share') {
    lbBlob().then((blob) => {
      const file = new File([blob], 'midas-kiyoshi-leaderboard.png', { type: 'image/png' })
      if (navigator.canShare?.({ files: [file] })) navigator.share({ files: [file], text: LB_CAPTIONS[state.lb.caption] }).catch(() => {})
    })
    return
  }
  busy(t, async () => {
    if (a === 'new-season') {
      const f = state.fund
      if (!state.trades || state.trades.error) await loadTrades()
      const T = state.trades && !state.trades.error ? state.trades : null
      const trades = T ? {
        count: T.trades.length, wins: T.trades.filter((x) => x.profit > 0).length,
        volume: Math.round(T.trades.reduce((s, x) => s + x.size, 0) * 2 * 100) / 100,
        profit: Math.round(T.trades.reduce((s, x) => s + x.profit, 0) * 1e6) / 1e6, since: T.since, until: T.now,
      } : null
      const live = await liveValue()
      const ret = (live / n(f.total_units) - 1) * 100
      if (!await ask(`End Season ${f.season} at ${pct(ret, true)} (fund value ${money(live)} now) and start Season ${f.season + 1} at $1.00? This cannot be undone.`)) return
      const r = await rpc('admin_start_season', { p_value: live, p_trades: trades })
      flash(`Season ${r.ended} closed at ${pct(r.return_pct, true)}. Season ${r.ended + 1} has started at $1.00 with ${money(r.carried_over)} carried over.`)
      await Promise.all([loadPublic(), loadAdmin()]); return render()
    }
  })
})

window.addEventListener('hashchange', render)

sb.auth.onAuthStateChange((event, session) => {
  const was = state.session?.user?.id
  state.session = session
  if ((session?.user?.id || null) !== (was || null)) loadAdmin().then(render)
})

// First paint, then keep the numbers fresh (without interrupting someone typing).
loadPublic().then(() => { render(); loadTrades() })
setInterval(async () => {
  const a = document.activeElement
  if (document.querySelector('.modal') || (a && ['INPUT', 'SELECT', 'TEXTAREA'].includes(a.tagName))) return
  await loadPublic()
  const y = window.scrollY; render(); window.scrollTo(0, y)
}, 120_000)
