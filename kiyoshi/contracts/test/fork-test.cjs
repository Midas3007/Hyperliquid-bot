// Fork test of KiyoshiArb against Textile's real reactor, validation contract, Permit2 and the real Uniswap pools.
const hre = require('hardhat'); const fs = require('fs')
const { createWalletClient, createPublicClient, custom, encodeAbiParameters, encodeFunctionData, parseAbi, keccak256, encodePacked, toHex, getAddress, erc20Abi } = require('viem')
const { privateKeyToAccount } = require('viem/accounts')
const { abi, bytecode } = JSON.parse(fs.readFileSync(__dirname + '/../KiyoshiArb.json'))
const REACTOR = '0xa9AA0a64769cBed4d3B1Ceb4Df01CdE915C235b3', VALIDATOR = '0x10B9EbA3a175Df418a35CB8329a527691EE258C5'
const PERMIT2 = '0x000000000022D473030F116dDEE9F6B43aC78BA3', ROUTER = '0x5615CDAb10dc425a742d643d949a7F474C01abc4', QUOTER = '0x82825d0554fA07f7FC52Ab63c961F330fdEFa8E8'
const USDT = '0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e', WARS = '0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D', CNGN = '0xF6829D7393dAe24509eb1E52eE8e572e2E271a4f'
const POOL_WARS = '0x5d8ef8b839be522b9e3d60a51edb5837cd0b2391', POOL_CNGN = '0x6519d56eb0a69fc0338657784c783b169b8f7d32'
const owner = privateKeyToAccount(require('viem/accounts').generatePrivateKey()), maker = privateKeyToAccount(require('viem/accounts').generatePrivateKey()), stranger = privateKeyToAccount(require('viem/accounts').generatePrivateKey())
let pass = 0, fail = 0
const ok = (c, m) => { if (c) { pass++; console.log('  ok  ', m) } else { fail++; console.log('  FAIL', m) } }
async function main() {
  const tr = custom(hre.network.provider)
  const pub = createPublicClient({ transport: tr })
  const w = (a) => createWalletClient({ account: a, transport: tr, chain: { id: 42220, name: 'celo', nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 }, rpcUrls: { default: { http: [] } } } })
  const rpc = (method, params) => hre.network.provider.request({ method, params })
  for (const a of [owner, maker, stranger]) await rpc('hardhat_setBalance', [a.address, '0x56BC75E2D63100000'])
  const bal = (t, a) => pub.readContract({ address: t, abi: erc20Abi, functionName: 'balanceOf', args: [a] })
  async function fund(token, from, to, amount) {
    await rpc('hardhat_impersonateAccount', [from]); await rpc('hardhat_setBalance', [from, '0x56BC75E2D63100000'])
    const h = await rpc('eth_sendTransaction', [{ from, to: token, data: encodeFunctionData({ abi: erc20Abi, functionName: 'transfer', args: [to, amount] }) }])
    await pub.waitForTransactionReceipt({ hash: h }); await rpc('hardhat_stopImpersonatingAccount', [from])
  }
  // deploy
  const dh = await w(owner).deployContract({ abi, bytecode, args: [owner.address, REACTOR, ROUTER, USDT] })
  const arb = (await pub.waitForTransactionReceipt({ hash: dh })).contractAddress
  console.log('deployed KiyoshiArb at', arb)
  await fund(USDT, POOL_WARS, arb, 1000_000000n)
  ok((await bal(USDT, arb)) === 1000_000000n, 'contract funded with 1,000 USDT')

  const quoterAbi = parseAbi(['function quoteExactInputSingle((address tokenIn,address tokenOut,uint256 amountIn,uint24 fee,uint160 sqrtPriceLimitX96)) returns (uint256 amountOut,uint160,uint32,uint256)'])
  const uniOut = async (tin, tout, amountIn) => (await pub.simulateContract({ address: QUOTER, abi: quoterAbi, functionName: 'quoteExactInputSingle', args: [{ tokenIn: tin, tokenOut: tout, amountIn, fee: 100, sqrtPriceLimitX96: 0n }] })).result[0]

  // A maker order like Textile's: maker gives `X`, receives `usdtOut` USDT; only `arb` may fill (validation contract)
  let nonceSeq = 1n
  async function makeOrder(X, xAmount, usdtOut, fillers) {
    const now = Number((await pub.getBlock()).timestamp), deadline = BigInt(now + 300)
    const info = { reactor: REACTOR, swapper: maker.address, nonce: 1606938044258990275541962092341162602522202995577000000000000n + nonceSeq++, deadline, additionalValidationContract: VALIDATOR,
      additionalValidationData: encodeAbiParameters([{ type: 'address[]' }, { type: 'uint256' }], [fillers, deadline]) }
    const input = { token: X, amount: xAmount, maxAmount: xAmount }, outputs = [{ token: USDT, amount: usdtOut, recipient: maker.address }]
    const signature = await maker.signTypedData({
      domain: { name: 'Permit2', chainId: 42220, verifyingContract: PERMIT2 },
      types: {
        PermitWitnessTransferFrom: [{ name: 'permitted', type: 'TokenPermissions' }, { name: 'spender', type: 'address' }, { name: 'nonce', type: 'uint256' }, { name: 'deadline', type: 'uint256' }, { name: 'witness', type: 'LimitOrder' }],
        TokenPermissions: [{ name: 'token', type: 'address' }, { name: 'amount', type: 'uint256' }],
        LimitOrder: [{ name: 'info', type: 'OrderInfo' }, { name: 'inputToken', type: 'address' }, { name: 'inputAmount', type: 'uint256' }, { name: 'outputs', type: 'OutputToken[]' }],
        OrderInfo: [{ name: 'reactor', type: 'address' }, { name: 'swapper', type: 'address' }, { name: 'nonce', type: 'uint256' }, { name: 'deadline', type: 'uint256' }, { name: 'additionalValidationContract', type: 'address' }, { name: 'additionalValidationData', type: 'bytes' }],
        OutputToken: [{ name: 'token', type: 'address' }, { name: 'amount', type: 'uint256' }, { name: 'recipient', type: 'address' }],
      },
      primaryType: 'PermitWitnessTransferFrom',
      message: { permitted: { token: X, amount: xAmount }, spender: REACTOR, nonce: info.nonce, deadline, witness: { info, inputToken: X, inputAmount: xAmount, outputs } },
    })
    const order = encodeAbiParameters([{ type: 'tuple', components: [
      { name: 'info', type: 'tuple', components: [{ name: 'reactor', type: 'address' }, { name: 'swapper', type: 'address' }, { name: 'nonce', type: 'uint256' }, { name: 'deadline', type: 'uint256' }, { name: 'additionalValidationContract', type: 'address' }, { name: 'additionalValidationData', type: 'bytes' }] },
      { name: 'input', type: 'tuple', components: [{ name: 'token', type: 'address' }, { name: 'amount', type: 'uint256' }, { name: 'maxAmount', type: 'uint256' }] },
      { name: 'outputs', type: 'tuple[]', components: [{ name: 'token', type: 'address' }, { name: 'amount', type: 'uint256' }, { name: 'recipient', type: 'address' }] }] }], [{ info, input, outputs }])
    return encodeFunctionData({ abi: parseAbi(['function executeBatch((bytes order,bytes sig)[] orders)']), functionName: 'executeBatch', args: [[{ order, sig: signature }]] })
  }
  async function setupMaker(X, pool, amount) {
    await fund(X, pool, maker.address, amount)
    const h = await w(maker).writeContract({ address: X, abi: erc20Abi, functionName: 'approve', args: [PERMIT2, 2n ** 255n] }); await pub.waitForTransactionReceipt({ hash: h })
  }
  const call = async (acct, fn, args) => {
    const data = encodeFunctionData({ abi, functionName: fn, args })
    try { await rpc('eth_call', [{ from: acct.address, to: arb, data }, 'latest']) } catch (e) {
      const raw = typeof e.data === 'string' ? e.data : e.data?.data; let name = e.message.slice(0, 200)
      if (typeof raw === 'string' && raw.length >= 10) { try { const { decodeErrorResult } = require('viem'); const x = decodeErrorResult({ abi, data: raw }); name = x.errorName + JSON.stringify(x.args ?? [], (k, v) => typeof v === 'bigint' ? v.toString() : v) } catch { name = 'raw ' + raw.slice(0, 200) } }
      return { ok: false, err: name } }
    const h = await w(acct).sendTransaction({ to: arb, data, gas: 3_000_000n }); const r = await pub.waitForTransactionReceipt({ hash: h }); return { ok: r.status === 'success', r }
  }
  const allowance = (t, s) => pub.readContract({ address: t, abi: erc20Abi, functionName: 'allowance', args: [arb, s] })

  for (const [X, pool, name] of [[WARS, POOL_WARS, 'wARS'], [CNGN, POOL_CNGN, 'cNGN']]) {
    console.log(`\n${name}`)
    const xAmt = name === 'wARS' ? 300n * 1750n * 10n ** 18n : 300n * 1440n * 10n ** 6n // about $300 of X
    await setupMaker(X, pool, xAmt * 4n)
    const back = await uniOut(X, USDT, xAmt) // what Uniswap pays for it now
    // 1. a gap of ~5 bps: maker asks 5 bps less than Uniswap pays
    const cheap = (back * 9995n) / 10000n
    const u0 = await bal(USDT, arb), m0 = await bal(USDT, maker.address)
    let r = await call(owner, 'textileThenUniswap', [await makeOrder(X, xAmt, cheap, [arb]), (cheap * 1005n) / 1000n, X, 100, 1n])
    const u1 = await bal(USDT, arb), m1 = await bal(USDT, maker.address)
    ok(r.ok, `profitable trade fills in one transaction ${r.err ?? ''}`)
    ok(u1 > u0, `contract USDT grew by $${(Number(u1 - u0) / 1e6).toFixed(4)} (paid maker $${(Number(m1 - m0) / 1e6).toFixed(2)}, includes any Textile fee)`)
    ok((await bal(X, arb)) === 0n, `no ${name} left in the contract`)
    ok((await allowance(USDT, REACTOR)) === 0n && (await allowance(X, ROUTER)) === 0n, 'allowances back to zero')
    if (r.ok) console.log('        gas used', r.r.gasUsed.toString())
    // 2. no gap: maker asks 10 bps MORE than Uniswap pays -> must revert, nothing moves
    const back2 = await uniOut(X, USDT, xAmt), dear = (back2 * 10010n) / 10000n
    const before = await bal(USDT, arb)
    r = await call(owner, 'textileThenUniswap', [await makeOrder(X, xAmt, dear, [arb]), (dear * 1005n) / 1000n, X, 100, 1n])
    ok(!r.ok && /NoProfit/.test(r.err ?? ''), `losing trade reverts (NoProfit) ${r.ok ? '' : ''}`)
    ok((await bal(USDT, arb)) === before, 'balance unchanged after the revert')
    // 3. minProfit is enforced: profitable by ~5 bps but asks for $5
    const back3 = await uniOut(X, USDT, xAmt), cheap3 = (back3 * 9995n) / 10000n
    r = await call(owner, 'textileThenUniswap', [await makeOrder(X, xAmt, cheap3, [arb]), (cheap3 * 1005n) / 1000n, X, 100, 5_000000n])
    ok(!r.ok && /NoProfit/.test(r.err ?? ''), 'reverts when profit is below minProfit')
    // 4. an order bound to someone else cannot be filled by the contract
    r = await call(owner, 'textileThenUniswap', [await makeOrder(X, xAmt, cheap3, [stranger.address]), (cheap3 * 1005n) / 1000n, X, 100, 1n])
    ok(!r.ok, `order bound to another taker is refused by Textile's validator`)
  }

  console.log('\naccess and safety')
  const anyOrder = await makeOrder(WARS, 10n ** 18n, 1n, [arb])
  let r = await call(stranger, 'textileThenUniswap', [anyOrder, 1n, WARS, 100, 0n]); ok(!r.ok && /NotOwner/.test(r.err ?? ''), 'a stranger cannot trade')
  r = await call(stranger, 'withdraw', [USDT, 1n]); ok(!r.ok && /NotOwner/.test(r.err ?? ''), 'a stranger cannot withdraw')
  r = await call(owner, 'textileThenUniswap', [encodeFunctionData({ abi: erc20Abi, functionName: 'transfer', args: [stranger.address, 1n] }), 0n, WARS, 100, 0n]); ok(!r.ok && /BadCall/.test(r.err ?? ''), 'only execute/executeBatch calls reach the reactor')
  r = await call(owner, 'setPaused', [true]); r = await call(owner, 'textileThenUniswap', [anyOrder, 1n, WARS, 100, 0n]); ok(!r.ok && /IsPaused/.test(r.err ?? ''), 'paused blocks trading')
  const o0 = await bal(USDT, owner.address), c0 = await bal(USDT, arb)
  r = await call(owner, 'withdraw', [USDT, 2n ** 256n - 1n]); ok(r.ok && (await bal(USDT, arb)) === 0n && (await bal(USDT, owner.address)) === o0 + c0, 'withdraw works while paused and returns everything to the owner')
  // EIP-1271
  const hash = keccak256(toHex('textile challenge'))
  const wrapped = keccak256(encodeAbiParameters([{ type: 'bytes32' }, { type: 'address' }, { type: 'uint256' }, { type: 'bytes32' }], [keccak256(toHex('KiyoshiArb')), arb, 42220n, hash]))
  const isValid = (h, s) => pub.readContract({ address: arb, abi, functionName: 'isValidSignature', args: [h, s] })
  ok((await isValid(hash, await owner.sign({ hash: wrapped }))) === '0x1626ba7e', 'EIP-1271: owner signature over the wrapped hash is valid')
  ok((await isValid(hash, await owner.sign({ hash }))) !== '0x1626ba7e', 'EIP-1271: owner signature over the raw hash is refused (no replay)')
  ok((await isValid(hash, await stranger.sign({ hash: wrapped }))) !== '0x1626ba7e', "EIP-1271: someone else's signature is refused")
  console.log(`\n${pass} passed, ${fail} failed`)
  process.exit(fail ? 1 : 0)
}
main().catch((e) => { console.error(e); process.exit(1) })
