import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ExecutionGateway, MockTerminationAdapter } from '../src/gateway/Gateway'
import { loadGatewayConfig, type GatewayIdentity } from '../src/gateway/config'
import { validatePolicy, type GatewayPolicy, type PolicySource } from '../src/gateway/policy'
import { InMemoryIEECStore } from '../src/core/event/InMemoryIEECStore'
import { SQLiteIEECStore } from '../src/core/event/SqlEventStore'
import { EventType } from '../src/interfaces/contracts'

const key = '0123456789abcdef0123456789abcdef'
const allowed = 'i-aaaaaaaaaaaaaaaaa'
const actual = { instanceId: allowed, skipOsShutdown: false }
const caller = (callerId: string): GatewayIdentity => ({ gatewayId: 'gateway-one', callerId, delegatedBy: 'operator-one' })
const policy: GatewayPolicy = { protocolVersion: 1, version: 'v1', allowedInstanceIds: [allowed],
  protectedInstanceIds: [], allowSkipOsShutdown: false,
  instances: { [allowed]: { state: 'running', tags: { env: 'dev', critical: 'false' } } }, rules: [] }
const source: PolicySource = { current: async () => validatePolicy(policy) }

test('ordinary startup requires a strong explicit signing key and all launcher identities', () => {
  const base = { OKG_GATEWAY_ID: 'gateway-one', OKG_CALLER_ID: 'agent-one', OKG_DELEGATED_BY: 'operator-one' }
  expect(() => loadGatewayConfig(base)).toThrow('OKG_SIGNING_KEY_HEX')
  expect(() => loadGatewayConfig({ ...base, OKG_SIGNING_KEY_HEX: '' })).toThrow('OKG_SIGNING_KEY_HEX')
  expect(() => loadGatewayConfig({ ...base, OKG_SIGNING_KEY_HEX: 'not-hex' })).toThrow('OKG_SIGNING_KEY_HEX')
  expect(() => loadGatewayConfig({ ...base, OKG_SIGNING_KEY_HEX: '00'.repeat(32) })).toThrow('Signing key is weak')
  expect(() => loadGatewayConfig({ ...base, OKG_SIGNING_KEY_HEX: 'd3d0e5b7186396cfb401046ec9f9526c0fca6d8571a1f52fe66a7380cef385d5' })).toThrow('Demo signing key')
  expect(() => loadGatewayConfig({ ...base, OKG_CALLER_ID: '', OKG_SIGNING_KEY_HEX: 'a4f70d539aecc638da2180f97edb0d456eb78a2900fed627b658aee4576b61d9' })).toThrow('callerId')
  const valid = loadGatewayConfig({ ...base, OKG_SIGNING_KEY_HEX: 'a4f70d539aecc638da2180f97edb0d456eb78a2900fed627b658aee4576b61d9' })
  expect(valid.callerId).toBe('agent-one')
})

test('grant and replay are confined to the launcher-attested caller and delegator', async () => {
  const store = new InMemoryIEECStore()
  const original = new ExecutionGateway(source, new MockTerminationAdapter(), key, caller('agent-one'), store)
  const otherAdapter = new MockTerminationAdapter()
  const other = new ExecutionGateway(source, otherAdapter, key, caller('agent-two'), store)
  const admitted = await original.admit(actual)
  if (admitted.status !== 'allowed') throw new Error('Expected grant')
  expect((await other.execute(admitted.grant, actual)).code).toBe('CALLER_MISMATCH')
  await expect(other.replay(admitted.intentId)).rejects.toThrow('EVIDENCE_ACCESS_DENIED')
  expect(otherAdapter.calls).toHaveLength(0)
  const replay = await original.replay(admitted.intentId)
  expect(replay.originalIntent.metadata).toMatchObject({ actor: 'agent-one', delegatedBy: 'operator-one', gatewayId: 'gateway-one' })
  expect((await original.execute(admitted.grant, actual)).status).toBe('executed')
})

