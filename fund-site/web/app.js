// Midas × Kiyoshi Fund — member and admin dashboards.
// All numbers come from database functions (fund_summary, my_position, admin_overview), so what a member
// can see is enforced by the database, not by this page.
const C = window.FUND_CONFIG
const sb = window.supabase.createClient(C.supabaseUrl, C.supabaseAnonKey)
const $app = document.getElementById('app')
const state = { session: null, me: null, fund: null, admin: null, wallet: null, preview: null, flash: null }

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

const LOGO = `<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
  <circle cx="16" cy="16" r="14.5" fill="none" stroke="#c9a84c" stroke-width="1.2"/>
  <path d="M9 22V10l7 8 7-8v12" fill="none" stroke="#e4c56a" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
</svg>`
const brand = (name) => {
  const [a, b] = String(name || 'Midas × Kiyoshi Fund').split('×')
  return `<div class="brand">${LOGO}<div class="brand-name">${esc(a)}${b !== undefined ? `<b>×</b>${esc(b)}` : ''}</div></div>`
}

// ---------- disclaimer ----------
const DISCLAIMER = `
<div class="disclaimer">
  <h3>Risk disclosure &amp; limitation of liability</h3>
  <ol>
    <li><b>You can lose all of your money.</b> The pool trades digital assets with an automated arbitrage bot on the Celo blockchain. Trading, software and blockchain risks can cause partial or total loss of the amount you contribute.</li>
    <li><b>No guarantee of profit.</b> Past or displayed results do not predict future returns. Opportunities can disappear for long periods, and competing traders can take them first.</li>
    <li><b>Technology risk.</b> Losses can result from bugs in the bot, smart-contract failures, stablecoin de-pegs, exchange or RFQ-venue failures, network outages, or the theft or loss of private keys. The bot software is open-source third-party code that has not been independently audited.</li>
    <li><b>Not a regulated product.</b> This pool is a private arrangement. It is not registered with or approved by any securities regulator, is not a bank deposit, and is not insured or protected by any compensation scheme. Nothing on this site is financial, investment, legal or tax advice.</li>
    <li><b>Figures shown.</b> The fund value is recorded by the manager and may lag the true on-chain position, especially while trades are open. The pool wallet's live balance is shown for transparency and may not equal the fund value at any moment.</li>
    <li><b>Fees.</b> The manager takes a performance fee (currently 10%) of profit above the capital you put in, charged when you withdraw and shown on your dashboard as it accrues. No fee is charged on losses. An early-exit penalty may apply if you withdraw within the lock period shown on your dashboard.</li>
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

async function load() {
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
  if (!state.me.disclaimer_accepted_at) showDisclaimerGate()
  loadWallet()
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
    ${brand(state.fund.fund_name)}
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
    ${stat('Fund value', money(f.current_value), 'Updated ' + stamp(f.last_updated), '', true)}
    ${stat('Unit price', '$' + n(f.unit_price).toFixed(4), `<span class="${tone(growth)}">${pct(growth, true)}</span> since start`)}
    ${stat('Fund P&amp;L', money(pnl, true), `${pct(n(f.capital) ? (pnl / n(f.capital)) * 100 : 0, true)} on ${money(f.capital)} invested`, tone(pnl))}
    ${stat('Members', `${f.members} / ${f.total_slots}`, `${Math.max(f.total_slots - f.members, 0)} slots open`)}
  </div>
  <div class="grid two" style="margin-top:12px">
    <div class="card">${chart(f.history)}</div>
    <div class="card">${walletCard()}</div>
  </div>

  <div class="section-title">Your position · ${esc(m.name)}</div>
  <div class="grid">
    ${stat('Your value', money(m.net_value), `after ${m.fee_exempt ? 'no' : pct(m.fee_pct)} manager fee`, '', true)}
    ${stat('Your profit', money(m.net_profit, true), `${pct(myRet, true)} on your capital`, tone(m.net_profit))}
    ${stat('Your share', pct(n(m.share) * 100), `${units(m.units)} units`)}
    ${stat('Capital invested', money(m.capital), `Deposited ${money(m.total_deposited)} · paid out ${money(m.total_withdrawn)}`)}
  </div>
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

function chart(history) {
  const pts = (history || []).map((h) => ({ t: new Date(h.t).getTime(), v: n(h.unit_price) }))
  if (pts.length < 2) return `<div class="label">Unit price</div><p class="note">The chart appears after the next fund value update.</p>`
  const W = 600, H = 200, pad = 6
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1
  let lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v))
  if (hi - lo < 0.002) { lo -= 0.001; hi += 0.001 }
  const x = (t) => pad + ((t - t0) / Math.max(t1 - t0, 1)) * (W - 2 * pad)
  const y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - 2 * pad)
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(t1).toFixed(1)},${H} L${x(t0).toFixed(1)},${H} Z`
  const last = pts[pts.length - 1].v
  return `
  <div class="chart-head"><div class="label">Unit price · value of $1 at start</div><div class="mono" style="font-size:13px">$${last.toFixed(4)}</div></div>
  <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Unit price history">
    <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#c9a84c" stop-opacity=".28"/><stop offset="1" stop-color="#c9a84c" stop-opacity="0"/></linearGradient></defs>
    ${lo < 1 && hi > 1 ? `<line x1="0" x2="${W}" y1="${y(1)}" y2="${y(1)}" stroke="#2a2a2e" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>` : ''}
    <path d="${area}" fill="url(#g)"/>
    <path d="${line}" fill="none" stroke="#e4c56a" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
  </svg>
  <div class="row sub" style="justify-content:space-between"><span>${day(new Date(t0).toISOString())}</span><span>low $${lo.toFixed(4)} · high $${hi.toFixed(4)}</span><span>${day(new Date(t1).toISOString())}</span></div>`
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
        <label class="field"><span>Current total fund value (USD)</span><input name="value" type="number" step="0.01" min="0" value="${n(f.current_value).toFixed(2)}" required></label>
        <label class="field"><span>Note (optional)</span><input name="note" placeholder="e.g. Daily update"></label>
        <button class="btn" type="submit">Save fund value</button>
        <p class="note" style="margin:0">Type the pool's total value, including money tied up in open trades. Every member's value and the unit price update from this.</p>
      </form>
    </div>
    <div class="card">
      <div class="label">Fees</div>
      <div class="big pos">${money(f.fees_accrued)}</div>
      <div class="sub">accrued on open positions (${pct(f.fee_pct)} of profit)</div><br>
      <div class="label">Collected</div>
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
        <label class="field"><span>Fund value just before this deposit</span><input name="before" type="number" step="0.01" min="0" value="${n(f.current_value).toFixed(2)}"></label>
        <label class="check"><input type="checkbox" name="founder"> Founder deposit (buys at $1.00, ignores the value above)</label>
        <button class="btn" type="submit">Record deposit</button>
        <p class="note" style="margin:0">Units bought = amount ÷ (fund value before ÷ total units). The fund value goes up by the deposit automatically.</p>
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
        <label class="field"><span>Fund value just before this withdrawal</span><input name="before" type="number" step="0.01" min="0.01" value="${n(f.current_value).toFixed(2)}" required></label>
        <button class="btn ghost" type="submit">Preview</button>
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
      <td><span class="pill ${r.kind === 'deposit' ? 'gold' : ''}">${r.kind}</span></td>
      <td class="num">${money(r.gross)}</td><td class="num">${r.kind === 'withdrawal' ? money(r.fee) : ''}</td>
      <td class="num">${r.kind === 'withdrawal' ? money(r.penalty) : ''}</td><td class="num">${r.kind === 'withdrawal' ? money(r.net_payout) : ''}</td>
      <td class="num">$${price(r.unit_price)}</td><td class="num">${units(r.units)}</td></tr>`).join('') || '<tr><td colspan="10" class="muted">No entries</td></tr>'}</tbody>
  </table></div>

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
  <p class="note">Send ${money(p.net_payout)} to the member and keep ${money(p.fee)} as your fee. Both leave the pool wallet.</p>`
}

