// On-chain fund (FundVault on Celo): connect a wallet, deposit USDT, see and redeem shares.
// Shown on the public page and the members' dashboard once FUND_CONFIG.vault.address is set.
// Uses the wallet the page is opened in (MetaMask, Rabby, Valora, MiniPay); reads go to a public Celo RPC.
const VCFG = window.FUND_CONFIG.vault || {}
const VAULT_NETS = {
  mainnet: { id: 42220, name: 'Celo', rpc: 'https://forno.celo.org', explorer: 'https://celoscan.io' },
  testnet: { id: 11142220, name: 'Celo Sepolia (testnet)', rpc: 'https://forno.celo-sepolia.celo-testnet.org', explorer: 'https://celo-sepolia.blockscout.com' },
  local: { id: 31337, name: 'Local test chain', rpc: 'http://127.0.0.1:8545', explorer: 'http://127.0.0.1' }, // development only
}
const VNET = VAULT_NETS[VCFG.network] || VAULT_NETS.mainnet
const VAULT_ABI_SRC = [
  'function nav() view returns (uint256)', 'function totalSupply() view returns (uint256)', 'function sharePrice() view returns (uint256)',
  'function feeBps() view returns (uint256)', 'function depositCap() view returns (uint256)', 'function minDeposit() view returns (uint256)',
  'function paused() view returns (bool)', 'function manager() view returns (address)', 'function owner() view returns (address)',
  'function settlements() view returns (uint256)', 'function navUpdatedAt() view returns (uint256)', 'function totalPendingDeposits() view returns (uint256)',
  'function queueLengths() view returns (uint256 deposits, uint256 redeems)', 'function pending(uint8) view returns (uint256 value, uint256 eta)',
  'function memberInfo(address) view returns (uint256 shares, uint256 escrowed, uint256 value, uint256 costBasis, uint256 feeIfRedeemed, uint256 queuedDeposit, uint256 unclaimed, bool isAllowed)',
  'function requestDeposit(uint256 assets)', 'function cancelDeposit()', 'function requestRedeem(uint256 shares)', 'function cancelRedeem()', 'function claim()',
  'function setAllowed(address member, bool ok)', 'function setLimits(uint256 depositCap, uint256 minDeposit)',
  'function queueChange(uint8 c, uint256 value)', 'function cancelChange(uint8 c)', 'function executeChange(uint8 c)',
  'function balanceOf(address) view returns (uint256)', 'function allowance(address, address) view returns (uint256)', 'function approve(address, uint256) returns (bool)',
]
const VS = { viem: null, abi: null, pub: null, account: null, chainId: null, d: null, msg: null, loadErr: null, busy: false }
const vUsd = (v) => Number(v) / 1e6
const vShares = (v) => (Number(v) / 1e18).toLocaleString('en-US', { maximumFractionDigits: 4 })
const vLink = (path, text) => `<a href="${VNET.explorer}/${path}" target="_blank" rel="noopener">${text}</a>`

async function vInit() {
  if (VS.viem) return
  VS.viem = await import('https://cdn.jsdelivr.net/npm/viem@2.21.60/+esm')
  VS.abi = VS.viem.parseAbi(VAULT_ABI_SRC)
  VS.chain = VS.viem.defineChain({ id: VNET.id, name: VNET.name, nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 }, rpcUrls: { default: { http: [VNET.rpc] } } })
  VS.pub = VS.viem.createPublicClient({ chain: VS.chain, transport: VS.viem.http(VNET.rpc) })
  if (window.ethereum) {
    window.ethereum.on?.('accountsChanged', (a) => { VS.account = a[0] || null; vRefresh() })
    window.ethereum.on?.('chainChanged', (c) => { VS.chainId = Number(c); vRefresh() })
    try {
      const a = await window.ethereum.request({ method: 'eth_accounts' })
      VS.account = a[0] || null
      VS.chainId = Number(await window.ethereum.request({ method: 'eth_chainId' }))
    } catch { /* not connected yet */ }
  }
}

async function vLoad() {
  const r = (fn, args = [], address = VCFG.address) => VS.pub.readContract({ address, abi: VS.abi, functionName: fn, args })
  const names = ['nav', 'totalSupply', 'sharePrice', 'feeBps', 'depositCap', 'minDeposit', 'paused', 'manager', 'settlements', 'navUpdatedAt', 'totalPendingDeposits', 'queueLengths']
  const vals = await Promise.all(names.map((n) => r(n)))
  const d = Object.fromEntries(names.map((n, i) => [n, vals[i]]))
  d.feeChange = await r('pending', [4])
  if (VS.account) {
    const [info, bal, allow] = await Promise.all([r('memberInfo', [VS.account]), r('balanceOf', [VS.account], VCFG.usdt), r('allowance', [VS.account, VCFG.address], VCFG.usdt)])
    d.me = { shares: info[0], escrowed: info[1], value: info[2], cost: info[3], fee: info[4], queued: info[5], unclaimed: info[6], allowed: info[7], usdt: bal, allowance: allow }
    d.isManager = VS.account.toLowerCase() === d.manager.toLowerCase()
  }
  VS.d = d
}

