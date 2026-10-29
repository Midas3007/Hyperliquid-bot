// Deploy the fund vault from the phone, signed by the bot wallet.
//   npm run deploy -- --testnet     Celo Sepolia, with a test USDT you can mint (try everything here first)
//   npm run deploy -- --mainnet     Celo, real USDT
// Owner and operator: the bot wallet. Manager and fee recipient: Midas's personal wallet.
// The source is then verified on Sourcify (shows as verified on Blockscout and Celoscan).
import fs from 'node:fs'
import path from 'node:path'
import { formatEther } from 'viem'
import { CELO_USDT, PERSONAL, ROOT, art, confirm, errText, network, saveDeployment } from './lib.mjs'

const net = network(process.argv)
const bot = net.account.address
const gas = await net.pub.getBalance({ address: bot })
console.log(`\nNetwork:        ${net.name} (${net.chain.name})`)
console.log(`Bot wallet:     ${bot}  (key from ${net.keyFrom})  ${formatEther(gas)} CELO for gas`)
console.log(`Owner/operator: ${bot} (bot wallet)`)
console.log(`Manager/fees:   ${PERSONAL} (personal wallet)`)
console.log(`Asset:          ${net.name === 'mainnet' ? `USDT ${CELO_USDT}` : 'a new test USDT (tUSDT)'}`)
if (gas === 0n) {
  console.log(`\nThe bot wallet has no CELO for gas.${net.name === 'testnet' ? ' Get free test CELO at https://faucet.celo.org/celo-sepolia' : ''}`)
  process.exit(1)
}
if (!(await confirm('\nDeploy?'))) process.exit(0)

async function deploy(name, args) {
  const { abi, bytecode } = art(name)
  const hash = await net.wallet.deployContract({ abi, bytecode, args })
  const r = await net.pub.waitForTransactionReceipt({ hash, timeout: 180_000 })
  if (r.status !== 'success' || !r.contractAddress) throw new Error(`${name} deployment failed: ${hash}`)
  console.log(`${name}: ${r.contractAddress}`)
  return { address: r.contractAddress, block: Number(r.blockNumber) }
}

let usdt = CELO_USDT
if (net.name !== 'mainnet') usdt = (await deploy('MockUSDT', [])).address
const v = await deploy('FundVault', [usdt, bot, bot, PERSONAL, PERSONAL])
saveDeployment(net.name, { network: net.name, chainId: net.chain.id, vault: v.address, usdt, block: v.block, owner: bot, manager: PERSONAL, deployedAt: new Date().toISOString() })
console.log(`\nSaved to deployments/${net.name}.json`)

// Source verification on Sourcify: the compiler metadata plus the exact source files.
async function verify(name, address) {
  const meta = JSON.parse(art(name).metadata)
  const files = { 'metadata.json': art(name).metadata }
  for (const src of Object.keys(meta.sources)) {
    const local = src.startsWith('@') ? path.join(ROOT, 'node_modules', src) : path.join(ROOT, 'contracts', src)
    if (!fs.existsSync(local)) throw new Error(`missing source ${src} (run npm install to get the OpenZeppelin files)`)
    files[src] = fs.readFileSync(local, 'utf8')
  }
  const r = await fetch('https://sourcify.dev/server/verify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ address, chain: String(net.chain.id), files }),
  })
  const j = await r.json().catch(() => ({}))
  const status = j.result?.[0]?.status
  console.log(status ? `${name} verified on Sourcify (${status})` : `${name}: Sourcify said ${JSON.stringify(j).slice(0, 200)}`)
}
if (net.name !== 'local') try { await verify('FundVault', v.address) } catch (e) { console.log(`Verification skipped: ${errText(e)} (the contract works without it)`) }
if (net.name === 'testnet') try { await verify('MockUSDT', usdt) } catch { /* optional */ }

console.log(`\nExplorer: ${net.explorer}/address/${v.address}`)
console.log(`Next: npm run vault -- approve${net.name === 'mainnet' ? ' --mainnet' : ''}   (lets settlements pull payouts from the bot wallet)`)
console.log(`Then: npm run vault -- open live${net.name === 'mainnet' ? ' --mainnet' : ''}   (opening shares for your personal wallet at $1.00)`)
