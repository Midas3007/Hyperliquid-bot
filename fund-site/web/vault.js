// On-chain fund (FundVault on Celo): the wallet a member connects is their account.
// app.js renders containers <div data-vx="tiles|wallet|account|admin|connect"></div>; vRender() fills them.
// Reads go to a public Celo RPC; writes go through the wallet the page is opened in (MetaMask, Rabby, Valora,
// MiniPay, or any wallet that announces itself with EIP-6963).
const VNETS = {
  mainnet: { id: 42220, name: 'Celo', rpc: 'https://forno.celo.org', explorer: 'https://celoscan.io' },
  testnet: { id: 11142220, name: 'Celo Sepolia (testnet)', rpc: 'https://forno.celo-sepolia.celo-testnet.org', explorer: 'https://celo-sepolia.blockscout.com' },
  local: { id: 31337, name: 'Local test chain', rpc: 'http://127.0.0.1:8545', explorer: 'http://127.0.0.1' }, // development only
}
const CHANGE_NAMES = ['Owner', 'Operator (keeper)', 'Manager', 'Fee recipient', 'Fee', 'Daily value limit', 'Treasury']
const VAULT_ABI_SRC = [
  'function nav() view returns (uint256)', 'function totalSupply() view returns (uint256)', 'function sharePrice() view returns (uint256)',
  'function feeBps() view returns (uint256)', 'function maxNavChangeBps() view returns (uint256)', 'function depositCap() view returns (uint256)', 'function minDeposit() view returns (uint256)',
  'function paused() view returns (bool)', 'function ownerPaused() view returns (bool)', 'function manager() view returns (address)', 'function owner() view returns (address)',
  'function treasury() view returns (address)', 'function operator() view returns (address)', 'function feeRecipient() view returns (address)',
  'function settlements() view returns (uint256)', 'function navUpdatedAt() view returns (uint256)', 'function totalPendingDeposits() view returns (uint256)',
  'function queueLengths() view returns (uint256 deposits, uint256 redeems)', 'function pending(uint8) view returns (uint256 value, uint256 eta)',
  'function memberInfo(address) view returns (uint256 shares, uint256 escrowed, uint256 value, uint256 costBasis, uint256 feeIfRedeemed, uint256 queuedDeposit, uint256 unclaimed, bool isAllowed)',
  'function requestDeposit(uint256 assets)', 'function cancelDeposit()', 'function requestRedeem(uint256 shares)', 'function cancelRedeem()', 'function claim()',
  'function setAllowed(address member, bool ok)', 'function setLimits(uint256 depositCap, uint256 minDeposit)', 'function setPaused(bool p)',
  'function queueChange(uint8 c, uint256 value)', 'function cancelChange(uint8 c)', 'function executeChange(uint8 c)',
  'function balanceOf(address) view returns (uint256)', 'function allowance(address, address) view returns (uint256)', 'function approve(address, uint256) returns (bool)',
  'event DepositRequested(address indexed member, uint256 assets)', 'event DepositCancelled(address indexed member, uint256 assets)',
  'event RedeemRequested(address indexed member, uint256 shares)', 'event RedeemCancelled(address indexed member, uint256 shares)',
  'event Deposited(address indexed member, uint256 assets, uint256 shares, uint256 settlement)',
  'event Redeemed(address indexed member, uint256 shares, uint256 gross, uint256 fee, uint256 paid, uint256 settlement)',
  'event Claimed(address indexed member, uint256 assets)', 'event OpeningShares(address indexed to, uint256 assets, uint256 shares)',
  'event Allowed(address indexed member, bool allowed)',
]
const VS = { viem: null, abi: null, pub: null, chain: null, net: null, account: null, chainId: null, d: null, msg: null, loadErr: null, busy: false, logs: null, logsTo: 0n, times: {}, hidden: false }
const vcfg = () => (state.pub?.vault?.address ? state.pub.vault : window.FUND_CONFIG.vault || {})
const vnet = () => VNETS[vcfg().network] || VNETS.mainnet
const vOn = () => !!vcfg().address
const vUsd = (v) => Number(v) / 1e6
const vShares = (v) => (Number(v) / 1e18).toLocaleString('en-US', { maximumFractionDigits: 4 })
const vLink = (path, text) => `<a href="${vnet().explorer}/${path}" target="_blank" rel="noopener">${text}</a>`
const vMask = (a) => (a ? a.slice(0, 6) + '…' + a.slice(-4) : '')
const same = (a, b) => !!a && !!b && a.toLowerCase() === b.toLowerCase()

// ---------- wallet discovery ----------
const VW = { list: [] }
window.addEventListener('eip6963:announceProvider', (e) => {
  if (!VW.list.some((x) => x.info?.uuid === e.detail?.info?.uuid)) VW.list.push(e.detail)
  vRender()
})
window.dispatchEvent(new Event('eip6963:requestProvider'))
const vProv = () => window.ethereum || VW.list[0]?.provider || null

async function vInit() {
  if (!VS.viem) {
    VS.viem = await import('https://cdn.jsdelivr.net/npm/viem@2.21.60/+esm')
    VS.abi = VS.viem.parseAbi(VAULT_ABI_SRC)
    await new Promise((r) => setTimeout(r, 300)) // let wallets announce themselves
    const prov = vProv()
    if (prov) {
      prov.on?.('accountsChanged', (a) => { VS.account = a[0] || null; VS.hidden = false; VS.d = null; vRefresh() })
      prov.on?.('chainChanged', (c) => { VS.chainId = Number(c); vRefresh() })
      try {
        let off = false
        try { off = !!localStorage.getItem('vault-disconnected') } catch { /* optional */ }
        if (!off) VS.account = (await prov.request({ method: 'eth_accounts' }))[0] || null
        VS.chainId = Number(await prov.request({ method: 'eth_chainId' }))
      } catch { /* not connected yet */ }
    }
  }
  const n = vnet()
  if (VS.net !== n) { // (re)build the client when the configured network changes
    VS.net = n
    VS.chain = VS.viem.defineChain({ id: n.id, name: n.name, nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 }, rpcUrls: { default: { http: [n.rpc] } } })
    VS.pub = VS.viem.createPublicClient({ chain: VS.chain, transport: VS.viem.http(n.rpc) })
    VS.logs = null
  }
}

