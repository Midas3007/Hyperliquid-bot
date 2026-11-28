import { test } from 'node:test'
import assert from 'node:assert/strict'
import { entryMin, execMin, floatAction, sizesFor } from '../src/atomic-math.js'

const $ = (d: number) => BigInt(Math.round(d * 1e6))
test('sizes: a spread up to the float, whole dollars, nothing under $20', () => {
  assert.deepEqual(sizesFor($(300)), [$(75), $(150), $(225), $(294)])
  assert.deepEqual(sizesFor($(1000)), [$(250), $(500), $(750), $(980)])
  assert.deepEqual(sizesFor($(40)), [$(20), $(30), $(39)]) // 25% of $40 = $10 is too small
  assert.deepEqual(sizesFor($(10)), [])
})
test('entry minimum: 1 bp of the size, at least $0.02; execution floor is half a bp, never zero', () => {
  assert.equal(entryMin($(300)), $(0.03))
  assert.equal(entryMin($(100)), $(0.02))
  assert.equal(execMin($(300)), $(0.015))
  assert.equal(execMin(1n), 1n)
})
test('float: top up to the target, keep half the wallet, skim profit above target + $25', () => {
  assert.deepEqual(floatAction(0n, $(300), $(600)), { kind: 'top-up', amount: $(300) })
  assert.deepEqual(floatAction(0n, $(300), $(400)), { kind: 'top-up', amount: $(200) }) // half of the wallet's $400
  assert.equal(floatAction($(280), $(300), $(600)), null) // within 10%
  assert.equal(floatAction($(320), $(300), $(600)), null) // under the $25 slack
  assert.deepEqual(floatAction($(330), $(300), $(600)), { kind: 'skim', amount: $(30) })
  assert.equal(floatAction(0n, $(300), $(30)), null) // $15 spare is below the $20 minimum
})
