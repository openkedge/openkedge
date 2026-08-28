import { randomUUID } from 'node:crypto'
import { once } from 'node:events'
import { createDemoServer, runCapabilityInjectionScenario } from '../server'
import { intent, record, SECRET } from '../../../tests/helpers/temporal'
import { InMemoryEventStore } from '../../../src/core/event/InMemoryEventStore'
import { EventType, type ExecutionIdentity, type Intent } from '../../../src/interfaces/contracts'
import type { IdentityProvider } from '../../../src/core/identity/IdentityProvider'
import { mintCapabilityToken } from '../../../src/core/crypto/capabilities'
import { mintExecutionContract, verifyExecutionContract } from '../../../src/core/crypto/executionContracts'
import { IdentityManager } from '../../../src/core/identity/IdentityManager'
import { TemporalGovernance } from '../../../src/core/governance/TemporalGovernance'
import type { CapabilityToken } from '../../../src/core/governance/types'

function identities(): IdentityProvider & { issueIdentity: jest.Mock; revokeIdentity: jest.Mock } {
  return {
    issueIdentity: jest.fn(async (proposal: Intent): Promise<ExecutionIdentity> => ({
      id: randomUUID(), intentId: proposal.id, issuedAt: Date.now(), expiresAt: Date.now() + 60_000, permissions: [proposal.type]
    })),
    revokeIdentity: jest.fn(async () => {})
  }
}

test('blocks the prompt injection before mutation context/credentials and preserves replay integrity', async () => {
  const provider = identities()
  const { client } = createDemoServer({ secretKey: SECRET, identityProvider: provider })
  const scenario = await runCapabilityInjectionScenario(client)
  expect(scenario.result).toMatchObject({ success: false, errorCode: 'CAPABILITY_MISMATCH_ERROR' })
  expect(provider.issueIdentity).toHaveBeenCalledTimes(1)
  const replay = await client.replayIntent(scenario.intentId)
  expect(replay.integrity.valid).toBe(true)
  expect(replay.reconstructed.finalOutcome).toBe('blocked')
  expect(replay.events.some(event => event.type === EventType.ContextResolved)).toBe(false)
})

test('allows an attenuated mutation and embeds its causal proof in the contract', async () => {
  const { client, store } = createDemoServer({ secretKey: SECRET })
  const source = intent('lookup_instance_cost', { resourceId: 'i-0000016' })
  const read = await client.submitIntent(source)
  const mutation = intent('terminate_instance', { resourceId: 'i-0000016', targetAccountId: 'acc-demo' }, { capabilities: read.capabilities })
  const result = await client.submitIntent(mutation)
  expect(result.success).toBe(true)
  const sourceRecord = (await store.getTrace(source.id))!
  expect(result.executionContract).toMatchObject({ linkedCapabilities: [read.capabilities![0].tokenId], preconditionHashes: [sourceRecord.hash] })
  expect((await client.replayIntent(mutation.id)).integrity.valid).toBe(true)
})

test('cannot bypass server requirements by omitting tokens or self-declaring READ', async () => {
  const provider = identities()
  const { client } = createDemoServer({ secretKey: SECRET, identityProvider: provider })
  const result = await client.submitIntent(intent('terminate_instance', { resourceId: 'i-0000016' }, { kind: 'READ' }))
  expect(result).toMatchObject({ success: false, errorCode: 'CAPABILITY_REQUIRED_ERROR' })
  expect(provider.issueIdentity).not.toHaveBeenCalled()
})

test('malformed tokens are denied and remain replayable', async () => {
  const { client } = createDemoServer({ secretKey: SECRET })
  const proposal = intent('terminate_instance', {}, { capabilities: [null as unknown as CapabilityToken] })
  expect(await client.submitIntent(proposal)).toMatchObject({ errorCode: 'CAPABILITY_INVALID_ERROR' })
  expect((await client.replayIntent(proposal.id)).reconstructed.finalOutcome).toBe('blocked')
})

test('rejects an actor change, expired capability, and a cryptographically valid token with no local source', async () => {
  let now = Date.now()
  const { client } = createDemoServer({ secretKey: SECRET, governance: { clock: () => now } })
  const read = await client.submitIntent(intent('lookup_instance_cost', { resourceId: 'i-0000016' }))
  const payload = { resourceId: 'i-0000016', targetAccountId: 'acc-demo' }
  expect(await client.submitIntent(intent('terminate_instance', payload, { capabilities: read.capabilities,
    metadata: { actor: 'other', timestamp: now } }))).toMatchObject({ errorCode: 'CAPABILITY_ACTOR_ERROR' })
  now = read.capabilities![0].expiresAt
  expect(await client.submitIntent(intent('terminate_instance', payload, { capabilities: read.capabilities }))).toMatchObject({ errorCode: 'CAPABILITY_EXPIRED_ERROR' })
  const foreign = await record(new InMemoryEventStore(), intent('lookup_instance_cost', {}, { kind: 'READ' }), Date.now(), 'SUCCESS', payload)
  const token = mintCapabilityToken(foreign, payload, SECRET, 5_000)
  const local = createDemoServer({ secretKey: SECRET })
  expect((await local.client.submitIntent(intent('terminate_instance', payload, { capabilities: [token] }))).success).toBe(false)
})