test('a reopened SQLite evidence store preserves one-use grants across gateway restarts and races', async () => {
  const { DatabaseSync } = require('node:sqlite') as { DatabaseSync: new (path: string) => import('../src/core/event/SqlEventStore').SQLiteDatabase & { close(): void } }
  const databasePath = join(mkdtempSync(join(tmpdir(), 'openkedge-restart-')), 'evidence.sqlite')
  const firstDatabase = new DatabaseSync(databasePath)
  const firstStore = new SQLiteIEECStore(firstDatabase)
  const firstAdapter = new MockTerminationAdapter()
  const first = new ExecutionGateway(source, firstAdapter, key, caller('agent-one'), firstStore)
  const admitted = await first.admit(actual)
  if (admitted.status !== 'allowed') throw new Error('Expected grant')
  firstDatabase.close()
  const reopenedStore = new SQLiteIEECStore(new DatabaseSync(databasePath))
  const restartedAdapter = new MockTerminationAdapter()
  const restarted = new ExecutionGateway(source, restartedAdapter, key, caller('agent-one'), reopenedStore)
  expect((await restarted.execute(admitted.grant, actual)).status).toBe('executed')
  expect((await restarted.execute(admitted.grant, actual)).code).toBe('GRANT_REPLAY')
  const racing = await restarted.admit(actual)
  if (racing.status !== 'allowed') throw new Error('Expected second grant')
  const competingAdapter = new MockTerminationAdapter()
  const competing = new ExecutionGateway(source, competingAdapter, key, caller('agent-one'),
    new SQLiteIEECStore(new DatabaseSync(databasePath)))
  const results = await Promise.all([restarted.execute(racing.grant, actual), competing.execute(racing.grant, actual)])
  expect(results.filter(result => result.status === 'executed')).toHaveLength(1)
  expect(results.filter(result => result.status === 'rejected')).toHaveLength(1)
  expect(firstAdapter.calls.length + restartedAdapter.calls.length + competingAdapter.calls.length).toBe(2)
  expect((await restarted.replay(admitted.intentId)).integrity.valid).toBe(true)
})

test('unavailable evidence or malformed policy fails closed without invoking adapter', async () => {
  const adapter = new MockTerminationAdapter()
  const brokenStore = new InMemoryIEECStore()
  brokenStore.append = async () => { throw new Error('disk unavailable') }
  const unavailable = new ExecutionGateway(source, adapter, key, caller('agent-one'), brokenStore)
  const denied = await unavailable.admit(actual)
  expect(denied.status).toBe('denied')
  if (denied.status !== 'allowed') expect(denied.code).toBe('EVIDENCE_UNAVAILABLE')
  const malformed: PolicySource = { current: async () => validatePolicy({ ...policy, protocolVersion: 2 }) }
  const malformedGateway = new ExecutionGateway(malformed, adapter, key, caller('agent-one'))
  const rejected = await malformedGateway.admit(actual)
  expect(rejected.status).toBe('denied')
  if (rejected.status !== 'allowed') expect(rejected.code).toBe('POLICY_UNAVAILABLE')
  expect(adapter.calls).toHaveLength(0)

  const redeemStore = new InMemoryIEECStore()
  const redeemGateway = new ExecutionGateway(source, adapter, key, caller('agent-one'), redeemStore)
  const admitted = await redeemGateway.admit(actual)
  if (admitted.status !== 'allowed') throw new Error('Expected grant')
  redeemStore.getEventsByIntent = async () => { throw new Error('disk unavailable') }
  expect((await redeemGateway.execute(admitted.grant, actual)).code).toBe('EVIDENCE_UNAVAILABLE')
  expect(adapter.calls).toHaveLength(0)

  const lateStore = new InMemoryIEECStore()
  const lateGateway = new ExecutionGateway(source, adapter, key, caller('agent-one'), lateStore)
  const lateGrant = await lateGateway.admit(actual)
  if (lateGrant.status !== 'allowed') throw new Error('Expected grant')
  const originalGetTrace = lateStore.getTrace.bind(lateStore)
  lateStore.getTrace = async id => {
    const trace = await originalGetTrace(id)
    if (trace?.status === 'RUNNING') throw new Error('disk unavailable')
    return trace
  }
  expect((await lateGateway.execute(lateGrant.grant, actual)).code).toBe('EVIDENCE_UNAVAILABLE')
  expect(adapter.calls).toHaveLength(0)
})

test('loss of outcome persistence after adapter invocation reports uncertainty and blocks retry', async () => {
  const store = new InMemoryIEECStore()
  const adapter = new MockTerminationAdapter()
  const gateway = new ExecutionGateway(source, adapter, key, caller('agent-one'), store)
  const admitted = await gateway.admit(actual)
  if (admitted.status !== 'allowed') throw new Error('Expected grant')
  const originalAppend = store.append.bind(store)
  store.append = async event => {
    if (event.type === EventType.ExecutionCompleted) throw new Error('disk unavailable')
    return originalAppend(event)
  }
  const result = await gateway.execute(admitted.grant, actual)
  expect(result.status).toBe('uncertain')
  expect(result.code).toBe('OUTCOME_UNCERTAIN')
  expect(adapter.calls).toHaveLength(1)
  expect((await gateway.execute(admitted.grant, actual)).code).toBe('GRANT_REPLAY')
})
