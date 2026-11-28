# KiyoshiArb

One-transaction Textile-first arbitrage for the Kiyoshi bot (fix 3 in `fund-site/docs/earnings-investigation.pdf`).
The bot asks Textile for a firm quote with this contract as the taker, then calls `textileThenUniswap`, which fills the
quote, sells the coins on Uniswap and reverts unless the contract ends with at least `minProfit` more USDT.

- Owner: the bot wallet (fixed at deployment). Only it can trade, pause or withdraw; withdrawals go only to it.
- Textile proves control of a contract taker through EIP-1271; `isValidSignature` accepts only the owner's signature
  over `keccak256(abi.encode(keccak256("KiyoshiArb"), contract, chainId, hash))`.

Build and test (Node, from this folder; the test forks Celo mainnet through forno):

    npm i solc@0.8.30 hardhat@2.26.3 viem@2.21.0
    node compile.cjs
    npx hardhat run test/fork-test.cjs --config hardhat.config.cjs

The fork test builds maker orders exactly like Textile's (same reactor, validation contract `0x10B9…58C5` with the
allowed-taker list, Permit2 witness signature) and runs them against the real Uniswap wARS and cNGN pools: profitable
fills, losing fills reverting, minProfit, orders bound to another taker, access control, pause, withdraw and EIP-1271.
