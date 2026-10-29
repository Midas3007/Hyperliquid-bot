// Only used by the tests: an in-memory chain. Contracts are compiled by scripts/build.mjs.
module.exports = { networks: { hardhat: { chainId: 31337, allowUnlimitedContractSize: false } }, paths: { cache: './cache' } }