async function vLoad() {
  const c = vcfg()
  const r = (fn, args = [], address = c.address) => VS.pub.readContract({ address, abi: VS.abi, functionName: fn, args })
  const names = ['nav', 'totalSupply', 'sharePrice', 'feeBps', 'maxNavChangeBps', 'depositCap', 'minDeposit', 'paused', 'ownerPaused', 'manager', 'owner', 'treasury',
    'operator', 'feeRecipient', 'settlements', 'navUpdatedAt', 'totalPendingDeposits', 'queueLengths']
  const vals = await Promise.all(names.map((n) => r(n)))
  const d = Object.fromEntries(names.map((n, i) => [n, vals[i]]))
  d.changes = await Promise.all(CHANGE_NAMES.map((_, i) => r('pending', [i])))
  if (VS.account) {
    const [info, bal, allow] = await Promise.all([r('memberInfo', [VS.account]), r('balanceOf', [VS.account], c.usdt), r('allowance', [VS.account, c.address], c.usdt)])
    d.me = { shares: info[0], escrowed: info[1], value: info[2], cost: info[3], fee: info[4], queued: info[5], unclaimed: info[6], allowed: info[7], usdt: bal, allowance: allow }
    d.isManager = same(VS.account, d.manager)
    d.isOwner = same(VS.account, d.owner)
  }
  VS.d = d
}

// Every event of the vault, fetched once and then incrementally (newest windows first, until the opening).
async function vLogs() {
  const c = vcfg(), latest = await VS.pub.getBlockNumber()
  const get = (fromBlock, toBlock) => VS.pub.getLogs({ address: c.address, fromBlock, toBlock })
  const parse = (logs) => VS.viem.parseEventLogs({ abi: VS.abi, logs, strict: false })
  if (!VS.logs) {
    let all = []
    try { all = await get(0n, latest) } catch {
      const W = 50_000n
      for (let to = latest, i = 0; i < 60 && to > 0n; i++, to -= W) {
        const from = to > W ? to - W + 1n : 0n
        const part = await get(from, to)
        all = part.concat(all)
        if (parse(part).some((l) => l.eventName === 'OpeningShares')) break
      }
    }
    VS.logs = parse(all)
  } else if (latest > VS.logsTo) {
    VS.logs = VS.logs.concat(parse(await get(VS.logsTo + 1n, latest)))
  }
  VS.logsTo = latest
  return VS.logs
}
async function vTime(block) {
  const k = String(block)
  if (!VS.times[k]) VS.times[k] = Number((await VS.pub.getBlock({ blockNumber: block })).timestamp) * 1000
  return VS.times[k]
}

// ---------- views ----------
function vNoWallet() {
  const url = location.origin + location.pathname, host = location.host + location.pathname, enc = encodeURIComponent(url)
  return `<div class="card"><div class="label">Connect a wallet</div>
    <p class="note">This browser has no wallet. On a phone, open this page inside your wallet app (its built-in browser), then tap Connect:</p>
    <div class="row">
      <a class="btn small" href="https://metamask.app.link/dapp/${host}">Open in MetaMask</a>
      <a class="btn ghost small" href="https://link.trustwallet.com/open_url?coin_id=52752&url=${enc}">Trust Wallet</a>
      <a class="btn ghost small" href="https://go.cb-w.com/dapp?cb_url=${enc}">Coinbase Wallet</a>
      <button class="btn plain small" data-action="copy" data-value="${esc(url)}">Copy link</button>
    </div>
    <p class="note" style="margin-bottom:0">Rabby, Valora or MiniPay: open the app, go to its browser (Dapps / Discover) and paste the link. On a computer, install the MetaMask or Rabby extension.</p></div>`
}
// The wallet's state if it is not ready to use the fund, else null.
function vGate(what = 'to deposit USDT, see your shares and redeem') {
  if (!vProv()) return vNoWallet()
  if (!VS.account || VS.hidden) return `<div class="card"><div class="label">Your wallet</div><p class="note">Connect ${what}.</p><button class="btn" data-vault="connect">Connect wallet</button></div>`
  if (VS.chainId !== vnet().id) return `<div class="card"><div class="label">Your wallet · ${esc(vMask(VS.account))}</div><p class="note">Your wallet is on another network.</p><button class="btn" data-vault="switch">Switch to ${vnet().name}</button></div>`
  if (!VS.d?.me) return `<div class="card"><div class="sub">${VS.loadErr ? esc(VS.loadErr) + ' <button class="btn plain small" data-vault="retry">Try again</button>' : 'Reading the fund contract…'}</div></div>`
  return null
}
const vNotice = () => (VS.msg ? `<div class="msg ${VS.msg.startsWith('Done') ? 'ok' : 'err'}" style="margin-bottom:12px">${esc(VS.msg)}</div>` : '')
function vNextSettle() {
  const t = new Date(); t.setUTCHours(20, 7, 0, 0)
  if (t < new Date()) t.setUTCDate(t.getUTCDate() + 1)
  return t.toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
}