test('shared quota remains deterministic across two engines and concurrent agents', async () => {
  const store = new InMemoryEventStore()
  const execute = jest.fn(async () => { await new Promise(resolve => setTimeout(resolve, 5)); return { success: true } })
  const one = createDemoServer({ store, secretKey: SECRET, executor: { execute } })
  const two = createDemoServer({ store, secretKey: SECRET, executor: { execute } })
  for (let attempt = 0; attempt < 10; attempt++) {
    const results = await Promise.all([one, two].map(({ client }, i) => client.submitIntent(intent('transfer', { accountId: `shared-${attempt}`, amount: 300 },
      { metadata: { actor: `agent-${i}`, timestamp: Date.now() } }))))
    expect(results.filter(result => result.success)).toHaveLength(1)
    expect(results.filter(result => result.errorCode === 'TEMPORAL_CONSTRAINT_ERROR')).toHaveLength(1)
  }
  expect(execute).toHaveBeenCalledTimes(10)
})

test('uncertain execution retains a reservation, and duplicate IDs cannot consume it twice', async () => {
  const store = new InMemoryEventStore()
  const first = createDemoServer({ store, secretKey: SECRET, executor: { async execute() { throw new Error('Remote outcome unknown') } } })
  const proposal = intent('transfer', { accountId: 'shared', amount: 300 })
  expect((await first.client.submitIntent(proposal)).success).toBe(false)
  expect((await store.getTrace(proposal.id))?.status).toBe('RESERVED')
  expect(await first.client.submitIntent(proposal)).toMatchObject({ errorCode: 'DUPLICATE_PROPOSAL_ERROR' })
  const second = createDemoServer({ store, secretKey: SECRET })
  expect(await second.client.submitIntent(intent('transfer', { accountId: 'shared', amount: 300 }))).toMatchObject({ errorCode: 'TEMPORAL_CONSTRAINT_ERROR' })
})

test('definite failure and failure before executor invocation release reserved quota', async () => {
  const store = new InMemoryEventStore()
  const failed = createDemoServer({ store, secretKey: SECRET, executor: { async execute() { return { success: false } } } })
  await failed.client.submitIntent(intent('transfer', { accountId: 'shared', amount: 500 }))
  const provider = identities()
  provider.issueIdentity.mockRejectedValueOnce(new Error('Identity unavailable'))
  const blocked = createDemoServer({ store, secretKey: SECRET, identityProvider: provider })
  const proposal = intent('transfer', { accountId: 'shared', amount: 500 })
  expect((await blocked.client.submitIntent(proposal)).success).toBe(false)
  expect((await store.getTrace(proposal.id))?.status).toBe('FAILED')
  expect((await createDemoServer({ store, secretKey: SECRET }).client.submitIntent(intent('transfer', { accountId: 'shared', amount: 500 }))).success).toBe(true)
})

test('bounds must be active before issuing credentials', async () => {
  const provider = identities()
  const { client } = createDemoServer({ secretKey: SECRET, identityProvider: provider })
  for (const temporalBounds of [{ notBefore: Date.now() + 60_000 }, { notAfter: Date.now() - 1 }, { maxDurationMs: 0 }]) {
    const result = await client.submitIntent(intent('transfer', { accountId: 'shared', amount: 1 }, { temporalBounds }))
    expect(result).toMatchObject({ errorCode: 'CONTRACT_TEMPORAL_BOUNDS_ERROR' })
  }
  expect(provider.issueIdentity).not.toHaveBeenCalled()
})

test('rechecks expiry after slow identity issuance and always revokes the returned identity', async () => {
  let now = Date.now()
  const provider = identities()
  provider.issueIdentity.mockImplementation(async (proposal: Intent) => {
    now += 100
    return { id: 'delayed', intentId: proposal.id, issuedAt: now, expiresAt: now + 60_000, permissions: [proposal.type] }
  })
  const execute = jest.fn(async () => ({ success: true }))
  const { client } = createDemoServer({ secretKey: SECRET, identityProvider: provider, executor: { execute },
    governance: { clock: () => now, contractTtlMs: 50 } })
  expect((await client.submitIntent(intent('transfer', { accountId: 'shared', amount: 1 }))).success).toBe(false)
  expect(execute).not.toHaveBeenCalled()
  expect(provider.revokeIdentity).toHaveBeenCalledTimes(1)
})