// Next daily settlement (the keeper settles after 20:00 UTC).
function vNextSettle() {
  const t = new Date(); t.setUTCHours(20, 0, 0, 0)
  if (t < new Date()) t.setUTCDate(t.getUTCDate() + 1)
  return t.toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
}

function vaultBox() {
  if (!VCFG.address) return ''
  const d = VS.d
  const head = `<div class="section-title">On-chain fund${VCFG.network === 'testnet' ? ' · testnet' : ''}</div>`
  if (!d) return `${head}<div class="card"><div class="sub">${VS.loadErr ? esc(VS.loadErr) : 'Reading the fund contract…'}</div></div>`
  const price = vUsd(d.sharePrice), me = d.me
  const fundTiles = `
  <div class="grid">
    ${stat('Share price', '$' + price.toFixed(4), `${(Number(d.feeBps) / 100).toFixed(0)}% fee on profit · contract ${vLink('address/' + VCFG.address, short(VCFG.address))}`, '', true)}
    ${stat('Fund value', money(vUsd(d.nav)), d.navUpdatedAt ? `reported ${stamp(new Date(Number(d.navUpdatedAt) * 1000).toISOString())}` : 'not reported yet')}
    ${stat('Next settlement', vNextSettle(), `${d.queueLengths[0]} deposit(s), ${d.queueLengths[1]} redemption(s) queued`)}
    ${stat('Deposits', d.paused ? 'Paused' : d.depositCap > 0n ? 'Open' : 'Closed', `cap ${money(vUsd(d.depositCap))} · minimum ${money(vUsd(d.minDeposit))}`)}
  </div>`
  const notice = VS.msg ? `<div class="msg ${VS.msg.startsWith('Done') ? 'ok' : 'err'}" style="margin-bottom:12px">${esc(VS.msg)}</div>` : ''
  let walletCard
  if (!window.ethereum) {
    walletCard = `<div class="card"><div class="label">Your wallet</div>
      <p class="note">Open this page in your wallet app's browser to connect: MetaMask, Rabby, Valora or MiniPay.</p>
      <a class="btn ghost small" href="https://metamask.app.link/dapp/${location.host}${location.pathname}">Open in MetaMask</a></div>`
  } else if (!VS.account) {
    walletCard = `<div class="card"><div class="label">Your wallet</div><p class="note">Connect to deposit USDT, see your shares and redeem.</p>
      <button class="btn" data-vault="connect">Connect wallet</button></div>`
  } else if (VS.chainId !== VNET.id) {
    walletCard = `<div class="card"><div class="label">Your wallet · ${esc(short(VS.account))}</div><p class="note">Your wallet is on another network.</p>
      <button class="btn" data-vault="switch">Switch to ${VNET.name}</button></div>`
  } else {
    const profit = Math.round((vUsd(me.value) - vUsd(me.cost)) * 100) / 100 || 0
    walletCard = `<div class="card"><div class="label">Your wallet · ${vLink('address/' + VS.account, esc(short(VS.account)))}</div>
      <div class="preview" style="margin-top:12px;border-style:solid;border-color:var(--line)">
        <div><span>Shares</span><span>${vShares(me.shares + me.escrowed)}${me.escrowed ? ` (${vShares(me.escrowed)} queued to redeem)` : ''}</span></div>
        <div><span>Value</span><span>${money(vUsd(me.value))}</span></div>
        <div><span>Put in</span><span>${money(vUsd(me.cost))}</span></div>
        <div><span>Profit before fee</span><span class="${tone(profit)}">${money(profit, true)}</span></div>
        <div><span>Fee if you redeemed today</span><span>${money(vUsd(me.fee))}</span></div>
        ${me.queued ? `<div><span>Deposit waiting</span><span>${money(vUsd(me.queued))} <button class="btn plain small" data-vault="cancel-deposit">Cancel</button></span></div>` : ''}
        ${me.escrowed ? `<div><span>Redemption waiting</span><span>${vShares(me.escrowed)} shares <button class="btn plain small" data-vault="cancel-redeem">Cancel</button></span></div>` : ''}
        ${me.unclaimed ? `<div class="total"><span>Ready to collect</span><span>${money(vUsd(me.unclaimed))} <button class="btn small" data-vault="claim">Collect</button></span></div>` : ''}
      </div>
      <div class="grid two" style="margin-top:12px">
        <form data-vault-form="deposit">
          <label class="field"><span>Deposit USDT · you have ${money(vUsd(me.usdt))}</span><input name="amount" type="number" step="0.01" min="0" placeholder="e.g. 50" ${me.allowed ? '' : 'disabled'}></label>
          ${me.allowed ? `<label class="check"><input type="checkbox" name="ok" required> I accept the risk disclosure below, including the possible loss of everything I deposit.</label>
            <button class="btn" type="submit" ${VS.busy ? 'disabled' : ''}>Deposit</button>`
            : `<p class="note" style="margin:0">This wallet is not on the members list yet. Send the manager your address: <span class="mono">${esc(short(VS.account))}</span> <button class="btn plain small" type="button" data-action="copy" data-value="${esc(VS.account)}">Copy</button></p>`}
        </form>
        <form data-vault-form="redeem">
          <label class="field"><span>Redeem (USD value)</span><input name="amount" type="number" step="0.01" min="0" placeholder="e.g. 20" ${me.shares ? '' : 'disabled'}></label>
          <label class="check"><input type="checkbox" name="all" ${me.shares ? '' : 'disabled'}> Everything</label>
          <button class="btn ghost" type="submit" ${VS.busy || !me.shares ? 'disabled' : ''}>Redeem</button>
        </form>
      </div>
      <p class="note" style="margin-bottom:0">Deposits and redemptions wait until the next settlement (${vNextSettle()}) and use that day's share price.
        You can cancel until then. Redemptions are paid in USDT to this wallet, after the fee on your profit.</p></div>`
  }
  const fc = d.feeChange
  const managerCard = d.isManager && VS.chainId === VNET.id ? `
  <div class="card" style="margin-top:12px"><div class="label">Manager</div>
    <div class="grid two" style="margin-top:12px">
      <form data-vault-form="allow"><label class="field"><span>Wallet to allow or remove</span><input name="who" placeholder="0x…" autocapitalize="none" spellcheck="false"></label>
        <div class="row"><button class="btn small" type="submit" name="op" value="allow">Allow</button><button class="btn plain small" type="submit" name="op" value="remove">Remove</button></div></form>
      <form data-vault-form="limits"><label class="field"><span>Deposit cap · minimum (USDT)</span>
        <div class="row" style="flex-wrap:nowrap"><input name="cap" type="number" step="1" value="${vUsd(d.depositCap)}"><input name="min" type="number" step="0.01" value="${vUsd(d.minDeposit)}"></div></label>
        <button class="btn small" type="submit">Save limits</button></form>
      <form data-vault-form="fee"><label class="field"><span>New fee (% of profit, max 20) · waits 48 hours</span><input name="pct" type="number" step="0.5" min="0" max="20" value="${Number(d.feeBps) / 100}"></label>
        <button class="btn small" type="submit">Queue fee change</button>
        ${fc[1] ? `<p class="note" style="margin:0">Queued: ${Number(fc[0]) / 100}% · ${Number(fc[1]) * 1000 > Date.now() ? `ready ${stamp(new Date(Number(fc[1]) * 1000).toISOString())}` : '<button class="btn small" type="button" data-vault="exec-fee">Apply now</button>'}
          <button class="btn plain small" type="button" data-vault="cancel-fee">Cancel</button></p>` : ''}</form>
    </div></div>` : ''
  return `${head}${notice}${fundTiles}<div style="margin-top:12px">${walletCard}</div>${managerCard}`
}