function vTiles() {
  const d = VS.d
  if (!d) return `<div class="card"><div class="sub">${VS.loadErr ? esc(VS.loadErr) + ' <button class="btn plain small" data-vault="retry">Try again</button>' : 'Reading the fund contract…'}</div></div>`
  const price = vUsd(d.sharePrice)
  return `<div class="grid">
    ${stat('Share price', '$' + price.toFixed(4), `<span class="${tone(price - 1)}">${pct((price - 1) * 100, true)}</span> since the vault opened · ${(Number(d.feeBps) / 100).toFixed(0)}% fee on profit`, '', true)}
    ${stat('Fund value', money(vUsd(d.nav)), d.navUpdatedAt ? `updated ${stamp(new Date(Number(d.navUpdatedAt) * 1000).toISOString())}` : 'not reported yet')}
    ${stat('Next settlement', vNextSettle(), `${d.queueLengths[0]} deposit(s), ${d.queueLengths[1]} redemption(s) waiting`)}
    ${stat('Deposits', d.paused ? 'Paused' : d.depositCap > 0n ? 'Open' : 'Closed', `cap ${money(vUsd(d.depositCap))} · minimum ${money(vUsd(d.minDeposit))} · ${vLink('address/' + vcfg().address, 'contract')}`)}
  </div>`
}

function vWallet() {
  const g = vGate()
  if (g) return g
  const d = VS.d, me = d.me
  const after = vUsd(me.value) - vUsd(me.fee)
  const profit = Math.round((after - vUsd(me.cost)) * 100) / 100 || 0
  return `${vNotice()}<div class="grid two">
    <div class="card hero"><div class="label">Your position · ${vLink('address/' + VS.account, esc(vMask(VS.account)))}</div>
      <div class="big">${money(after)}</div>
      <div class="sub"><span class="${tone(profit)}">${money(profit, true)}</span> after the ${(Number(d.feeBps) / 100).toFixed(0)}% fee · ${vShares(me.shares + me.escrowed)} shares</div>
      <div class="preview" style="margin-top:12px;border-style:solid;border-color:var(--line)">
        <div><span>Value before fee</span><span>${money(vUsd(me.value))}</span></div>
        <div><span>You put in</span><span>${money(vUsd(me.cost))}</span></div>
        <div><span>Fee if you redeemed today</span><span>${money(vUsd(me.fee))}</span></div>
        ${me.queued ? `<div><span>Deposit waiting</span><span>${money(vUsd(me.queued))} <button class="btn plain small" data-vault="cancel-deposit">Cancel</button></span></div>` : ''}
        ${me.escrowed ? `<div><span>Redemption waiting</span><span>${vShares(me.escrowed)} shares <button class="btn plain small" data-vault="cancel-redeem">Cancel</button></span></div>` : ''}
        ${me.unclaimed ? `<div class="total"><span>Ready to collect</span><span>${money(vUsd(me.unclaimed))} <button class="btn small" data-vault="claim">Collect</button></span></div>` : ''}
      </div>
    </div>
    <div class="card">
      <form data-vault-form="deposit">
        <label class="field"><span>Deposit USDT · you have ${money(vUsd(me.usdt))}</span><input name="amount" type="number" step="0.01" min="0" placeholder="e.g. 50" ${me.allowed ? '' : 'disabled'}></label>
        ${me.allowed ? `<label class="check"><input type="checkbox" name="ok" required> I accept the risk disclosure at the bottom of this page, including the possible loss of everything I deposit.</label>
          <button class="btn" type="submit" ${VS.busy || d.paused ? 'disabled' : ''}>Deposit</button>`
          : `<p class="note" style="margin:0">This wallet is not on the members list yet. Send the fund manager your address: <span class="mono">${esc(vMask(VS.account))}</span> <button class="btn plain small" type="button" data-action="copy" data-value="${esc(VS.account)}">Copy</button></p>`}
      </form>
      <form data-vault-form="redeem" style="margin-top:14px">
        <label class="field"><span>Redeem (USD value)</span><input name="amount" type="number" step="0.01" min="0" placeholder="e.g. 20" ${me.shares ? '' : 'disabled'}></label>
        <label class="check"><input type="checkbox" name="all" ${me.shares ? '' : 'disabled'}> Everything</label>
        <button class="btn ghost" type="submit" ${VS.busy || !me.shares || d.paused ? 'disabled' : ''}>Redeem</button>
      </form>
      <p class="note" style="margin-bottom:0">Deposits and redemptions settle automatically once a day (next: ${vNextSettle()}) at that day's share price; you can cancel until then. Redemptions are paid in USDT to this wallet, after the fee on your profit.</p>
    </div>
  </div>`
}