test('times out execution, signals cancellation, revokes credentials, and retains uncertain quota', async () => {
  const provider = identities()
  let signal: AbortSignal | undefined
  const { client, store } = createDemoServer({ secretKey: SECRET, identityProvider: provider, governance: { maxDurationMs: 25 },
    executor: { async execute(_proposal, _context, _identity, abort) { signal = abort; return new Promise(() => {}) } } })
  const proposal = intent('transfer', { accountId: 'shared', amount: 500 })
  expect(await client.submitIntent(proposal)).toMatchObject({ success: false, error: 'CONTRACT_EXECUTION_TIMEOUT_ERROR' })
  expect(signal?.aborted).toBe(true)
  expect(provider.revokeIdentity).toHaveBeenCalledTimes(1)
  expect((await store.getTrace(proposal.id))?.status).toBe('RESERVED')
})

test('intent snapshot cannot be changed by the caller during an async context lookup', async () => {
  const proposal = intent('transfer', { accountId: 'shared', amount: 100 })
  const { client, store } = createDemoServer({ secretKey: SECRET })
  const pending = client.submitIntent(proposal)
  ;(proposal.payload as { amount: number }).amount = 1_000
  expect((await pending).success).toBe(true)
  expect((await store.getTrace(proposal.id))?.intent.payload).toEqual({ accountId: 'shared', amount: 100 })
})

test('precondition hashes and signed contract fields are checked before credentials unlock', async () => {
  const store = new InMemoryEventStore()
  const proposal = intent('transfer', { accountId: 'shared', amount: 1 })
  const now = Date.now()
  const bounds = { notBefore: now, notAfter: now + 30_000, maxDurationMs: 30_000 }
  const missing = mintExecutionContract(proposal, [], ['a'.repeat(64)], bounds, SECRET, now)
  const provider = identities()
  await expect(new IdentityManager(provider, store).withIdentity(proposal, async () => true, {
    contract: missing, assertCanUnlock: () => verifyExecutionContract(missing, proposal, store, SECRET)
  })).rejects.toThrow('CONTRACT_PRECONDITION_ERROR')
  expect(provider.issueIdentity).not.toHaveBeenCalled()
  const valid = mintExecutionContract(proposal, [], [], bounds, SECRET, now)
  await expect(verifyExecutionContract({ ...valid, temporalBounds: { ...bounds, notAfter: now + 60_000 } }, proposal, store, SECRET)).rejects.toThrow('CONTRACT_SIGNATURE_ERROR')
  await expect(verifyExecutionContract(valid, { ...proposal, payload: { amount: 999 } }, store, SECRET)).rejects.toThrow('CONTRACT_INTENT_MISMATCH_ERROR')
})

test('shortens a contract to the preceding event expiry and rechecks before unlock', async () => {
  let now = Date.now()
  const store = new InMemoryEventStore()
  await record(store, intent('approved'), now - 990)
  const governance = new TemporalGovernance(store, { secretKey: SECRET, actions: {}, clock: () => now,
    rules: [{ type: 'PRECEDING_EVENT_REQUIRED', targetAction: 'transfer', requiredPrecedingAction: 'approved', windowMs: 1_000 }] })
  const proposal = governance.normalize(intent('transfer'))
  const { contract } = await governance.reserve(proposal)
  expect(contract.temporalBounds.notAfter).toBe(now + 10)
  now += 10
  await expect(governance.assertCanUnlock(contract, proposal)).rejects.toThrow('CONTRACT_TEMPORAL_BOUNDS_ERROR')
})

test('late synchronous executor results cannot evade the deadline before timers fire', async () => {
  let now = Date.now()
  const { client, store } = createDemoServer({ secretKey: SECRET, governance: { clock: () => now, contractTtlMs: 100 },
    executor: { async execute() { now += 101; return { success: true } } } })
  const proposal = intent('transfer', { amount: 500, accountId: 'shared' })
  expect((await client.submitIntent(proposal)).success).toBe(false)
  expect((await store.getTrace(proposal.id))?.status).toBe('RESERVED')
})

test('HTTP scenario returns a rejected mutation and replay with verified parent evidence', async () => {
  const { app } = createDemoServer({ secretKey: SECRET })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Missing local port')
  const base = `http://127.0.0.1:${address.port}`
  try {
    const response = await fetch(`${base}/scenarios/capability-injection`, { method: 'POST' })
    expect(response.status).toBe(200)
    const body = await response.json() as { intentId: string; result: { errorCode: string } }
    expect(body.result.errorCode).toBe('CAPABILITY_MISMATCH_ERROR')
    const replay = await (await fetch(`${base}/replay/${body.intentId}`)).json() as { capabilityLinks: unknown[]; integrity: { valid: boolean } }
    expect(replay.capabilityLinks).toEqual([expect.objectContaining({ sourceVerified: true, capabilityVerified: false })])
    expect(replay.integrity.valid).toBe(true)
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  }
})
