// Shared bits for the Termux commands: networks, the bot wallet key, the deployed addresses, confirmations.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import readline from 'node:readline/promises'
import dotenv from 'dotenv'
import { createPublicClient, createWalletClient, defineChain, fallback, formatUnits, http, parseUnits } from 'viem'
import { celo } from 'viem/chains'
import { privateKeyToAccount } from 'viem/accounts'

export const ROOT = path.dirname(path.dirname(new URL(import.meta.url).pathname))
export const PERSONAL = '0xf95E42d5FC3602b05CE9cF8d2E4cFa6b810F3113' // Midas's personal wallet: manager + fee recipient
export const CELO_USDT = '0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e'

const celoSepolia = defineChain({
  id: 11142220, name: 'Celo Sepolia', nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 },
  rpcUrls: { default: { http: ['https://forno.celo-sepolia.celo-testnet.org'] } },
  blockExplorers: { default: { name: 'Blockscout', url: 'https://celo-sepolia.blockscout.com' } },
  testnet: true,
})
export const NETWORKS = {
  testnet: { chain: celoSepolia, rpcs: ['https://forno.celo-sepolia.celo-testnet.org'], explorer: 'https://celo-sepolia.blockscout.com' },
  mainnet: { chain: celo, rpcs: ['https://forno.celo.org', 'https://celo.drpc.org', 'https://rpc.ankr.com/celo'], explorer: 'https://celoscan.io' },
  // for development: npx hardhat node
  local: { chain: defineChain({ id: 31337, name: 'Local', nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 }, rpcUrls: { default: { http: ['http://127.0.0.1:8545'] } } }), rpcs: ['http://127.0.0.1:8545'], explorer: 'local' },
}

// The bot wallet key: from this folder's .env, or the bot's own .env (~/kiyoshi or ~/midas-arb). Never printed.
export function botKey() {
  const places = [path.join(ROOT, '.env'), path.join(ROOT, '..', 'kiyoshi', '.env'), path.join(os.homedir(), 'kiyoshi', '.env'), path.join(os.homedir(), 'midas-arb', '.env')]
  for (const p of places) {
    if (!fs.existsSync(p)) continue
    const k = dotenv.parse(fs.readFileSync(p)).AGENT_PRIVATE_KEY
    if (k) return { key: k.startsWith('0x') ? k : '0x' + k, from: p }
  }
  throw new Error('No AGENT_PRIVATE_KEY found. Put it in fund-vault/.env (or keep it in ~/kiyoshi/.env).')
}

export function network(args) {
  const name = args.includes('--mainnet') ? 'mainnet' : args.includes('--local') ? 'local' : args.includes('--testnet') ? 'testnet' : (process.env.VAULT_NETWORK || 'testnet')
  const n = NETWORKS[name]
  const { key, from } = botKey()
  const account = privateKeyToAccount(key)
  const transport = fallback(n.rpcs.map((u) => http(u, { timeout: 20_000, retryCount: 2 })))
  return {
    name, ...n, account, keyFrom: from,
    pub: createPublicClient({ chain: n.chain, transport }),
    wallet: createWalletClient({ account, chain: n.chain, transport }),
  }
}

export const art = (n) => JSON.parse(fs.readFileSync(path.join(ROOT, 'build', `${n}.json`), 'utf8'))
const depFile = (name) => path.join(ROOT, 'deployments', `${name}.json`)
export const loadDeployment = (name) => {
  if (!fs.existsSync(depFile(name))) throw new Error(`No ${name} deployment yet. Run: npm run deploy -- --${name}`)
  return JSON.parse(fs.readFileSync(depFile(name), 'utf8'))
}
export const saveDeployment = (name, d) => {
  fs.mkdirSync(path.join(ROOT, 'deployments'), { recursive: true })
  fs.writeFileSync(depFile(name), JSON.stringify(d, null, 2) + '\n')
}

export async function confirm(question) {
  if (process.argv.includes('--yes')) return true
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
  const a = (await rl.question(`${question} Type yes to continue: `)).trim().toLowerCase()
  rl.close()
  return a === 'yes' || a === 'y'
}

export const usd = (x) => parseUnits(String(x), 6)
export const fmtUsd = (v) => '$' + Number(formatUnits(v, 6)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const fmtShares = (v) => Number(formatUnits(v, 18)).toLocaleString('en-US', { maximumFractionDigits: 4 })

export async function send(net, address, abi, functionName, args = []) {
  const { request } = await net.pub.simulateContract({ account: net.account, address, abi, functionName, args })
  const hash = await net.wallet.writeContract(request)
  const r = await net.pub.waitForTransactionReceipt({ hash, timeout: 120_000 })
  if (r.status !== 'success') throw new Error(`transaction failed: ${hash}`)
  return hash
}
export const errText = (e) => String(e?.shortMessage || e?.message || e).split('\n')[0]
