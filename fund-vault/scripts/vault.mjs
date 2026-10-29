// The bot wallet's controls for the fund vault, from Termux. Every command that sends a transaction shows what
// it will do and asks for "yes" first. Add --mainnet for the real fund (default: the testnet).
//
//   npm run vault -- status                       roles, settings, value, queues, pending changes
//   npm run vault -- pause | unpause              immediate
//   npm run vault -- set-manager 0x…              queued for 48 hours (also set-operator, set-fee-recipient, set-owner)
//   npm run vault -- set-max-move 50              queued: how far the value may move in a day, in %
//   npm run vault -- execute manager              apply a queued change after its 48 hours
//   npm run vault -- cancel manager               cancel a queued change, immediately
//   npm run vault -- approve                      let settlements pull payouts from the bot wallet (once)
//   npm run vault -- open live | open 117.28      opening shares for the personal wallet at $1.00 (once)
//   npm run vault -- allow 0x… | disallow 0x…     who may deposit (the manager can also do this on the site)
//   npm run vault -- limits 5000 1                deposit cap and minimum deposit, in USDT
//   npm run vault -- report live | settle live    post the value / settle the queues (the keeper does this)
//   npm run vault -- mint 0x… 100                 testnet only: test USDT for trying deposits
import { erc20Abi, formatUnits, getAddress, isAddress, maxUint256 } from 'viem'
import { PERSONAL, art, confirm, errText, fmtShares, fmtUsd, loadDeployment, network, send, usd } from './lib.mjs'
import { walletValue } from './nav.mjs'

const args = process.argv.slice(2).filter((a) => !a.startsWith('--'))
const [cmd, a1, a2] = args
const net = network(process.argv)
const dep = loadDeployment(net.name)
const V = art('FundVault').abi
const read = (fn, a = []) => net.pub.readContract({ address: dep.vault, abi: V, functionName: fn, args: a })
const CHANGES = { owner: 0, operator: 1, manager: 2, 'fee-recipient': 3, fee: 4, 'max-move': 5 }
const NAMES = Object.keys(CHANGES)
const me = net.account.address

async function act(label, fn, fnArgs) {
  console.log(`\n${label}\n  contract ${dep.vault} on ${net.name}, signed by the bot wallet ${me}`)
  if (!(await confirm('Send it?'))) return console.log('Cancelled; nothing sent.')
  const hash = await send(net, dep.vault, V, fn, fnArgs)
  console.log(`Done: ${net.explorer}/tx/${hash}`)
}
const addr = (x) => {
  if (!x || !isAddress(x)) throw new Error('Give a wallet address (0x…)')
  return getAddress(x)
}
async function liveNav() {
  const { value, holdings } = await walletValue(net, await read('operator'), dep)
  console.log(`Bot wallet value: ${fmtUsd(value)}  (${holdings.map((h) => `${h.amount.toFixed(2)} ${h.symbol}`).join(', ') || 'empty'})`)
  return value
}
const amountOrLive = async (x) => (x === 'live' || !x ? liveNav() : usd(x))

async function status() {
  const [owner, operator, manager, feeRecipient, paused, feeBps, maxMove, nav, supply, price, cap, min, settlements, navAt, q] = await Promise.all(
    ['owner', 'operator', 'manager', 'feeRecipient', 'paused', 'feeBps', 'maxNavChangeBps', 'nav', 'totalSupply', 'sharePrice', 'depositCap', 'minDeposit', 'settlements', 'navUpdatedAt', 'queueLengths'].map((f) => read(f)))
  const tag = (a) => (a.toLowerCase() === me.toLowerCase() ? ' (bot wallet)' : a.toLowerCase() === PERSONAL.toLowerCase() ? ' (personal wallet)' : '')
  console.log(`\nFund vault ${dep.vault} on ${net.name}${paused ? '   *** PAUSED ***' : ''}`)
  console.log(`  owner          ${owner}${tag(owner)}`)
  console.log(`  operator       ${operator}${tag(operator)}`)
  console.log(`  manager        ${manager}${tag(manager)}`)
  console.log(`  fee recipient  ${feeRecipient}${tag(feeRecipient)}`)
  console.log(`  fee            ${Number(feeBps) / 100}% of profit · value may move ${Number(maxMove) / 100}% a day`)
  console.log(`  fund value     ${fmtUsd(nav)} · ${fmtShares(supply)} shares · share price $${Number(formatUnits(price, 6)).toFixed(4)}`)
  console.log(`  updated        ${navAt ? new Date(Number(navAt) * 1000).toLocaleString() : 'never'} · ${settlements} settlement(s)`)
  console.log(`  deposits       cap ${fmtUsd(cap)}${cap === 0n ? ' (closed)' : ''} · minimum ${fmtUsd(min)}`)
  console.log(`  queued         ${q[0]} deposit(s), ${q[1]} redemption(s) · ${fmtUsd(await read('totalPendingDeposits'))} waiting`)
  for (const name of NAMES) {
    const [value, eta] = await read('pending', [CHANGES[name]])
    if (!eta) continue
    const v = name === 'fee' || name === 'max-move' ? `${Number(value) / 100}%` : getAddress('0x' + value.toString(16).padStart(40, '0'))
    const left = Number(eta) * 1000 - Date.now()
    console.log(`  QUEUED CHANGE  ${name} -> ${v}  ${left > 0 ? `ready in ${(left / 3_600_000).toFixed(1)} h` : 'ready: npm run vault -- execute ' + name}`)
  }
  const allowance = await net.pub.readContract({ address: dep.usdt, abi: erc20Abi, functionName: 'allowance', args: [operator, dep.vault] })
  if (allowance === 0n) console.log('  note           the operator has not approved payouts yet: npm run vault -- approve')
}

