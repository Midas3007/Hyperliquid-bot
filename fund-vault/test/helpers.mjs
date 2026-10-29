import { createRequire } from 'node:module'
import fs from 'node:fs'
import { createPublicClient, createWalletClient, custom, decodeErrorResult, encodeFunctionData, parseUnits } from 'viem'
const require = createRequire(import.meta.url)
process.env.HARDHAT_CONFIG = new URL('../hardhat.config.cjs', import.meta.url).pathname
const hre = require('hardhat')
export const provider = hre.network.provider
const art = (n) => JSON.parse(fs.readFileSync(new URL(`../build/${n}.json`, import.meta.url)))
export const usd = (x) => parseUnits(String(x), 6)
export const sh = (x) => parseUnits(String(x), 18)
const chain = { id: 31337, name: 'local', nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 }, rpcUrls: { default: { http: [] } } }
export const pub = createPublicClient({ chain, transport: custom(provider) })

export async function setup() {
  await provider.request({ method: 'hardhat_reset', params: [] })
  const [bot, personal, alice, bob, carol] = await provider.request({ method: 'eth_accounts' })
  const w = (a) => createWalletClient({ account: a, chain, transport: custom(provider) })
  const deploy = async (name, args) => {
    const { abi, bytecode } = art(name)
    const hash = await w(bot).deployContract({ abi, bytecode, args })
    const r = await pub.waitForTransactionReceipt({ hash })
    return r.contractAddress
  }
  const usdt = await deploy('MockUSDT', [])
  const vault = await deploy('FundVault', [usdt, bot, bot, personal, personal])
  const V = art('FundVault').abi, U = art('MockUSDT').abi
  const send = async (from, address, abi, functionName, args = []) => {
    // dry-run first, so a revert reports the contract's error name
    const data = encodeFunctionData({ abi, functionName, args })
    try { await provider.request({ method: 'eth_call', params: [{ from, to: address, data }, 'latest'] }) } catch (e) {
      const raw = e.data?.data ?? e.data
      let name = e.message
      try { name = decodeErrorResult({ abi, data: raw }).errorName } catch { /* not a custom error */ }
      throw new Error('reverted: ' + name)
    }
    const hash = await w(from).writeContract({ address, abi, functionName, args })
    const r = await pub.waitForTransactionReceipt({ hash })
    if (r.status !== 'success') throw new Error('reverted')
    return r
  }
  const v = (from, fn, args) => send(from, vault, V, fn, args)
  const u = (from, fn, args) => send(from, usdt, U, fn, args)
  const read = (fn, args = []) => pub.readContract({ address: vault, abi: V, functionName: fn, args })
  const bal = (a) => pub.readContract({ address: usdt, abi: U, functionName: 'balanceOf', args: [a] })
  const fails = async (p, name) => {
    try { await p } catch (e) { const m = String(e.message); if (name && !m.includes(name)) throw new Error(`expected ${name}, got: ${m.slice(0, 300)}`); return }
    throw new Error(`expected a revert${name ? ' (' + name + ')' : ''}`)
  }
  const warp = async (s) => { await provider.request({ method: 'evm_increaseTime', params: [s] }); await provider.request({ method: 'evm_mine', params: [] }) }
  return { bot, personal, alice, bob, carol, usdt, vault, v, u, read, bal, fails, warp }
}