function wireAdmin() {
  const all = document.querySelector('form[data-form="withdraw"] input[name="all"]')
  const amt = document.querySelector('form[data-form="withdraw"] input[name="amount"]')
  if (all) all.addEventListener('change', () => { amt.disabled = all.checked; if (all.checked) amt.value = '' })
  const founder = document.querySelector('form[data-form="deposit"] input[name="founder"]')
  const before = document.querySelector('form[data-form="deposit"] input[name="before"]')
  if (founder) founder.addEventListener('change', () => { before.disabled = founder.checked })
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
      flash('Fund value saved.'); return load()
    }
    if (kind === 'member') {
      if (!!d.username !== !!d.password) throw new Error('Give both a username and a password, or neither')
      const id = await rpc('admin_add_member', { p_name: d.name, p_fee_exempt: !!d.fee_exempt })
      if (d.username) await adminUsers({ action: 'create_login', member_id: id, username: d.username, password: d.password })
      flash(`${d.name} added${d.username ? ` with login "${d.username.toLowerCase()}"` : ''}. Now record their deposit.`); return load()
    }
    if (kind === 'deposit') {
      const r = await rpc('admin_add_deposit', {
        p_member: d.member, p_amount: Number(d.amount), p_on: d.date || null,
        p_value_before: d.founder ? null : Number(form.querySelector('[name="before"]').value),
      })
      flash(`Deposit recorded: ${units(r.units)} units at $${price(r.unit_price)}.`); return load()
    }
    if (kind === 'withdraw') {
      const args = { p_member: d.member, p_amount: d.all ? null : Number(d.amount), p_on: d.date || null, p_value_before: Number(d.before) }
      if (!d.all && !(Number(d.amount) > 0)) throw new Error('Enter an amount, or tick "Withdraw everything"')
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
  if (a === 'reload') return load()
  if (a === 'close-modal') return t.closest('.modal').remove()
  if (a === 'copy') { navigator.clipboard?.writeText(t.dataset.value); t.textContent = 'Copied'; return }
  if (a === 'login') return loginModal(t.dataset.id, t.dataset.name, false)
  if (a === 'reset') return loginModal(t.dataset.id, t.dataset.name, true)
  if (a === 'cancel-wd') { state.preview = null; document.getElementById('wd-preview').innerHTML = withdrawPreview(); return }
  busy(t, async () => {
    if (a === 'confirm-wd') {
      const p = state.preview
      const r = await rpc('admin_withdraw', p.args)
      state.preview = null
      flash(`Withdrawal saved. Send ${money(r.net_payout)} to ${r.member}; your fee is ${money(r.fee)}.`); return load()
    }
    if (a === 'unlink') {
      if (!await ask(`Remove ${t.dataset.name}'s login? Their money stays in the fund.`)) return
      await adminUsers({ action: 'remove_login', member_id: t.dataset.id })
      flash('Login removed.'); return load()
    }
    if (a === 'undo') {
      const last = state.admin.ledger[0]
      if (!await ask(`Undo the latest entry: ${last.kind} by ${last.member} for ${money(last.gross)}?`)) return
      await rpc('admin_undo_last'); flash('Latest entry undone.'); return load()
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
