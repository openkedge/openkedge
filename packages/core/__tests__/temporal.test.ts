import { aggregateHistoricalMetric, evaluateTemporalRules } from '../../../src/core/governance/temporal'
import { InMemoryEventStore } from '../../../src/core/event/InMemoryEventStore'
import type { TemporalRule } from '../../../src/core/governance/types'
import { intent, NOW, record } from '../../../tests/helpers/temporal'

const rate: TemporalRule = { type: 'RATE_LIMIT', targetAction: 'send', windowMs: 600_000, maxCount: 5 }
const quota: TemporalRule = { type: 'SLIDING_WINDOW_QUOTA', targetAction: 'transfer', metricPath: 'intent.payload.amount',
  windowMs: 86_400_000, maxCumulativeValue: 500, scope: 'RESOURCE', resourcePath: 'accountId' }

test('rejects the sixth action in a ten-minute window, including the proposed action', async () => {
  const store = new InMemoryEventStore()
  for (let i = 0; i < 5; i++) await record(store, intent('send'), NOW - 10)
  expect(await evaluateTemporalRules(intent('send'), store, [rate], NOW)).toMatchObject({ decision: 'REJECT', budgets: [{ used: 5, requested: 1 }] })
})

test('uses (now-window, now], ignores failures, unrelated actions and actors, and trusts server time', async () => {
  const store = new InMemoryEventStore()
  await record(store, intent('send'), NOW - rate.windowMs)
  await record(store, intent('send'), NOW + 1)
  await record(store, intent('send'), NOW, 'FAILED')
  await record(store, intent('other'), NOW)
  await record(store, intent('send', {}, { metadata: { actor: 'other', timestamp: 0 } }), NOW)
  await record(store, intent('send'), NOW)
  const proposal = intent('send', {}, { metadata: { actor: 'agent-1', timestamp: 0 } })
  expect(await evaluateTemporalRules(proposal, store, [rate], NOW)).toMatchObject({ allowed: true, budgets: [{ used: 1 }] })
})

test('requires matching successful A before B and binds its hash and expiry', async () => {
  const store = new InMemoryEventStore()
  const rule: TemporalRule = { type: 'PRECEDING_EVENT_REQUIRED', targetAction: 'B', requiredPrecedingAction: 'A', windowMs: 900_000,
    matches: [{ currentPath: 'payload.resourceId', historicalPath: 'intent.payload.resourceId' }] }
  const current = intent('B', { resourceId: 'dev' })
  expect((await evaluateTemporalRules(current, store, [rule], NOW)).allowed).toBe(false)
  await record(store, intent('A', { resourceId: 'prod' }), NOW)
  await record(store, intent('A', { resourceId: 'dev' }), NOW, 'FAILED')
  await record(store, intent('A', { resourceId: 'dev' }), NOW - rule.windowMs)
  expect((await evaluateTemporalRules(current, store, [rule], NOW)).allowed).toBe(false)
  const source = await record(store, intent('A', { resourceId: 'dev' }), NOW - 100)
  expect(await evaluateTemporalRules(current, store, [rule], NOW)).toMatchObject({ allowed: true, preconditionHashes: [source.hash], validBefore: NOW - 100 + rule.windowMs })
})

test('resource quotas span actors, isolate accounts, and include the incoming value', async () => {
  const store = new InMemoryEventStore()
  await record(store, intent('transfer', { amount: 150, accountId: 'shared' }), NOW)
  await record(store, intent('transfer', { amount: 1_000, accountId: 'other' }), NOW)
  const current = intent('transfer', { amount: 350, accountId: 'shared' }, { metadata: { actor: 'agent-2', timestamp: NOW } })
  expect((await evaluateTemporalRules(current, store, [quota], NOW)).allowed).toBe(true)
  current.payload = { amount: 351, accountId: 'shared' }
  expect(await evaluateTemporalRules(current, store, [quota], NOW)).toMatchObject({ allowed: false, budgets: [{ used: 150, requested: 351, limit: 500 }] })
  expect(await aggregateHistoricalMetric(store, 'agent-1', 'transfer', 'intent.payload.amount', 86_400_000, NOW)).toBe(1_150)
})

test('unresolved reservations never age out or double count after completion', async () => {
  const store = new InMemoryEventStore()
  const pending = intent('transfer', { amount: 400, accountId: 'shared' })
  await record(store, pending, NOW - 100_000_000, 'RESERVED')
  const next = intent('transfer', { amount: 150, accountId: 'shared' })
  expect((await evaluateTemporalRules(next, store, [quota], NOW)).allowed).toBe(false)
  await record(store, pending, NOW)
  expect((await evaluateTemporalRules(next, store, [quota], NOW)).budgets[0].used).toBe(400)
})

test.each([undefined, '10', -1, NaN, Infinity])('rejects malformed or negative metrics: %s', async amount => {
  const result = await evaluateTemporalRules(intent('transfer', { amount, accountId: 'shared' }), new InMemoryEventStore(), [quota], NOW)
  expect(result).toMatchObject({ allowed: false })
  expect(result.reasons.join()).toContain('TEMPORAL_INVALID_ERROR')
})

test('fails closed on invalid historical metrics and unknown rule types', async () => {
  const store = new InMemoryEventStore()
  await record(store, intent('transfer', { amount: -10, accountId: 'shared' }))
  expect((await evaluateTemporalRules(intent('transfer', { amount: 1, accountId: 'shared' }), store, [quota], NOW)).allowed).toBe(false)
  expect((await evaluateTemporalRules(intent('send'), store, [{ ...rate, type: 'UNKNOWN' } as unknown as TemporalRule], NOW)).allowed).toBe(false)
})
