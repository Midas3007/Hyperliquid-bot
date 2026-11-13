// Running costs: the pot that pays for gas and Render hosting (014_treasury.sql).
// Members see the pot, this month's movements and the log on the Dashboard (treasuryDashboard).
// The signed-in admin also gets the form to record a Render withdrawal on the Admin tab (treasuryAdmin).
// Nothing here moves money: the pot is an accounting line over USDT that stays in the pool wallet.
// Uses the helpers of app.js (esc, n, money, stamp, rpc, busy, ask, flash, render, state, errText).

let potShown = null // the level the pot was last drawn at, so it eases from there to the new level
let logOpen = false

const TX_URL = 'https://celoscan.io/tx/'

window.loadTreasury = async () => {
  try { state.treasury = await rpc('treasury_overview') } catch { state.treasury = state.treasury || null }
}
window.loadTreasuryAdmin = async () => {
  try { state.treasuryAdmin = await rpc('admin_treasury_admin') } catch { state.treasuryAdmin = null }
}

// ---------- the pot ----------
// A pot whose gold level eases to the new balance; the surface moves gently. Full = target_days of costs.
function wavePath(top, amp, period, width) {
  let d = `M0 ${top}`
  for (let x = 0; x < width; x += period) d += ` q${period / 4} ${-amp} ${period / 2} 0 t${period / 2} 0`
  return d + ` V200 H0 Z`
}
function potSvg(level) {
  const from = potShown == null ? 0 : potShown
  return `
  <svg class="pot" viewBox="0 0 160 176" role="img" aria-label="Pot filled to ${Math.round(level * 100)} percent" style="--lvl:${from}" data-pot-level="${level}">
    <defs>
      <clipPath id="potclip"><path d="M28 44 C28 34 38 30 48 30 H112 C122 30 132 34 132 44 V108 C132 144 110 164 80 164 C50 164 28 144 28 108 Z"/></clipPath>
      <linearGradient id="potgold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e4c56a"/><stop offset="1" stop-color="#9c7f33"/></linearGradient>
    </defs>
    <path class="pot-body" d="M28 44 C28 34 38 30 48 30 H112 C122 30 132 34 132 44 V108 C132 144 110 164 80 164 C50 164 28 144 28 108 Z"/>
    <g clip-path="url(#potclip)">
      <g class="pot-liquid">
        <g class="pot-wave back"><path d="${wavePath(52, 3, 80, 400)}" fill="#c9a84c" fill-opacity=".45"/></g>
        <g class="pot-wave"><path d="${wavePath(56, 3.5, 80, 400)}" fill="url(#potgold)"/></g>
      </g>
    </g>
    <rect class="pot-rim" x="20" y="22" width="120" height="10" rx="5"/>
    <path class="pot-shine" d="M42 52 V104" />
  </svg>`
}

