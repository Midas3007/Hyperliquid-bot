import { createPublicClient, createWalletClient, concat, http, type Address, type Hex, type TransactionReceipt } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'
import { celo } from 'viem/chains'
import { toDataSuffix } from '@celo/attribution-tags'
import { ATTRIBUTION_TAG, RPC_URL } from './config.js'
import { parsePrivateKey } from './keys.js'

const pk = parsePrivateKey(process.env.AGENT_PRIVATE_KEY)

export const account = privateKeyToAccount(pk)
export const pub = createPublicClient({ chain: celo, transport: http(RPC_URL) })
const wallet = createWalletClient({ account, chain: celo, transport: http(RPC_URL) })

export const TAG_SUFFIX = toDataSuffix(ATTRIBUTION_TAG) as Hex

// Block of the agent's most recent transaction. Reads must not come from a node behind this.
export let lastTxBlock = 0n

// The ONLY way the agent sends transactions, so nothing ever goes out untagged.
export type Fees = { maxFeePerGas?: bigint; maxPriorityFeePerGas?: bigint; gas?: bigint }
// One transaction at a time: the main loop and the atomic watcher (atomic.ts) share this wallet, and two sends at once
// could pick the same nonce. The lock covers sending and the receipt.
let queue: Promise<unknown> = Promise.resolve()
export function sendTaggedReceipt(tx: { to: Address; data?: Hex; value?: bigint }, fees: Fees = {}): Promise<TransactionReceipt> {
  const run = queue.then(() => sendNow(tx, fees))
  queue = run.catch(() => {})
  return run
}
async function sendNow(tx: { to: Address; data?: Hex; value?: bigint }, fees: Fees): Promise<TransactionReceipt> {
  const data = concat([tx.data ?? '0x', TAG_SUFFIX])
  const hash = await wallet.sendTransaction({ to: tx.to, data, value: tx.value ?? 0n, ...fees })
  const receipt = await pub.waitForTransactionReceipt({ hash })
  if (receipt.status !== 'success') throw new Error(`Transaction reverted: ${hash}`)
  if (receipt.blockNumber > lastTxBlock) lastTxBlock = receipt.blockNumber
  return receipt
}

export async function sendTagged(tx: { to: Address; data?: Hex; value?: bigint }): Promise<Hex> {
  return (await sendTaggedReceipt(tx)).transactionHash
}