try {
  if (!cmd || cmd === 'status') await status()
  else if (cmd === 'pause') await act('PAUSE the fund: deposits, redemptions and settlements stop at once. Cancels and claims keep working.', 'setPaused', [true])
  else if (cmd === 'unpause') await act('Unpause the fund.', 'setPaused', [false])
  else if (cmd.startsWith('set-') && cmd !== 'set-fee') {
    const name = cmd.slice(4)
    if (!(name in CHANGES)) throw new Error(`Unknown role. Use one of: ${NAMES.filter((n) => n !== 'fee').map((n) => 'set-' + n).join(', ')}`)
    const value = name === 'max-move' ? BigInt(Math.round(Number(a1) * 100)) : BigInt(addr(a1))
    await act(`Queue: ${name} -> ${a1}. It can be applied in 48 hours (npm run vault -- execute ${name}) and cancelled until then.`, 'queueChange', [CHANGES[name], value])
  } else if (cmd === 'set-fee') {
    console.log('The fee rate is set by the manager (your personal wallet) on the site, not by the bot wallet.')
  } else if (cmd === 'execute' || cmd === 'cancel') {
    if (!(a1 in CHANGES)) throw new Error(`Say which change: ${NAMES.join(', ')}`)
    await act(`${cmd === 'execute' ? 'Apply' : 'Cancel'} the queued ${a1} change.`, cmd === 'execute' ? 'executeChange' : 'cancelChange', [CHANGES[a1]])
  } else if (cmd === 'approve') {
    console.log(`\nApprove the vault to pull USDT from the bot wallet, only to pay members' redemptions at settlement.`)
    if (await confirm('Send it?')) {
      const hash = await send(net, dep.usdt, erc20Abi, 'approve', [dep.vault, maxUint256])
      console.log(`Done: ${net.explorer}/tx/${hash}`)
    }
  } else if (cmd === 'open') {
    const value = await amountOrLive(a1)
    await act(`Opening shares: ${fmtUsd(value)} -> ${fmtShares(value * 10n ** 12n)} shares at $1.00 for the personal wallet ${PERSONAL}. Only possible once.`, 'openingShares', [PERSONAL, value])
  } else if (cmd === 'allow' || cmd === 'disallow') {
    await act(`${cmd === 'allow' ? 'Allow' : 'Stop'} ${addr(a1)} ${cmd === 'allow' ? 'to deposit' : 'from depositing (they can still redeem)'}.`, 'setAllowed', [addr(a1), cmd === 'allow'])
  } else if (cmd === 'limits') {
    await act(`Deposit cap ${fmtUsd(usd(a1))} (total fund value), minimum deposit ${fmtUsd(usd(a2 || 1))}.`, 'setLimits', [usd(a1), usd(a2 || 1)])
  } else if (cmd === 'report' || cmd === 'settle') {
    const value = await amountOrLive(a1)
    const [nav, q] = await Promise.all([read('nav'), read('queueLengths')])
    const move = nav ? ` (${(((Number(value) - Number(nav)) / Number(nav)) * 100).toFixed(2)}% since the last report)` : ''
    await act(cmd === 'report' ? `Report the fund value: ${fmtUsd(value)}${move}.` : `Settle ${q[0]} deposit(s) and ${q[1]} redemption(s) at a fund value of ${fmtUsd(value)}${move}.`, cmd === 'report' ? 'reportValue' : 'settle', [value])
  } else if (cmd === 'mint') {
    if (net.name === 'mainnet') throw new Error('mint is for the testnet only')
    const hash = await send(net, dep.usdt, art('MockUSDT').abi, 'mint', [addr(a1), usd(a2 || 100)])
    console.log(`Minted ${a2 || 100} test USDT to ${a1}: ${net.explorer}/tx/${hash}`)
  } else {
    console.log('Unknown command. See the list at the top of scripts/vault.mjs, or run: npm run vault -- status')
  }
} catch (e) {
  console.error(`\n${errText(e)}`)
  process.exit(1)
}
