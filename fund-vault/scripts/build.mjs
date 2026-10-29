// Compile the contracts with the official Solidity compiler (solc-js) into build/*.json (abi + bytecode).
// The compiled files are committed, so the phone does not need the compiler: `npm run build` only to rebuild.
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const solc = require('solc')
const root = path.dirname(path.dirname(new URL(import.meta.url).pathname))
const sources = {}
for (const f of fs.readdirSync(path.join(root, 'contracts'))) if (f.endsWith('.sol')) sources[f] = { content: fs.readFileSync(path.join(root, 'contracts', f), 'utf8') }
const input = {
  language: 'Solidity', sources,
  settings: { optimizer: { enabled: true, runs: 200 }, viaIR: true, evmVersion: 'paris', outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object', 'metadata'] } } },
}
const findImports = (p) => {
  try { return { contents: fs.readFileSync(require.resolve(p), 'utf8') } } catch { return { error: 'not found: ' + p } }
}
const out = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }))
const errors = (out.errors || []).filter((e) => e.severity === 'error')
for (const e of out.errors || []) console.error(e.formattedMessage)
if (errors.length) process.exit(1)
fs.mkdirSync(path.join(root, 'build'), { recursive: true })
for (const [file, contracts] of Object.entries(out.contracts)) {
  if (!sources[file]) continue
  for (const [name, c] of Object.entries(contracts)) {
    fs.writeFileSync(path.join(root, 'build', name + '.json'), JSON.stringify({ abi: c.abi, bytecode: '0x' + c.evm.bytecode.object, metadata: c.metadata }, null, 1))
    console.log(`built ${name}: ${c.evm.bytecode.object.length / 2} bytes`)
  }
}