// Account: the wallet, its history and the P&L card.
function vAccount() {
  const g = vGate('to see your account')
  if (g) return g
  const rows = VS.history
  return `${vNotice()}<div class="grid two">
    <div class="card"><div class="label">Wallet</div>
      <div class="addr">${esc(VS.account)}</div>
      <div class="row"><button class="btn plain small" data-action="copy" data-value="${esc(VS.account)}">Copy</button>
        ${vLink('address/' + VS.account, 'View on explorer ↗')}
        <button class="btn plain small" data-vault="disconnect">Disconnect</button></div>
      <p class="note">Network: ${esc(vnet().name)}. ${VS.d.me.allowed ? 'This wallet is on the members list.' : 'This wallet is not on the members list yet.'}</p>
      <div class="label" style="margin-top:16px">Your history</div>
      <div class="table-wrap" style="margin-top:10px;border:0"><table><thead><tr><th>When</th><th>What</th><th class="num">Amount</th></tr></thead><tbody>
      ${rows === undefined ? '<tr><td colspan="3" class="muted">Reading…</td></tr>' : rows.length ? rows.map((r) => `<tr><td>${r.t ? stamp(new Date(r.t).toISOString()) : '—'}</td>
        <td>${vLink('tx/' + r.tx, esc(r.what))}</td><td class="num">${esc(r.amount)}</td></tr>`).join('') : '<tr><td colspan="3" class="muted">Nothing yet</td></tr>'}
      </tbody></table></div>
    </div>
    <div class="card"><div class="label">Your P&amp;L card</div>
      <canvas id="vault-card" width="1080" height="1350" style="width:100%;max-width:360px;height:auto;display:block;margin:12px auto 0;border:1px solid var(--line)" role="img" aria-label="Your P&L card"></canvas>
      <div class="row" style="justify-content:center;margin-top:12px">
        <button class="btn" data-vault="card-download">Download</button>
        ${navigator.canShare ? '<button class="btn ghost" data-vault="card-share">Share</button>' : ''}
        <button class="btn plain small" data-vault="card-amounts">${state.card.amounts ? 'Hide' : 'Show'} amounts</button>
        <button class="btn plain small" data-vault="card-name">Wallet: ${state.card.name ? 'shown' : 'hidden'}</button>
      </div>
    </div>
  </div>`
}

