import { test } from 'node:test'
import assert from 'node:assert/strict'
import { setup, usd, sh } from './helpers.mjs'

const near = (a, b, tol = 2n) => assert.ok((a > b ? a - b : b - a) <= tol, `${a} != ${b}`)
const C = { Owner: 0, Operator: 1, Manager: 2, FeeRecipient: 3, FeeBps: 4, MaxNavChange: 5 }

// A fund opened with Midas's $117.28 at $1.00, the bot holding the money, two allowed members.
async function opened() {
  const t = await setup()
  const { bot, personal, alice, bob, usdt, vault, v, u } = t
  await u(bot, 'mint', [bot, usd(117.28)]) // the bot wallet's trading money
  await v(bot, 'openingShares', [personal, usd(117.28)])
  await v(personal, 'setLimits', [usd(10000), usd(1)])
  for (const m of [alice, bob]) {
    await v(personal, 'setAllowed', [m, true])
    await u(bot, 'mint', [m, usd(1000)])
    await u(m, 'approve', [vault, usd(1000000)])
  }
  await u(bot, 'approve', [vault, usd(1000000)]) // the operator funds redemptions
  return t
}

test('opening shares: $117.28 becomes 117.28 shares at $1.00', async () => {
  const { personal, read, v, bot, fails } = await opened()
  assert.equal(await read('balanceOf', [personal]), sh(117.28))
  assert.equal(await read('sharePrice'), usd(1))
  assert.equal(await read('nav'), usd(117.28))
  await fails(v(bot, 'openingShares', [personal, usd(1)]), 'AlreadyOpened')
})

test('a deposit waits for settlement, then buys shares at that day\'s price; money goes to the bot', async () => {
  const { alice, bot, vault, v, read, bal } = await opened()
  await v(alice, 'requestDeposit', [usd(100)])
  assert.equal(await bal(vault), usd(100))
  assert.equal(await read('balanceOf', [alice]), 0n)
  // the fund made 10% before the settlement: alice buys at $1.10
  await v(bot, 'settle', [usd(129.008)])
  near(await read('balanceOf', [alice]), sh(100) * 10n / 11n, 10n ** 7n)
  assert.equal(await read('nav'), usd(229.008))
  assert.equal(await bal(vault), 0n)
  assert.equal(await bal(bot), usd(217.28))
  assert.equal(await read('cost', [alice]), usd(100))
})

test('redemption: fee only on the member\'s own profit, kept as the manager\'s shares; price unchanged', async () => {
  const { alice, bot, personal, v, read, bal } = await opened()
  await v(alice, 'requestDeposit', [usd(100)])
  await v(bot, 'settle', [usd(117.28)]) // alice buys 100 shares at $1.00
  await v(bot, 'reportValue', [usd(260.736)]) // +20%
  const priceBefore = await read('sharePrice')
  const aliceShares = await read('balanceOf', [alice])
  await v(alice, 'requestRedeem', [aliceShares])
  const before = await bal(alice)
  await v(bot, 'settle', [usd(260.736)])
  // gross 120, profit 20, fee 2, paid 118
  near((await bal(alice)) - before, usd(118), 2n)
  assert.equal(await read('balanceOf', [alice]), 0n)
  assert.equal(await read('cost', [alice]), 0n)
  near(await read('balanceOf', [personal]), sh(117.28) + sh(2) * 10n / 12n, 10n ** 7n) // fee shares at $1.20
  near(await read('sharePrice'), priceBefore, 2n)
  near(await read('nav'), usd(142.736), 2n)
})

test('the fee recipient pays no fee', async () => {
  const { personal, bot, v, read, bal } = await opened()
  await v(bot, 'reportValue', [usd(140.736)]) // +20%
  await v(personal, 'requestRedeem', [sh(10)])
  await v(bot, 'settle', [usd(140.736)])
  near(await bal(personal), usd(12), 2n)
})

test('partial redemption carries the matching part of the cost', async () => {
  const { alice, bot, v, read } = await opened()
  await v(alice, 'requestDeposit', [usd(100)])
  await v(bot, 'settle', [usd(117.28)])
  await v(alice, 'requestRedeem', [sh(25)])
  await v(bot, 'settle', [usd(117.28)])
  near(await read('cost', [alice]), usd(75), 1n)
})

test('members who join at different prices each pay only for what they bought', async () => {
  const { alice, bob, bot, v, read } = await opened()
  await v(alice, 'requestDeposit', [usd(100)])
  await v(bot, 'settle', [usd(117.28)]) // $1.00
  await v(bob, 'requestDeposit', [usd(100)])
  await v(bot, 'settle', [usd(325.92)]) // +50%: $1.50
  const [a, b, s, nav] = await Promise.all([read('balanceOf', [alice]), read('balanceOf', [bob]), read('totalSupply'), read('nav')])
  near(a * nav / s, usd(150), 2n)
  near(b * nav / s, usd(100), 2n)
})

test('shares cannot be transferred between wallets', async () => {
  const { personal, alice, v, fails } = await opened()
  await fails(v(personal, 'transfer', [alice, sh(1)]), 'NonTransferable')
})

