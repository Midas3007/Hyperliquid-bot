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

import { topUpSpend, usdtFor } from '../src/gas-math.js'
test('gas top-up: USDT needed for 5 CELO, with 2% headroom', () => {
  // 1 USDT buys 2 CELO -> 5 CELO needs 2.5 USDT, plus 2% = 2.55 USDT
  const need = usdtFor(5n * 10n ** 18n, 2n * 10n ** 18n)
  assert.ok(need >= 2_550_000n && need <= 2_550_001n)
})
test('gas top-up never spends more than the cap', () => {
  assert.equal(topUpSpend(2_550_001n, 500_000n), 500_000n)
  assert.equal(topUpSpend(400_000n, 500_000n), 400_000n)
})

import { settingFrom } from '../src/skip-reserved.js'
test('skipping is on unless Telegram switched it off', () => {
  assert.equal(settingFrom({ skip_reserved: false }), false)
  assert.equal(settingFrom({ skip_reserved: true }), true)
  assert.equal(settingFrom(null), true)
  assert.equal(settingFrom({}), true)
})