// Admin, on-chain: what the manager (personal wallet) and the owner (bot wallet) can do from the site.
function vAdmin() {
  const g = vGate('your personal (manager) wallet to pause, approve wallets and change limits')
  if (g) return g
  const d = VS.d, k = state.pub?.vault || {}
  if (!d.isManager && !d.isOwner) return `<div class="card"><p class="note" style="margin:0">The connected wallet ${esc(vMask(VS.account))} is not the fund manager. Connect your personal wallet (${esc(vMask(d.manager))}).</p></div>`
  const queued = d.changes.map((p, i) => ({ i, value: p[0], eta: Number(p[1]) })).filter((p) => p.eta)
  const showVal = (i, v) => (i === 4 || i === 5 ? `${Number(v) / 100}%` : vMask('0x' + v.toString(16).padStart(40, '0')))
  const holders = VS.holders
  return `${vNotice()}
  <div class="grid two">
    <div class="card ${d.paused ? 'hero' : ''}"><div class="label">Emergency pause</div>
      <div class="big">${d.paused ? 'Paused' : 'Running'}</div>
      <div class="sub">${d.paused ? (d.ownerPaused ? 'Paused by the bot wallet: only it can unpause (npm run vault -- unpause).' : 'Deposits, redemptions and settlements are stopped. Cancels and collects still work.') : 'Stops deposits, redemptions and settlements at once. It cannot move anyone\'s money.'}</div>
      <div class="row" style="margin-top:12px">${d.paused ? (!d.ownerPaused || d.isOwner ? '<button class="btn" data-vault="unpause">Unpause</button>' : '') : '<button class="btn" data-vault="pause">Pause the fund</button>'}</div>
    </div>
    <div class="card"><div class="label">Automatic settlement (keeper)</div>
      <p class="note">Posts the value every hour and settles once a day after 20:00 UTC. It signs with its own wallet, which holds no fund money.</p>
      <div class="preview" style="border-style:solid;border-color:var(--line)">
        <div><span>Keeper wallet</span><span>${k.keeper ? `${vLink('address/' + k.keeper, esc(vMask(k.keeper)))} <button class="btn plain small" data-action="copy" data-value="${esc(k.keeper)}">Copy</button>` : 'not created yet'}</span></div>
        <div><span>Is the vault's operator</span><span>${k.keeper ? (same(k.keeper, d.operator) ? 'yes' : `no: the operator is ${esc(vMask(d.operator))}`) : '—'}</span></div>
        <div><span>Last run</span><span>${k.last_run_at ? stamp(k.last_run_at) : 'never'}</span></div>
      </div>
      ${k.last_result ? `<p class="note">${esc(k.last_result)}</p>` : ''}
      <div class="row" style="margin-top:12px"><button class="btn" data-vault="settle-now" ${VS.busy ? 'disabled' : ''}>Settle now</button></div>
      <p class="note" style="margin-bottom:0">Settles the ${d.queueLengths[0]} waiting deposit(s) and ${d.queueLengths[1]} redemption(s) now instead of at 20:07 UTC. Use it only when the bot is not in a trade.</p>
    </div>
  </div>
  <div class="grid two" style="margin-top:12px">
    <form class="card" data-vault-form="allow"><div class="label">Approve a wallet</div>
      <label class="field" style="margin-top:10px"><span>Wallet address</span><input name="who" placeholder="0x…" autocapitalize="none" spellcheck="false"></label>
      <div class="row"><button class="btn" type="submit" name="op" value="allow">Approve</button><button class="btn plain" type="submit" name="op" value="remove">Remove</button></div>
      <p class="note" style="margin:0">Approved wallets can deposit. Removed wallets can still redeem what they hold.</p></form>
    <form class="card" data-vault-form="limits"><div class="label">Deposit limits</div>
      <label class="field" style="margin-top:10px"><span>Cap: total fund value (USDT)</span><input name="cap" type="number" step="1" value="${vUsd(d.depositCap)}"></label>
      <label class="field"><span>Minimum deposit (USDT)</span><input name="min" type="number" step="0.01" value="${vUsd(d.minDeposit)}"></label>
      <button class="btn" type="submit">Save limits</button></form>
  </div>
  <div class="grid two" style="margin-top:12px">
    <form class="card" data-vault-form="fee"><div class="label">Performance fee · now ${Number(d.feeBps) / 100}%</div>
      <label class="field" style="margin-top:10px"><span>New fee (% of profit, at most 20)</span><input name="pct" type="number" step="0.5" min="0" max="20" value="${Number(d.feeBps) / 100}"></label>
      <button class="btn" type="submit" ${d.isManager ? '' : 'disabled'}>Queue fee change</button>
      <p class="note" style="margin:0">Takes effect 48 hours after you queue it, so members can leave first. Shown to members while it waits.</p></form>
    <div class="card"><div class="label">Queued changes</div>
      ${queued.length ? `<div class="table-wrap" style="margin-top:10px;border:0"><table><tbody>${queued.map((p) => `<tr><td>${CHANGE_NAMES[p.i]}</td><td>${showVal(p.i, p.value)}</td>
        <td class="num">${p.eta * 1000 > Date.now() ? `ready ${stamp(new Date(p.eta * 1000).toISOString())}` : `<button class="btn small" data-vault="exec" data-i="${p.i}">Apply</button>`}
        ${d.isOwner || (d.isManager && p.i === 4) ? `<button class="btn plain small" data-vault="cancel-change" data-i="${p.i}">Cancel</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="note">None. Role changes are queued from the bot wallet in Termux (npm run vault -- set-manager …).</p>'}
    </div>
  </div>
  <div class="section-title">Holders</div>
  <div class="table-wrap"><table><thead><tr><th>Wallet</th><th class="num">Shares</th><th class="num">Value</th><th class="num">Put in</th><th class="num">Waiting</th><th>Approved</th><th></th></tr></thead><tbody>
  ${holders === undefined ? '<tr><td colspan="7" class="muted">Reading…</td></tr>' : holders.length ? holders.map((h) => `<tr><td>${vLink('address/' + h.a, esc(vMask(h.a)))}${same(h.a, d.feeRecipient) ? ' <span class="pill gold">you</span>' : ''}</td>
    <td class="num">${vShares(h.shares + h.escrowed)}</td><td class="num">${money(vUsd(h.value))}</td><td class="num">${money(vUsd(h.cost))}</td>
    <td class="num">${h.queued ? money(vUsd(h.queued)) + ' in' : ''}${h.escrowed ? ` ${vShares(h.escrowed)} out` : ''}</td><td>${h.allowed ? 'yes' : 'no'}</td>
    <td class="num"><button class="btn plain small" data-vault="${h.allowed ? 'remove' : 'approve'}" data-who="${h.a}">${h.allowed ? 'Remove' : 'Approve'}</button></td></tr>`).join('') : '<tr><td colspan="7" class="muted">No wallets yet</td></tr>'}
  </tbody></table></div>`
}

// Leaderboard: every holder's return after fees on what they put in (the manager is not ranked).
function vBoard() {
  if (!VS.d || VS.holders === undefined) return '<p class="note">Reading the fund contract…</p>'
  const rows = VS.holders.filter((h) => h.shares + h.escrowed > 0n && h.cost > 0n && !same(h.a, VS.d.feeRecipient))
    .map((h) => ({ label: vMask(h.a), roi: ((vUsd(h.value) - vUsd(h.fee) - vUsd(h.cost)) / vUsd(h.cost)) * 100, active: true, me: same(h.a, VS.account) }))
    .sort((x, y) => y.roi - x.roi)
  return boardView(rows, 'No members yet this season. Be the first.')
}

function vRender() {
  const fill = { tiles: vTiles, wallet: vWallet, account: vAccount, admin: vAdmin, connect: vConnectButton, board: vBoard }
  for (const el of document.querySelectorAll('[data-vx]')) {
    if (!vOn() && el.dataset.vx !== 'connect') { el.innerHTML = el.dataset.vx === 'wallet' ? '<div class="card"><p class="note" style="margin:0">The on-chain fund opens soon.</p></div>' : ''; continue }
    if (fill[el.dataset.vx]) el.innerHTML = fill[el.dataset.vx]()
  }
  if (document.getElementById('vault-card')) vDrawCard()
}
// The header's wallet button.
function vConnectButton() {
  if (!vOn()) return ''
  if (VS.account && !VS.hidden) return `<a class="btn plain small" href="#account">${esc(vMask(VS.account))}</a>`
  return `<button class="btn small" data-vault="connect">Connect wallet</button>`
}

async function vRefresh() {
  if (!vOn()) return vRender()
  try { await vInit(); await vLoad(); VS.loadErr = null } catch (e) {
    VS.loadErr = `The fund contract could not be read right now (${String(e?.shortMessage || e?.message || e).split('\n')[0].slice(0, 120)}).`
  }
  vRender()
  window.onVaultExtras?.()
  vExtras()
}

// History, holders and the personal P&L, from the vault's events (only when a view needs them).
async function vExtras() {
  const needHistory = !!document.querySelector('[data-vx="account"]') && VS.account && VS.d
  const needHolders = (!!document.querySelector('[data-vx="admin"]') && VS.d && (VS.d.isManager || VS.d.isOwner)) || (!!document.querySelector('[data-vx="board"]') && VS.d)
  const needMine = !!document.getElementById('my-pnl') && VS.account && VS.d
  if (!needHistory && !needHolders && !needMine) return
  try {
    const logs = await vLogs()
    const mine = VS.account ? logs.filter((l) => same(l.args?.member || l.args?.to, VS.account)) : []
    await Promise.all(mine.slice(-60).map((l) => vTime(l.blockNumber).catch(() => 0)))
    VS.myLogs = mine
    if (needHistory) {
      const label = { DepositRequested: 'Deposit queued', DepositCancelled: 'Deposit cancelled', RedeemRequested: 'Redemption queued', RedeemCancelled: 'Redemption cancelled', Deposited: 'Deposit settled', Redeemed: 'Redemption paid', Claimed: 'Payout collected', OpeningShares: 'Opening shares', Allowed: 'Members list' }
      const amt = (l) => {
        const a = l.args
        if (l.eventName === 'Deposited' || l.eventName === 'OpeningShares') return `${money(vUsd(a.assets))} → ${vShares(a.shares)} sh`
        if (l.eventName === 'Redeemed') return `${vShares(a.shares)} sh → ${money(vUsd(a.paid))}${a.fee ? ` (fee ${money(vUsd(a.fee))})` : ''}`
        if (l.eventName === 'RedeemRequested' || l.eventName === 'RedeemCancelled') return `${vShares(a.shares)} sh`
        if (l.eventName === 'Allowed') return a.allowed ? 'approved' : 'removed'
        return money(vUsd(a.assets))
      }
      VS.history = mine.slice(-60).reverse().map((l) => ({ t: VS.times[String(l.blockNumber)] || 0, tx: l.transactionHash, what: label[l.eventName] || l.eventName, amount: amt(l) }))
    }
    if (needHolders) {
      const addrs = [...new Set(logs.map((l) => l.args?.member || l.args?.to).filter(Boolean).map((a) => a.toLowerCase()))]
      const info = await Promise.all(addrs.map((a) => VS.pub.readContract({ address: vcfg().address, abi: VS.abi, functionName: 'memberInfo', args: [a] })))
      VS.holders = addrs.map((a, i) => ({ a: VS.viem.getAddress(a), shares: info[i][0], escrowed: info[i][1], value: info[i][2], cost: info[i][3], fee: info[i][4], queued: info[i][5], allowed: info[i][7] }))
        .sort((x, y) => (y.value > x.value ? 1 : y.value < x.value ? -1 : 0))
    }
  } catch { VS.history = VS.history || []; VS.holders = VS.holders || [] }
  vRender()
  window.onVaultExtras?.()
}

// Personal P&L items for the bar chart: the wallet's shares at each value update times the change in price.
window.vaultPersonalItems = () => {
  const hist = (state.pub?.vault_history || []).map((h) => ({ t: new Date(h.t).getTime(), price: n(h.unit_price) })).sort((a, b) => a.t - b.t)
  if (!VS.myLogs || hist.length < 2) return null
  const moves = VS.myLogs.map((l) => ({
    t: VS.times[String(l.blockNumber)] || 0,
    s: l.eventName === 'Deposited' || l.eventName === 'OpeningShares' ? Number(l.args.shares) / 1e18 : l.eventName === 'Redeemed' ? -Number(l.args.shares) / 1e18 : 0,
  })).filter((m) => m.s)
  const sharesAt = (t) => moves.reduce((s, m) => (m.t && m.t <= t ? s + m.s : s), 0)
  const items = []
  for (let i = 1; i < hist.length; i++) items.push({ t: hist[i].t, p: sharesAt(hist[i - 1].t) * (hist[i].price - hist[i - 1].price), n: 0 })
  return items
}

// ---------- P&L card (on-chain) ----------
async function vDrawCard() {
  const cv = document.getElementById('vault-card')
  if (!cv || !VS.d?.me) return
  try { await Promise.all([document.fonts.load('400 46px "JetBrains Mono"'), document.fonts.load('700 150px "JetBrains Mono"')]) } catch { /* fallback fonts */ }
  const ctx = cv.getContext('2d'), W = 1080, H = 1350, d = VS.d, me = d.me
  const GOLD = '#c9a84c', GOLD_HI = '#e4c56a', WHITE = '#f5f5f2', GREY = '#8d8d94', LINE = '#2a2a2e', MONO = '"JetBrains Mono", ui-monospace, monospace'
  const spaced = (px) => { if ('letterSpacing' in ctx) ctx.letterSpacing = px }
  const after = vUsd(me.value) - vUsd(me.fee), cost = vUsd(me.cost)
  const roi = cost > 0 ? ((after - cost) / cost) * 100 : 0
  ctx.fillStyle = '#0a0a0b'; ctx.fillRect(0, 0, W, H)
  const glow = ctx.createRadialGradient(W / 2, 0, 40, W / 2, 0, 950)
  glow.addColorStop(0, 'rgba(201,168,76,0.24)'); glow.addColorStop(1, 'rgba(201,168,76,0)')
  ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H)
  ctx.strokeStyle = GOLD; ctx.lineWidth = 3; ctx.strokeRect(36, 36, W - 72, H - 72)
  ctx.strokeStyle = 'rgba(201,168,76,0.35)'; ctx.lineWidth = 1; ctx.strokeRect(50, 50, W - 100, H - 100)
  await drawWordmark(ctx, W / 2, 175, 50)
  ctx.textAlign = 'center'; spaced('8px'); ctx.fillStyle = GREY; ctx.font = `500 24px ${MONO}`
  ctx.fillText('P&L CARD · ON-CHAIN FUND', W / 2, 250)
  if (state.card.name) { spaced('2px'); ctx.fillStyle = WHITE; ctx.font = `500 40px ${MONO}`; ctx.fillText(vMask(VS.account), W / 2, 340) }
  spaced('0px'); ctx.fillStyle = roi >= 0 ? GOLD_HI : WHITE; ctx.font = `700 150px ${MONO}`
  ctx.fillText(pct(roi, true), W / 2, 530)
  spaced('5px'); ctx.fillStyle = GREY; ctx.font = `500 26px ${MONO}`; ctx.fillText('RETURN AFTER FEES', W / 2, 585)
  const rows = []
  if (state.card.amounts) rows.push(['PROFIT', money(after - cost, true), after >= cost ? GOLD_HI : WHITE], ['VALUE', money(after), WHITE])
  const fr = (vUsd(d.sharePrice) - 1) * 100
  rows.push(['FUND SINCE OPENING', pct(fr, true), fr >= 0 ? GOLD_HI : WHITE], ['SHARE PRICE', '$' + vUsd(d.sharePrice).toFixed(4), WHITE])
  ctx.strokeStyle = LINE; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(140, 640); ctx.lineTo(W - 140, 640); ctx.stroke()
  rows.forEach(([k, v, c], i) => {
    const y = 705 + i * 62
    spaced('4px'); ctx.textAlign = 'left'; ctx.fillStyle = GREY; ctx.font = `500 24px ${MONO}`; ctx.fillText(k, 140, y)
    spaced('0px'); ctx.textAlign = 'right'; ctx.fillStyle = c; ctx.font = `700 34px ${MONO}`; ctx.fillText(v, W - 140, y)
  })
  const pts = (state.pub?.vault_history || []).map((h) => ({ t: new Date(h.t).getTime(), v: n(h.unit_price) }))
  const top = 705 + rows.length * 62 + 10, bot = 1170
  if (pts.length > 1) {
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1
    let lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v)); if (hi - lo < 0.002) { lo -= 0.001; hi += 0.001 }
    const X = (t) => 140 + ((t - t0) / Math.max(t1 - t0, 1)) * (W - 280), Y = (v) => bot - ((v - lo) / (hi - lo)) * (bot - top - 20)
    ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(X(p.t), Y(p.v)) : ctx.moveTo(X(p.t), Y(p.v))))
    ctx.strokeStyle = GOLD_HI; ctx.lineWidth = 4; ctx.lineJoin = 'round'; ctx.stroke()
  }
  ctx.textAlign = 'center'; ctx.fillStyle = GREY; ctx.font = `400 17px ${MONO}`; spaced('1px')
  ctx.fillText(`As of ${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })} · ${location.host}`, W / 2, 1250)
  ctx.fillText('Past returns don’t guarantee future returns. Not financial advice.', W / 2, 1278)
  spaced('0px')
}

// ---------- sending ----------
function vWalletClient() {
  return VS.viem.createWalletClient({ account: VS.account, chain: VS.chain, transport: VS.viem.custom(vProv()) })
}
async function vSend(label, fn, args, address = vcfg().address) {
  VS.busy = true; VS.msg = `${label}: confirm in your wallet…`; vRender()
  try {
    const { request } = await VS.pub.simulateContract({ account: VS.account, address, abi: VS.abi, functionName: fn, args })
    const hash = await vWalletClient().writeContract(request)
    VS.msg = `${label}: waiting for the network…`; vRender()
    await VS.pub.waitForTransactionReceipt({ hash })
    VS.msg = `Done: ${label.toLowerCase()}.`
  } catch (e) {
    const m = String(e.shortMessage || e.message || e)
    const known = { NotAllowed: 'This wallet is not on the members list.', CapReached: 'The fund is full right now (deposit cap).', IsPaused: 'The fund is paused.', BadAmount: 'Check the amount.', NotAuthorized: 'This wallet cannot do that.', TooEarly: 'Not yet: the 48 hours are not over.' }
    VS.msg = Object.entries(known).find(([k]) => m.includes(k))?.[1] || (/rejected|denied/i.test(m) ? 'Cancelled in the wallet.' : m.split('\n')[0])
    e.shown = true
    throw e
  } finally { VS.busy = false; await vRefresh() }
}
// Early settlement: the manager signs a short message (no transaction, no gas); the keeper checks it on the server
async function vSettleNow() {
  const d = VS.d
  if (!d.queueLengths[0] && !d.queueLengths[1]) { VS.msg = 'Nothing is waiting to settle.'; return vRender() }
  if (!(await ask(`Settle ${d.queueLengths[0]} deposit(s) and ${d.queueLengths[1]} redemption(s) now, at the fund's current value? Only do this when the bot is not in a trade.`))) return
  VS.busy = true; VS.msg = 'Settle now: sign the message in your wallet (it costs nothing)…'; vRender()
  try {
    const message = `Midas × Kiyoshi Fund: settle now\nVault: ${vcfg().address}\nTime: ${new Date().toISOString()}`
    const signature = await vWalletClient().signMessage({ message })
    VS.msg = 'Settle now: the keeper is settling (about 30 seconds)…'; vRender()
    const c = window.FUND_CONFIG
    const r = await fetch(`${c.supabaseUrl}/functions/v1/vault-keeper`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', apikey: c.supabaseAnonKey, Authorization: `Bearer ${c.supabaseAnonKey}` },
      body: JSON.stringify({ settle: { message, signature } }),
    })
    const j = await r.json().catch(() => ({}))
    VS.msg = j.error || (j.notes || []).filter((n) => !n.startsWith('season')).join(' · ') || 'Sent.'
  } catch (e) {
    const m = String(e.shortMessage || e.message || e)
    VS.msg = /rejected|denied/i.test(m) ? 'Cancelled in the wallet.' : m.split('\n')[0]
  } finally {
    VS.busy = false
    await loadPublic()
    await vRefresh()
  }
}
const vFail = (e) => { if (!e?.shown) VS.msg = e?.message || String(e); vRender() }

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-vault]')
  if (!b) return
  const a = b.dataset.vault
  VS.msg = null
  try {
    if (a === 'retry') return vRefresh()
    if (a === 'connect') {
      if (!vProv()) { modal(`${vNoWallet()}<button class="btn plain" data-action="close-modal">Close</button>`); return }
      await vInit()
      VS.account = (await vProv().request({ method: 'eth_requestAccounts' }))[0]
      VS.chainId = Number(await vProv().request({ method: 'eth_chainId' }))
      VS.hidden = false
      try { localStorage.removeItem('vault-disconnected') } catch { /* optional */ }
      return vRefresh()
    }
    if (a === 'disconnect') {
      VS.hidden = true; VS.account = null
      try { localStorage.setItem('vault-disconnected', '1') } catch { /* optional */ }
      try { await vProv()?.request({ method: 'wallet_revokePermissions', params: [{ eth_accounts: {} }] }) } catch { /* not every wallet supports it */ }
      vRender()
      return window.onVaultExtras?.()
    }
    if (a === 'switch') {
      const net = vnet(), hex = '0x' + net.id.toString(16)
      try { await vProv().request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hex }] }) } catch {
        await vProv().request({ method: 'wallet_addEthereumChain', params: [{ chainId: hex, chainName: net.name, rpcUrls: [net.rpc], nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 }, blockExplorerUrls: [net.explorer] }] })
      }
      return
    }
    if (a === 'card-download') return canvasBlob('vault-card').then((x) => saveBlob(x, 'midas-kiyoshi-pnl.png'))
    if (a === 'card-share') {
      const blob = await canvasBlob('vault-card'), file = new File([blob], 'midas-kiyoshi-pnl.png', { type: 'image/png' })
      if (navigator.canShare?.({ files: [file] })) navigator.share({ files: [file] }).catch(() => {})
      return
    }
    if (a === 'card-amounts') { state.card.amounts = !state.card.amounts; return vRender() }
    if (a === 'card-name') { state.card.name = state.card.name ? 0 : 1; return vRender() }
    if (a === 'cancel-deposit') return await vSend('Cancel deposit', 'cancelDeposit', [])
    if (a === 'cancel-redeem') return await vSend('Cancel redemption', 'cancelRedeem', [])
    if (a === 'claim') return await vSend('Collect payout', 'claim', [])
    if (a === 'pause') { if (await ask('Pause the fund now? Deposits, redemptions and settlements stop until you unpause.')) await vSend('Pause the fund', 'setPaused', [true]); return }
    if (a === 'unpause') return await vSend('Unpause the fund', 'setPaused', [false])
    if (a === 'exec') return await vSend(`Apply the ${CHANGE_NAMES[b.dataset.i].toLowerCase()} change`, 'executeChange', [Number(b.dataset.i)])
    if (a === 'cancel-change') return await vSend(`Cancel the ${CHANGE_NAMES[b.dataset.i].toLowerCase()} change`, 'cancelChange', [Number(b.dataset.i)])
    if (a === 'settle-now') return await vSettleNow()
    if (a === 'approve' || a === 'remove') return await vSend(`${a === 'approve' ? 'Approve' : 'Remove'} ${vMask(b.dataset.who)}`, 'setAllowed', [b.dataset.who, a === 'approve'])
  } catch (e) { vFail(e) }
})

