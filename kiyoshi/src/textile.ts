import crypto from 'node:crypto'
import { encodeAbiParameters, hashTypedData, keccak256, toHex, type Address } from 'viem'
import { account } from './wallet.js'
import { CHAIN_ID, TEXTILE_API } from './config.js'

const V2 = `${TEXTILE_API}/v2`
// Partner API key (Render env TEXTILE_API_KEY, never in the repo): own rate limits, and Textile skips the proof of
// control because the key is accountable for the taker. Without it, the proof below is signed as before.
const API_KEY = process.env.TEXTILE_API_KEY?.trim() || null
export const textileKeyed = !!API_KEY
const JSON_HEADERS: Record<string, string> = { 'Content-Type': 'application/json', ...(API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {}) }

export async function getTicker(id = 'USDT_NGN') {
  const r = await fetch(`${TEXTILE_API}/tickers?ticker_id=${id}`)
  if (!r.ok) throw new Error(`tickers ${r.status}`)
  const [t] = await r.json()
  return t as { last_price: string; bid: string; ask: string; base_volume: string }
}

const TAKER_CONTROL = {
  TakerControl: [
    { name: 'taker', type: 'address' },
    { name: 'chainId', type: 'uint256' },
    { name: 'nonce', type: 'bytes32' },
    { name: 'issuedAt', type: 'uint256' },
  ],
} as const

// Proof of control for `taker`: the bot wallet itself (EIP-712, ecrecover), or the KiyoshiArb contract, which Textile
// checks with EIP-1271. The contract accepts only the owner's signature over its own wrapping of the digest
// (contracts/KiyoshiArb.sol, isValidSignature), so this signs that wrapping, never the bare digest.
async function signTakerControl(taker: Address = account.address) {
  const nonce = toHex(crypto.randomBytes(32))
  const issuedAt = Date.now()
  const typed = {
    domain: { name: 'Textile Taker Control', version: '1', chainId: CHAIN_ID },
    types: TAKER_CONTROL, primaryType: 'TakerControl' as const,
    message: { taker, chainId: BigInt(CHAIN_ID), nonce, issuedAt: BigInt(issuedAt) },
  }
  if (taker.toLowerCase() === account.address.toLowerCase()) return { nonce, issuedAt, signature: await account.signTypedData(typed) }
  const digest = hashTypedData(typed)
  const wrapped = keccak256(encodeAbiParameters([{ type: 'bytes32' }, { type: 'address' }, { type: 'uint256' }, { type: 'bytes32' }],
    [keccak256(toHex('KiyoshiArb')), taker, BigInt(CHAIN_ID), digest]))
  return { nonce, issuedAt, signature: await account.sign({ hash: wrapped }) }
}

async function unwrap(res: Response) {
  const body: any = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(`Textile ${res.status}: ${JSON.stringify(body.error ?? body)}`)
  return body.data
}

type Leg = { sellToken: Address; buyToken: Address; sellAmount: string }

export async function preview(leg: Leg) {
  const res = await fetch(`${V2}/rfq/preview`, {
    method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ chainId: CHAIN_ID, ...leg }), signal: AbortSignal.timeout(10_000),
  })
  return unwrap(res)
}

// Firm quote. Tries unsigned first (12h grant), signs proof of control only when asked.
export async function requestQuote(leg: Leg, taker: Address = account.address) {
  const send = (extra: object = {}) =>
    fetch(`${V2}/rfq/request`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ chainId: CHAIN_ID, ...leg, taker, ...extra }),
      signal: AbortSignal.timeout(75_000),
    })
  let res = await send()
  if (res.status === 401 && !API_KEY) {
    const body: any = await res.clone().json().catch(() => ({}))
    if (body?.error?.details?.reason === 'proof_of_control_required') {
      res = await send({ takerProof: await signTakerControl(taker) })
    }
  }
  return unwrap(res)
}

export async function cancelRfq(rfqId: string, claimToken: string) {
  await fetch(`${V2}/rfq/${rfqId}/cancel`, { method: 'POST', headers: { ...JSON_HEADERS, 'X-Rfq-Claim': claimToken } }).catch(() => {})
}

export async function submitTx(rfqId: string, claimToken: string, txHash: string) {
  const res = await fetch(`${V2}/rfq/${rfqId}/submit`, {
    method: 'POST', headers: { ...JSON_HEADERS, 'X-Rfq-Claim': claimToken }, body: JSON.stringify({ txHash }),
  })
  return unwrap(res)
}

export async function getRfq(rfqId: string, claimToken: string) {
  const res = await fetch(`${V2}/rfq/${rfqId}`, { headers: { ...JSON_HEADERS, 'X-Rfq-Claim': claimToken } })
  return unwrap(res)
}