test('only allowed wallets can deposit, within the cap and minimum', async () => {
  const { alice, bob, carol, personal, v, u, bot, vault, fails } = await opened()
  await u(bot, 'mint', [carol, usd(100)]); await u(carol, 'approve', [vault, usd(100)])
  await fails(v(carol, 'requestDeposit', [usd(10)]), 'NotAllowed')
  await fails(v(alice, 'requestDeposit', [usd(0.5)]), 'BadAmount')
  await v(personal, 'setLimits', [usd(200), usd(1)])
  await v(alice, 'requestDeposit', [usd(80)])
  await fails(v(bob, 'requestDeposit', [usd(10)]), 'CapReached')
  await fails(v(alice, 'setAllowed', [carol, true]), 'NotAuthorized')
})

test('deposits and redemptions can be cancelled before settlement, even while paused', async () => {
  const { alice, bot, personal, v, read, bal, fails } = await opened()
  await v(alice, 'requestDeposit', [usd(100)])
  await v(personal, 'requestRedeem', [sh(10)])
  await v(bot, 'setPaused', [true])
  await fails(v(bot, 'settle', [usd(117.28)]), 'IsPaused')
  await fails(v(alice, 'requestDeposit', [usd(5)]), 'IsPaused')
  await v(alice, 'cancelDeposit')
  await v(personal, 'cancelRedeem')
  assert.equal(await bal(alice), usd(1000))
  assert.equal(await read('balanceOf', [personal]), sh(117.28))
  await fails(v(personal, 'setPaused', [false]), 'NotAuthorized')
  await v(bot, 'setPaused', [false])
  await v(bot, 'settle', [usd(117.28)])
  assert.equal(await read('balanceOf', [alice]), 0n)
})

test('a payout that cannot be sent waits as claimable and does not block others', async () => {
  const { alice, bob, bot, usdt, v, u, read, bal } = await opened()
  await v(alice, 'requestDeposit', [usd(100)]); await v(bob, 'requestDeposit', [usd(100)])
  await v(bot, 'settle', [usd(117.28)])
  await v(alice, 'requestRedeem', [sh(50)]); await v(bob, 'requestRedeem', [sh(50)])
  await u(bot, 'setBlocked', [alice, true])
  const bobBefore = await bal(bob)
  await v(bot, 'settle', [usd(317.28)])
  near((await bal(bob)) - bobBefore, usd(50), 1n)
  near(await read('claimable', [alice]), usd(50), 1n)
  await u(bot, 'setBlocked', [alice, false])
  await v(alice, 'claim')
  near(await bal(alice), usd(950), 1n)
})

test('the reported value may move at most 50% a day, however often it is reported', async () => {
  const { bot, v, fails, warp } = await opened()
  await fails(v(bot, 'reportValue', [usd(180)]), 'ValueJump') // +53%
  await v(bot, 'reportValue', [usd(170)]) // +45%
  await fails(v(bot, 'reportValue', [usd(180)]), 'ValueJump') // still +53% on the day
  await warp(86400)
  await v(bot, 'reportValue', [usd(250)]) // +47% on the new day
  await fails(v(bot, 'reportValue', [0n]), 'ValueJump')
})

test('an empty fund cannot be given a value', async () => {
  const { bot, v, fails } = await setup()
  await fails(v(bot, 'settle', [usd(5)]), 'ValueJump')
  await v(bot, 'settle', [0n])
})

test('roles and limits: only the right wallet, fees capped at 20%, changes wait 48 hours', async () => {
  const { bot, personal, alice, v, read, fails, warp } = await opened()
  await fails(v(personal, 'queueChange', [C.Manager, BigInt(alice)]), 'NotAuthorized')
  await fails(v(bot, 'queueChange', [C.FeeBps, 1500n]), 'NotAuthorized')
  await fails(v(personal, 'queueChange', [C.FeeBps, 2001n]), 'BadAmount')
  await fails(v(alice, 'setPaused', [true]), 'NotAuthorized')
  await fails(v(alice, 'reportValue', [usd(117.28)]), 'NotAuthorized')
  await fails(v(personal, 'settle', [usd(117.28)]), 'NotAuthorized')

  await v(personal, 'queueChange', [C.FeeBps, 1500n])
  await fails(v(alice, 'executeChange', [C.FeeBps]), 'TooEarly')
  await warp(48 * 3600)
  await v(alice, 'executeChange', [C.FeeBps]) // anyone can apply it once the delay is over
  assert.equal(await read('feeBps'), 1500n)

  await v(bot, 'queueChange', [C.Manager, BigInt(alice)])
  await fails(v(personal, 'cancelChange', [C.Manager]), 'NotAuthorized')
  await v(bot, 'cancelChange', [C.Manager])
  await fails(v(bot, 'executeChange', [C.Manager]), 'NothingQueued')

  await v(bot, 'queueChange', [C.Manager, BigInt(alice)])
  await warp(48 * 3600)
  await v(bot, 'executeChange', [C.Manager])
  assert.equal((await read('manager')).toLowerCase(), alice.toLowerCase())
  await fails(v(bot, 'queueChange', [C.Owner, 0n]), 'BadAmount')
})

test('redemptions larger than new deposits are topped up from the bot wallet', async () => {
  const { alice, bob, bot, v, read, bal } = await opened()
  await v(alice, 'requestDeposit', [usd(100)])
  await v(bot, 'settle', [usd(117.28)])
  await v(bob, 'requestDeposit', [usd(30)])
  await v(alice, 'requestRedeem', [sh(100)])
  const botBefore = await bal(bot)
  await v(bot, 'settle', [usd(217.28)])
  near(botBefore - (await bal(bot)), usd(70), 1n)
  near(await read('nav'), usd(147.28), 1n)
})
