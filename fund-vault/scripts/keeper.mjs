// Keeper: runs next to the bot on the phone. Every hour it posts the bot wallet's value (the share price on the
// dashboard); once a day it settles the queued deposits and redemptions at that value.
//   npm run keeper                 testnet
//   npm run keeper -- --mainnet    the real fund
// Settings (env): KEEPER_REPORT_MIN (60), KEEPER_SETTLE_HOUR_UTC (20 = 9 pm in Lagos), KEEPER_DRY=1 to only print.
// Before settling it checks that the bot wallet holds enough USDT to pay the redemptions; if not, it waits.
import { erc20Abi } from 'viem'
import { art, errText, fmtUsd, loadDeployment, network, send } from './lib.mjs'
import { walletValue } from './nav.mjs'

const net = network(process.argv)
const dep = loadDeployment(net.name)
const V = art('FundVault').abi
const REPORT_MS = Number(process.env.KEEPER_REPORT_MIN || 60) * 60_000
const SETTLE_HOUR = Number(process.env.KEEPER_SETTLE_HOUR_UTC ?? 20)
const DRY = process.env.KEEPER_DRY === '1'
const read = (fn, a = []) => net.pub.readContract({ address: dep.vault, abi: V, functionName: fn, args: a })
const log = (m) => console.log(`[${new Date().toISOString().slice(0, 16).replace('T', ' ')}] ${m}`)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
let lastSettleDay = null

// USDT the settlement will need from the bot wallet: payouts minus new deposits (an estimate before fees).
async function cashNeeded(value) {
  const [supply, pendingDeposits] = await Promise.all([read('totalSupply'), read('totalPendingDeposits')])
  if (supply === 0n) return 0n
  const escrow = await read('balanceOf', [dep.vault])
  const payouts = escrow * value / supply
  return payouts > pendingDeposits ? payouts - pendingDeposits : 0n
}

async function tick() {
  if (await read('paused')) return log('paused; nothing to do')
  if ((await read('operator')).toLowerCase() !== net.account.address.toLowerCase()) return log('this wallet is no longer the operator; stopping is safe')
  const { value } = await walletValue(net, net.account.address, dep)
  const now = new Date()
  const day = now.toISOString().slice(0, 10)
  const [q, nav] = await Promise.all([read('queueLengths'), read('nav')])
  const due = now.getUTCHours() >= SETTLE_HOUR && lastSettleDay !== day
  if (due && (q[0] > 0n || q[1] > 0n)) {
    const need = await cashNeeded(value)
    const usdt = await net.pub.readContract({ address: dep.usdt, abi: erc20Abi, functionName: 'balanceOf', args: [net.account.address] })
    if (usdt < need) return log(`settlement due, but the bot wallet holds ${fmtUsd(usdt)} USDT and payouts need ~${fmtUsd(need)}; waiting for trades to close`)
    log(`settling ${q[0]} deposit(s), ${q[1]} redemption(s) at ${fmtUsd(value)}`)
    if (!DRY) log(`settled: ${net.explorer}/tx/${await send(net, dep.vault, V, 'settle', [value])}`)
    lastSettleDay = day
    return
  }
  if (due) lastSettleDay = day // nothing queued today
  if (value === nav) return log(`value unchanged at ${fmtUsd(value)}`)
  log(`value ${fmtUsd(nav)} -> ${fmtUsd(value)}`)
  if (!DRY) log(`reported: ${net.explorer}/tx/${await send(net, dep.vault, V, 'reportValue', [value])}`)
}

log(`keeper for ${dep.vault} on ${net.name}: report every ${REPORT_MS / 60_000} min, settle daily after ${SETTLE_HOUR}:00 UTC${DRY ? ' (dry run)' : ''}`)
for (;;) {
  try { await tick() } catch (e) {
    const m = errText(e)
    log(/ValueJump/.test(m) ? `the value moved more than the daily limit allows; check the bot wallet, or raise it: npm run vault -- set-max-move <percent>` : `error: ${m}`)
  }
  await sleep(REPORT_MS)
}
