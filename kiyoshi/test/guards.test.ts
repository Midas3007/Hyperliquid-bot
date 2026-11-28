import { test } from 'node:test'
import assert from 'node:assert/strict'
import { addSample, liquidityOk, priceMove, type Sample } from '../src/guard-math.js'

const M = 60_000
test('liquidity guard: needs history, then blocks below half the 7-day median', () => {
  const few: Sample[] = [{ t: 0, v: 100 }, { t: 10 * M, v: 100 }]
  assert.equal(liquidityOk(few, 20 * M, 1).ok, true) // too little history to judge
  const h: Sample[] = Array.from({ length: 10 }, (_, i) => ({ t: i * 10 * M, v: 100 }))
  assert.equal(liquidityOk(h, 100 * M, 60).ok, true)
  assert.equal(liquidityOk(h, 100 * M, 40).ok, false) // like 14 and 17 Nov (about 25%)
  assert.equal(liquidityOk(h, 100 * M, 1).normal, 100)
  assert.equal(liquidityOk(h, 8 * 86_400_000, 1).ok, true) // history older than 7 days is ignored
})

test('volatility guard: largest range over the last 10 minutes, including now', () => {
  const h: Sample[] = [{ t: 0, v: 1000 }, { t: 5 * M, v: 1002 }]
  assert.ok(Math.abs(priceMove(h, 6 * M, 1004) - 0.004) < 1e-12)
  assert.ok(priceMove(h, 6 * M, 1006) > 0.005) // 0.6%: blocked
  assert.equal(priceMove(h, 30 * M, 1100), 0) // old samples drop out
})

test('samples are thinned by gap and trimmed to the window', () => {
  let h: Sample[] = []
  h = addSample(h, { t: 0, v: 1 }, 10 * M, 60 * M)
  h = addSample(h, { t: 5 * M, v: 2 }, 10 * M, 60 * M) // too soon: skipped
  h = addSample(h, { t: 10 * M, v: 3 }, 10 * M, 60 * M)
  assert.deepEqual(h.map((s) => s.v), [1, 3])
  h = addSample(h, { t: 65 * M, v: 4 }, 10 * M, 60 * M)
  assert.deepEqual(h.map((s) => s.v), [3, 4])
})
