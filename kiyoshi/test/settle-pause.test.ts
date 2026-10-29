import { test } from 'node:test'
import assert from 'node:assert/strict'
import { inSettlePause } from '../src/settle-pause.js'

test('no pause before the settle hour', () => {
  assert.equal(inSettlePause(new Date('2026-10-29T19:59:00Z'), 20, '2026-10-28'), false)
})
test('pause from the settle hour until today is settled', () => {
  assert.equal(inSettlePause(new Date('2026-10-29T20:00:00Z'), 20, '2026-10-28'), true)
  assert.equal(inSettlePause(new Date('2026-10-29T20:30:00Z'), 20, null), true)
  assert.equal(inSettlePause(new Date('2026-10-29T20:08:00Z'), 20, '2026-10-29'), false)
})
test('a missed settlement never pauses past midnight UTC', () => {
  assert.equal(inSettlePause(new Date('2026-10-30T00:00:00Z'), 20, '2026-10-28'), false)
})