document.addEventListener('submit', async (ev) => {
  const form = ev.target.closest('form[data-vault-form]')
  if (!form) return
  ev.preventDefault()
  const f = Object.fromEntries(new FormData(form)), kind = form.dataset.vaultForm, d = VS.d
  VS.msg = null
  try {
    const { parseUnits, isAddress } = VS.viem
    if (kind === 'deposit') {
      const amt = parseUnits(String(f.amount || 0), 6)
      if (amt < d.minDeposit) throw new Error(`The minimum deposit is ${money(vUsd(d.minDeposit))}`)
      if (amt > d.me.usdt) throw new Error('Not enough USDT in this wallet')
      if (d.me.allowance < amt) await vSend('Allow the fund to take your USDT', 'approve', [vcfg().address, amt], vcfg().usdt)
      return await vSend(`Deposit ${money(vUsd(amt))}`, 'requestDeposit', [amt])
    }
    if (kind === 'redeem') {
      let shares = d.me.shares
      if (!f.all) {
        const usdAmt = Number(f.amount || 0)
        if (!(usdAmt > 0)) throw new Error('Enter an amount, or tick Everything')
        shares = BigInt(Math.floor((usdAmt / vUsd(d.sharePrice)) * 1e6)) * 10n ** 12n
        if (shares > d.me.shares) shares = d.me.shares
      }
      return await vSend(`Redeem ${vShares(shares)} shares`, 'requestRedeem', [shares])
    }
    if (kind === 'allow') {
      if (!isAddress(f.who || '')) throw new Error('Enter a wallet address (0x…)')
      const ok = ev.submitter?.value !== 'remove'
      return await vSend(`${ok ? 'Approve' : 'Remove'} ${vMask(f.who)}`, 'setAllowed', [f.who, ok])
    }
    if (kind === 'limits') return await vSend('Save limits', 'setLimits', [parseUnits(String(f.cap || 0), 6), parseUnits(String(f.min || 1), 6)])
    if (kind === 'fee') {
      const bps = Math.round(Number(f.pct) * 100)
      if (!(bps >= 0 && bps <= 2000)) throw new Error('The fee must be between 0% and 20%')
      return await vSend(`Queue a ${bps / 100}% fee (applies in 48 hours)`, 'queueChange', [4, BigInt(bps)])
    }
  } catch (e) { vFail(e) }
})

window.mountVault = () => { vRender(); vRefresh() }
window.vaultState = VS
// The page may have been drawn before this file loaded: fill it now.
if (document.querySelector('[data-vx]')) window.mountVault()
