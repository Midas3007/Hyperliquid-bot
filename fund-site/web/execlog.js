// Execution log on the dashboard: the pool wallet's recent trades, read from its own transactions on the chain
// (public.pool_log, 016_pool_log.sql). Collapsible; the open/closed choice is remembered on this device.
const XL = { rows: undefined, page: 0, open: (() => { try { return localStorage.getItem('execlog-open') === '1' } catch { return false } })() }
const XL_PER = 25
const XL_ROUTE = { 'uni>tex': 'Uniswap → Textile', 'tex>uni': 'Textile → Uniswap', 'uni>uni': 'Uniswap → Uniswap', 'tex>tex': 'Textile → Textile' }

async function loadExecLog() {
  try { const { data, error } = await sb.rpc('pool_log', { p_limit: 200 }); XL.rows = error ? null : data } catch { XL.rows = null }
  redrawExecLog()
}
function redrawExecLog() { const el = document.getElementById('exec-log'); if (el) el.outerHTML = execLogView() }

function execLogView() {
  const r = XL.rows, pages = r ? Math.max(1, Math.ceil(r.length / XL_PER)) : 1
  const p = Math.min(XL.page, pages - 1), list = r ? r.slice(p * XL_PER, (p + 1) * XL_PER) : []
  const tx = (h) => `<a href="https://celoscan.io/tx/${esc(h)}" target="_blank" rel="noopener">${esc(h.slice(0, 6))}…${esc(h.slice(-4))}</a>`
  const time = (t) => new Date(t).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
  let body
  if (r === undefined) body = '<tr><td colspan="5" class="muted">Reading…</td></tr>'
  else if (r === null) body = '<tr><td colspan="5" class="muted">The execution log is unavailable right now.</td></tr>'
  else if (!list.length) body = '<tr><td colspan="5" class="muted">No trades yet this season.</td></tr>'
  else body = list.map((x) => {
    const pr = Number(x.profit), inc = x.path === 'uni>uni'
    return `<tr><td>${esc(time(x.t))}</td><td>${esc(XL_ROUTE[x.path] ?? x.path)}</td><td class="num">${money(Number(x.size))}</td>`
      + `<td class="num" style="color:${pr > 0 ? 'var(--gold)' : 'inherit'}">${inc ? '<span class="muted">incomplete</span> ' : ''}${pr >= 0 ? '+' : '−'}$${Math.abs(pr).toFixed(2)}</td>`
      + `<td>${(x.txs || []).map(tx).join(' ')}</td></tr>`
  }).join('')
  const pager = r && pages > 1
    ? `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 18px;font-family:var(--mono);font-size:12px">`
      + `<button class="btn plain small" data-xl="-1" ${p === 0 ? 'disabled' : ''}>‹</button><span class="muted">Page ${p + 1} / ${pages}</span>`
      + `<button class="btn plain small" data-xl="1" ${p >= pages - 1 ? 'disabled' : ''}>›</button></div>`
    : ''
  return `<details id="exec-log" class="card" style="margin-top:12px;padding:0" ${XL.open ? 'open' : ''}>`
    + `<summary style="cursor:pointer;padding:14px 18px;font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase">Execution log${r ? ` · ${r.length} recent trades` : ''}</summary>`
    + `<div class="table-wrap" style="border:0;border-top:1px solid var(--line)"><table><thead><tr><th>Time</th><th>Route</th><th class="num">Size</th><th class="num">Profit</th><th>Transactions</th></tr></thead><tbody>${body}</tbody></table></div>`
    + pager
    + `<p class="note" style="margin:0;padding:0 18px 14px">Profit is USDT back minus USDT in, before gas. "Uniswap → Uniswap" is an incomplete trade sold back. Links open each transaction on Celoscan.</p>`
    + `</details>`
}

document.addEventListener('toggle', (e) => {
  if (e.target.id !== 'exec-log') return
  XL.open = e.target.open
  try { localStorage.setItem('execlog-open', XL.open ? '1' : '0') } catch {}
}, true)
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-xl]')
  if (!b) return
  XL.page = Math.max(0, XL.page + Number(b.dataset.xl))
  redrawExecLog()
})