function treasuryDashboard(T) {
  if (!T?.active) return ''
  const m = T.month || {}, tot = T.total || {}
  const lvl = T.level == null ? (n(T.balance) > 0 ? 1 : 0) : Math.max(0, Math.min(1, n(T.level)))
  const days = T.days_covered == null ? null : Math.round(n(T.days_covered))
  const covered = days == null ? 'No running costs measured yet' : days >= 1 ? `About ${days} day${days === 1 ? '' : 's'} of costs covered` : 'Less than a day of costs covered'
  const log = T.log || []
  const shown = logOpen ? log : log.slice(0, 12)
  const html = `
  <div class="section-title">Running costs</div>
  <div class="card hero treasury">
    <div class="pot-wrap">${potSvg(lvl)}</div>
    <div class="pot-info">
      <div class="label">The fund's running costs pot</div>
      <div class="big pos" id="pot-balance">${money(T.balance)}</div>
      <div class="sub">${covered}</div>
      <div class="sub muted">Full at ${T.target_days} days of costs (${money(T.target_balance)}). Costs now: about ${money(T.cost_per_day)} a day (gas ${money(T.gas_per_day)}, hosting ${money(T.render_per_day)}).</div>
    </div>
  </div>
  <div class="grid" style="margin-top:12px">
    ${stat(`Carved in · ${esc(m.label || 'this month')}`, money(m.carved), `${Math.round(n(T.carve_pct) * 100)}% of trading profit`, n(m.carved) > 0 ? 'pos' : '')}
    ${stat(`Gas bought · ${esc(m.label || 'this month')}`, money(m.gas), 'automatic USDT to CELO top-ups')}
    ${stat(`Render · ${esc(m.label || 'this month')}`, money(m.render), 'hosting, paid by the manager from the pot')}
  </div>
  <div class="card" style="margin-top:12px">
    <div class="label" style="margin-bottom:10px">Every movement of the pot</div>
    <div class="tlog">${shown.length ? shown.map(logRow).join('') : '<p class="note" style="margin:0">Nothing yet. The first carve happens at the next daily settlement.</p>'}</div>
    ${log.length > 12 ? `<div class="row" style="margin-top:12px"><button class="btn plain small" data-action="treasury-log">${logOpen ? 'Show fewer' : `Show all ${log.length}`}</button></div>` : ''}
    <p class="note" style="margin:14px 0 0">Once a day, ${Math.round(n(T.carve_pct) * 100)}% of the pool's trading profit is moved into this pot. It pays for the bot's gas (the automatic USDT to CELO top-ups) and for the Render hosting. The manager keeps 100% of his own fee; none of it goes here. The pot is not part of the share price, so paying from it never moves the value of your shares${n(tot.paid_by_members) > 0 ? `. While the pot was empty or short, ${money(tot.paid_by_members)} of costs came out of the shares' value, as gas always did before` : ''}. Pot movements and the pool wallet are public on the blockchain.</p>
  </div>`
  return html
}

const KIND = { carve: ['Carved in', 'gold'], gas: ['Gas', ''], render: ['Render', ''], sweep: ['Returned', ''] }
function logRow(l) {
  const amt = n(l.amount)
  const [label, cls] = l.kind === 'carve' && amt === 0 ? ['No carve', ''] : (KIND[l.kind] || [l.kind, ''])
  let text = ''
  if (l.kind === 'carve') text = amt > 0 ? `${l.profit != null ? `from ${money(l.profit)} of trading profit` : 'from trading profit'}` : esc(l.note || 'Nothing carved')
  else if (l.kind === 'gas') text = `Bought CELO for gas${l.celo ? ` · ${n(l.celo).toFixed(3)} CELO` : ''}`
  else text = esc(l.note || (l.kind === 'render' ? 'Render hosting' : ''))
  const short = n(l.uncovered) > 0 ? `<div class="sub">The pot was short: ${money(l.uncovered)} was paid by the shares' value</div>` : ''
  const sign = l.kind === 'carve' ? (amt > 0 ? '+' : '') : '−'
  const money_ = amt > 0 ? `${sign}${money(amt).replace('−', '')}` : '$0.00'
  const when = l.pending ? (l.paid_on ? day(l.paid_on) : stamp(l.t)) : stamp(l.t)
  return `<div class="trow ${l.pending ? 'pending' : ''}">
    <div class="tdate sub">${when}</div>
    <div class="tkind"><span class="pill ${cls}">${l.pending ? 'Waiting' : label}</span></div>
    <div class="tinfo"><div>${text}${l.pending ? ' · waiting for the transfer to show on the chain' : ''}</div>${short}</div>
    <div class="tamt ${l.kind === 'carve' && amt > 0 ? 'pos' : ''}">${money_}</div>
    <div class="tlink">${l.tx && /^0x[0-9a-f]{64}$/.test(l.tx) ? `<a href="${TX_URL}${l.tx}" target="_blank" rel="noopener" aria-label="View the transaction on Celoscan">View ↗</a>` : ''}</div>
  </div>`
}

// After each draw: ease the pot from where it was to where it is now.
window.treasuryMount = () => {
  const el = document.querySelector('svg.pot')
  if (!el) return
  const to = Number(el.dataset.potLevel)
  requestAnimationFrame(() => requestAnimationFrame(() => { el.style.setProperty('--lvl', String(to)); potShown = to }))
}