async function vRefresh() {
  try { await vInit(); await vLoad(); VS.loadErr = null } catch { VS.loadErr = 'The fund contract could not be read right now. Try again in a minute.' }
  for (const el of document.querySelectorAll('#vault-box')) el.innerHTML = vaultBox()
}

async function vWallet() {
  return VS.viem.createWalletClient({ account: VS.account, chain: VS.chain, transport: VS.viem.custom(window.ethereum) })
}
async function vSend(label, fn, args, address = VCFG.address) {
  VS.busy = true; VS.msg = `${label}: confirm in your wallet…`
  for (const el of document.querySelectorAll('#vault-box')) el.innerHTML = vaultBox()
  try {
    const w = await vWallet()
    const { request } = await VS.pub.simulateContract({ account: VS.account, address, abi: VS.abi, functionName: fn, args })
    const hash = await w.writeContract(request)
    VS.msg = `${label}: waiting for the network…`
    for (const el of document.querySelectorAll('#vault-box')) el.innerHTML = vaultBox()
    await VS.pub.waitForTransactionReceipt({ hash })
    VS.msg = `Done: ${label.toLowerCase()}.`
  } catch (e) {
    const m = String(e.shortMessage || e.message || e)
    const known = { NotAllowed: 'This wallet is not on the members list.', CapReached: 'The fund is full right now (deposit cap).', IsPaused: 'The fund is paused.', BadAmount: 'Check the amount.', NotAuthorized: 'This wallet cannot do that.' }
    VS.msg = Object.entries(known).find(([k]) => m.includes(k))?.[1] || (/rejected|denied/i.test(m) ? 'Cancelled in the wallet.' : m.split('\n')[0])
    e.shown = true
    throw e
  } finally { VS.busy = false; await vRefresh() }
}

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-vault]')
  if (!b) return
  const a = b.dataset.vault
  VS.msg = null
  try {
    if (a === 'connect') { const acc = await window.ethereum.request({ method: 'eth_requestAccounts' }); VS.account = acc[0]; VS.chainId = Number(await window.ethereum.request({ method: 'eth_chainId' })); return vRefresh() }
    if (a === 'switch') {
      const hex = '0x' + VNET.id.toString(16)
      try { await window.ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hex }] }) } catch {
        await window.ethereum.request({ method: 'wallet_addEthereumChain', params: [{ chainId: hex, chainName: VNET.name, rpcUrls: [VNET.rpc], nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 }, blockExplorerUrls: [VNET.explorer] }] })
      }
      return
    }
    if (a === 'cancel-deposit') return await vSend('Cancel deposit', 'cancelDeposit', [])
    if (a === 'cancel-redeem') return await vSend('Cancel redemption', 'cancelRedeem', [])
    if (a === 'claim') return await vSend('Collect payout', 'claim', [])
    if (a === 'exec-fee') return await vSend('Apply fee change', 'executeChange', [4])
    if (a === 'cancel-fee') return await vSend('Cancel fee change', 'cancelChange', [4])
  } catch (e) {
    if (!e?.shown) VS.msg = e?.message || String(e)
    for (const el of document.querySelectorAll('#vault-box')) el.innerHTML = vaultBox()
  }
})

