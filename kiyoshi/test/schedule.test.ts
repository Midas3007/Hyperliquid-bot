import { test } from 'node:test'
import assert from 'node:assert/strict'
import { lagosTime, minEdgeFor, scheduleTable, smartProfile } from '../src/schedule.js'

// Lagos is UTC+1: 12:30 UTC is 13:30 Lagos
const at = (iso: string) => new Date(iso)

test('Lagos time is UTC+1, including across midnight', () => {
  assert.deepEqual(lagosTime(at('2026-11-02T12:30:00Z')), { dow: 1, hour: 13 }) // Monday
  assert.deepEqual(lagosTime(at('2026-11-01T23:30:00Z')), { dow: 1, hour: 0 }) // Sunday 23:30 UTC is Monday 00:30 Lagos
})

test('peak hours size up by weekday, bounded by the ceiling', () => {
  const mon = smartProfile(at('2026-11-02T12:30:00Z'), 330, 800)
  assert.equal(mon.window, 'peak'); assert.equal(mon.capUsd, 660)
  assert.equal(smartProfile(at('2026-11-03T13:10:00Z'), 330, 800).capUsd, 495) // Tuesday 14:10 Lagos, 1.5x
  assert.equal(smartProfile(at('2026-11-01T12:00:00Z'), 330, 800).capUsd, 412.5) // Sunday, 1.25x
  assert.equal(smartProfile(at('2026-11-06T12:00:00Z'), 500, 800).capUsd, 800) // Friday 2x = 1000, capped
  assert.equal(smartProfile(at('2026-11-06T12:00:00Z'), 900, 800).capUsd, 900) // never below MAX_TRADE_USD
})

test('outside the peak the size stays at MAX_TRADE_USD and the minimum edge rises', () => {
  const busy = smartProfile(at('2026-11-02T19:30:00Z'), 330, 800) // Monday 20:30 Lagos
  assert.equal(busy.window, 'busy'); assert.equal(busy.capUsd, 330)
  const quiet = smartProfile(at('2026-11-02T16:00:00Z'), 330, 800) // Monday 17:00 Lagos
  assert.equal(quiet.window, 'quiet')
  assert.equal(smartProfile(at('2026-11-01T19:30:00Z'), 330, 800).window, 'quiet') // Sunday 20:30: weekend, not busy
  assert.equal(smartProfile(at('2026-11-01T13:30:00Z'), 330, 800).window, 'peak') // Sunday 14:30: peak every day
})

test('minimum edge is a share of the trade with a dollar floor', () => {
  const quiet = smartProfile(at('2026-11-02T16:00:00Z'), 330, 800)
  assert.ok(Math.abs(minEdgeFor(330, quiet) - 0.0495) < 1e-9) // 1.5 bps of $330
  assert.equal(minEdgeFor(82.5, quiet), 0.03) // the floor for small trades
  const peak = smartProfile(at('2026-11-02T12:30:00Z'), 330, 800)
  assert.ok(Math.abs(minEdgeFor(660, peak) - 0.0198) < 1e-9)
})

test('the table names every window', () => {
  const t = scheduleTable(330, 800)
  assert.match(t, /Peak 13:00–14:59/); assert.match(t, /Mon \$660/); assert.match(t, /Quiet/)
})

import { CAREFUL_CAP_USD, CAREFUL_CORRIDORS, carefulLimits, windowAt } from '../src/schedule.js'

test('careful mode: higher minimum edge, never looser than normal, $500 cap outside the peak', () => {
  const normal = (s: number) => Math.max(0.01, (s * 0.3) / 10_000) // the old peak rule
  const peak = carefulLimits(at('2026-11-23T12:30:00Z'), normal, 1000) // Monday 13:30 Lagos
  assert.equal(peak.window, 'peak'); assert.equal(peak.capUsd, 1000) // the peak keeps its size
  assert.equal(peak.minEdge(500), 0.1) // 2 bps of $500
  assert.equal(peak.minEdge(100), 0.05) // the $0.05 floor
  const quiet = carefulLimits(at('2026-11-23T14:30:00Z'), normal, 750) // Monday 15:30 Lagos
  assert.equal(quiet.window, 'quiet'); assert.equal(quiet.capUsd, CAREFUL_CAP_USD)
  assert.equal(quiet.minEdge(500), 0.2) // 4 bps of $500
  const busy = carefulLimits(at('2026-11-23T19:30:00Z'), () => 5, 300) // a stricter normal rule wins
  assert.equal(busy.window, 'busy'); assert.equal(busy.capUsd, 300); assert.equal(busy.minEdge(300), 5)
  assert.equal(windowAt(at('2026-11-22T19:30:00Z')), 'quiet') // Sunday 20:30: weekend
  assert.deepEqual(CAREFUL_CORRIDORS, ['cNGN', 'USDC', 'wARS'])
})