// ---------- admin ----------
function treasuryAdmin() {
  const A = state.treasuryAdmin, T = state.treasury
  if (!A) return ''
  if (!A.active) return `
  <div class="section-title">Running costs treasury</div>
  <div class="card" style="max-width:640px">
    <p class="note" style="margin-top:0">The treasury is not running. When you start it, the pot begins at $0: it only counts what happens from that moment (the season so far is not carved). From then on, once a day at the settlement, ${Math.round(n(A.carve_pct) * 100)}% of the trading profit moves into the pot, gas top-ups are paid from it, and you record your Render withdrawals here. Nothing is back-dated and no money moves.</p>
    <button class="btn" data-action="treasury-start">Start the treasury</button>
  </div>`
  const free = Math.max(0, n(A.balance) - n(A.committed))
  return `
  <div class="section-title">Running costs treasury</div>
  <div class="grid two">
    <form class="card" data-form="treasury-render">
      <div class="label">Withdraw from the treasury</div>
      <p class="note" style="margin:0">Between $5 and $10, once every 30 days, to any address. The bot sends it within a few minutes. It never changes the share price.</p>
      <label class="field"><span>Amount (USDT)</span><input name="amount" type="number" inputmode="decimal" step="0.01" min="5" max="10" placeholder="8.00" required></label>
      <label class="field"><span>Send to (address)</span><input name="dest" placeholder="0x…" autocapitalize="none" spellcheck="false" required></label>
      <label class="field"><span>Note</span><input name="note" maxlength="200" value="Render hosting" placeholder="Render hosting"></label>
      <button class="btn" type="submit">Withdraw</button>
      <div class="sub" data-free="${free}">The pot holds ${money(A.balance)}${n(A.committed) > 0 ? `, of which ${money(A.committed)} is already waiting for a transfer` : ''}.</div>
    </form>
    <div class="card">
      <div class="label">Waiting to be matched</div>
      ${A.pending?.length ? A.pending.map((p) => `<div class="row" style="justify-content:space-between;margin-top:10px"><span class="mono">${money(p.amount)} · ${day(p.paid_on)} · ${esc(p.note || '')}</span><button class="btn plain small" data-action="treasury-cancel" data-id="${p.id}">Cancel</button></div>`).join('') : '<p class="note">Nothing waiting.</p>'}
      <div class="label" style="margin-top:18px">Transfers out of the wallet not recorded as Render</div>
      ${A.unmatched?.length ? A.unmatched.map((u) => `<div class="mono sub" style="margin-top:8px">${stamp(u.t)} · ${money(u.amount)} · <a href="${TX_URL}${esc(u.hash)}" target="_blank" rel="noopener">View ↗</a></div>`).join('') : '<p class="note">None.</p>'}
      <p class="note" style="margin-bottom:0">If one of these was for Render, record it with the same amount and it is matched to it. Until a transfer is recorded or three hours pass, the keeper waits instead of posting a value that dips.</p>
      ${T?.excess > 0 ? `<div class="label" style="margin-top:18px">More than ${T.target_days} days of costs</div>
        <p class="note">${money(T.excess)} above the target. Returning it moves it back into the shares' value (a small step up on the chart).</p>
        <button class="btn plain small" data-action="treasury-sweep" data-amount="${T.excess}">Return ${money(T.excess)} to members</button>` : ''}
    </div>
  </div>`
}
window.treasuryDashboard = treasuryDashboard
window.treasuryAdmin = treasuryAdmin

// Live warning when the amount is more than the pot holds.
document.addEventListener('input', (ev) => {
  const form = ev.target.closest?.('form[data-form="treasury-render"]')
  if (!form) return
  const free = Number(form.querySelector('[data-free]')?.dataset.free || 0)
  const amount = Number(form.elements.amount.value || 0)
  const over = form.querySelector('[data-over]'), ok = form.querySelector('[data-over-ok]')
  const tooBig = amount > free + 0.000001
  over.style.display = ok.style.display = tooBig ? '' : 'none'
  if (!tooBig) form.elements.allow_over.checked = false
  over.textContent = tooBig ? `The pot holds only ${money(free)} free. ${money(amount - free)} of this would come out of the shares' value.` : ''
})

document.addEventListener('submit', (ev) => {
  const form = ev.target.closest('form[data-form="treasury-render"]')
  if (!form) return
  ev.preventDefault()
  const d = Object.fromEntries(new FormData(form))
  busy(form.querySelector('button[type="submit"]'), async () => {
    const amount = Math.round(Number(d.amount) * 100) / 100
    if (!(amount >= 5 && amount <= 10)) throw new Error('A withdrawal is between $5 and $10')
    const dest = String(d.dest || '').trim()
    if (!/^0x[0-9a-fA-F]{40}$/.test(dest)) throw new Error('The address is 0x followed by 40 characters')
    if (!await ask(`Send ${money(amount)} from the treasury to ${dest}?`)) return
    await rpc('admin_treasury_withdraw', { p_amount: amount, p_dest: dest, p_note: d.note || null })
    flash(`Requested. The bot sends ${money(amount)} within a few minutes; it shows in the log once the chain confirms it.`)
    await Promise.all([window.loadTreasury(), window.loadTreasuryAdmin()]); render()
  })
})

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-action^="treasury-"]')
  if (!t) return
  const a = t.dataset.action
  if (a === 'treasury-log') { logOpen = !logOpen; const y = window.scrollY; render(); window.scrollTo(0, y); return }
  const refresh = async () => { await Promise.all([window.loadTreasury(), window.loadTreasuryAdmin()]); render() }
  if (a === 'treasury-start') {
    if (!await ask('Start the running costs treasury? The pot starts at $0 and only counts what happens from now on. Nothing is back-dated and no money moves.')) return
    busy(t, async () => { await rpc('admin_treasury_start'); flash('The treasury is running. The first carve happens at the next daily settlement.'); await refresh() })
  } else if (a === 'treasury-cancel') {
    if (!await ask('Cancel this recorded withdrawal? Do this only if it was a mistake or the transfer was never made.')) return
    busy(t, async () => { await rpc('admin_treasury_cancel_render', { p_id: Number(t.dataset.id) }); flash('Cancelled.'); await refresh() })
  } else if (a === 'treasury-sweep') {
    const amount = Number(t.dataset.amount)
    if (!await ask(`Return ${money(amount)} from the pot to the members? It moves into the shares' value and cannot be undone.`)) return
    busy(t, async () => { await rpc('admin_treasury_sweep', { p_amount: amount }); flash(`${money(amount)} returned to the members.`); await refresh() })
  }
})