document.addEventListener('submit', async (ev) => {
  const form = ev.target.closest('form[data-vault-form]')
  if (!form) return
  ev.preventDefault()
  const f = Object.fromEntries(new FormData(form)), kind = form.dataset.vaultForm, d = VS.d
  const { parseUnits, isAddress } = VS.viem
  VS.msg = null
  try {
    if (kind === 'deposit') {
      const amt = parseUnits(String(f.amount || 0), 6)
      if (amt < d.minDeposit) throw new Error(`The minimum deposit is ${money(vUsd(d.minDeposit))}`)
      if (amt > d.me.usdt) throw new Error('Not enough USDT in this wallet')
      if (d.me.allowance < amt) await vSend('Allow the fund to take your USDT', 'approve', [VCFG.address, amt], VCFG.usdt)
      return await vSend(`Deposit ${money(vUsd(amt))}`, 'requestDeposit', [amt])
    }
    if (kind === 'redeem') {
      const total = d.me.shares
      let shares = total
      if (!f.all) {
        const usdAmt = Number(f.amount || 0)
        if (!(usdAmt > 0)) throw new Error('Enter an amount, or tick Everything')
        shares = BigInt(Math.floor((usdAmt / vUsd(d.sharePrice)) * 1e6)) * 10n ** 12n
        if (shares > total) shares = total
      }
      return await vSend(`Redeem ${vShares(shares)} shares`, 'requestRedeem', [shares])
    }
    if (kind === 'allow') {
      if (!isAddress(f.who || '')) throw new Error('Enter a wallet address (0x…)')
      const ok = ev.submitter?.value !== 'remove'
      return await vSend(`${ok ? 'Allow' : 'Remove'} ${short(f.who)}`, 'setAllowed', [f.who, ok])
    }
    if (kind === 'limits') return await vSend('Save limits', 'setLimits', [parseUnits(String(f.cap || 0), 6), parseUnits(String(f.min || 1), 6)])
    if (kind === 'fee') {
      const bps = Math.round(Number(f.pct) * 100)
      if (!(bps >= 0 && bps <= 2000)) throw new Error('The fee must be between 0% and 20%')
      return await vSend(`Queue a ${bps / 100}% fee (applies in 48 hours)`, 'queueChange', [4, BigInt(bps)])
    }
  } catch (e) {
    if (!e?.shown) VS.msg = e?.message || String(e)
    for (const el of document.querySelectorAll('#vault-box')) el.innerHTML = vaultBox()
  }
})

// Mount: app.js renders <div id="vault-box"></div> and calls this after each render.
window.mountVault = () => {
  if (!VCFG.address) return
  for (const el of document.querySelectorAll('#vault-box')) el.innerHTML = vaultBox()
  vRefresh()
}
