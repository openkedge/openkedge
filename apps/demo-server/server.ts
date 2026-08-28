import express from 'express'
import cors from 'cors'
import { randomBytes, randomUUID } from 'node:crypto'

import { OpenKedgeEngine } from '../../src/core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../../src/core/event/InMemoryEventStore'
import { IdentityManager } from '../../src/core/identity/IdentityManager'
import { OpenKedgeClient } from '../../src/sdk/client'
import type { Executor } from '../../src/core/execution/Executor'
import type { IdentityProvider } from '../../src/core/identity/IdentityProvider'
import type { IEECStore, TemporalGovernanceOptions } from '../../src/core/governance/types'
import { isRecord } from '../../src/core/crypto/canonical'
import type { Intent } from '../../src/interfaces/contracts'

const MOCK_EC2_INSTANCES = Array.from({ length: 35 }, (_, index) => ({
  id: `i-${String(index + 1).padStart(7, '0')}`,
  tags: { env: index < 15 ? 'prod' : 'dev', critical: index < 5 ? 'true' : 'false' }
}))

export interface DemoOptions {
  store?: IEECStore
  secretKey?: string
  executor?: Executor
  identityProvider?: IdentityProvider
  governance?: Partial<Omit<TemporalGovernanceOptions, 'secretKey'>>
}

export function createDemoServer(options: DemoOptions = {}) {
  const app = express()
  app.use(cors())
  app.use(express.json({ limit: '128kb' }))
  const store = options.store ?? new InMemoryEventStore()
  const governance: TemporalGovernanceOptions = {
    secretKey: options.secretKey ?? process.env.OPENKEDGE_CAPABILITY_SECRET ?? randomBytes(32).toString('hex'),
    actions: {
      lookup_instance_cost: { kind: 'READ', capabilityBindings: { resourceId: 'resourceId', targetAccountId: 'targetAccountId' } },
      terminate_instance: { kind: 'MUTATION', requiredCapabilities: ['lookup_instance_cost'] },
      transfer: { kind: 'MUTATION' }
    },
    rules: [
      { id: 'recent-instance-inspection', type: 'PRECEDING_EVENT_REQUIRED', targetAction: 'terminate_instance', windowMs: 15 * 60_000,
        requiredPrecedingAction: 'lookup_instance_cost', matches: [
          { currentPath: 'payload.resourceId', historicalPath: 'result.resourceId' },
          { currentPath: 'payload.targetAccountId', historicalPath: 'result.targetAccountId' }
        ] },
      { id: 'shared-transfer-budget', type: 'SLIDING_WINDOW_QUOTA', targetAction: 'transfer', scope: 'RESOURCE',
        resourcePath: 'accountId', windowMs: 24 * 60 * 60_000, metricPath: 'intent.payload.amount', maxCumulativeValue: 500, unit: 'USD' }
    ],
    ...options.governance
  }
  const identityProvider: IdentityProvider = options.identityProvider ?? {
    async issueIdentity(intent) {
      const issuedAt = governance.clock?.() ?? Date.now()
      return { id: `demo-${randomUUID()}`, intentId: intent.id, issuedAt, expiresAt: issuedAt + 30_000,
        permissions: [intent.type], metadata: { provider: 'demo' } }
    },
    async revokeIdentity(identity) { identity.metadata = { ...identity.metadata, revokedAt: Date.now() } }
  }
  const executor: Executor = options.executor ?? {
    async execute(intent) {
      if (intent.type === 'lookup_instance_cost') {
        // Output comes from trusted mock inventory, never text returned by an agent.
        const resourceId = isRecord(intent.payload) ? intent.payload.resourceId : undefined
        const instance = MOCK_EC2_INSTANCES.find(item => item.id === resourceId)
        if (!instance) return { success: false, error: 'Instance not found' }
        return { success: true, result: { resourceId: instance.id, targetAccountId: 'acc-demo', hourlyCost: 0.12 } }
      }
      return { success: true, result: { simulated: true } }
    }
  }
  const engine = new OpenKedgeEngine(
    { async resolve(intent) {
      const ids = Array.isArray(intent.payload) ? intent.payload : isRecord(intent.payload) ? [intent.payload.resourceId] : []
      return { instances: MOCK_EC2_INSTANCES.filter(instance => ids.includes(instance.id)).map(instance => ({
        instanceId: instance.id, tags: instance.tags, state: 'running'
      })), environment: 'demo-cloud' }
    } },
    { async evaluate(intent, context) {
      if (!['ec2:TerminateInstances', 'lookup_instance_cost', 'terminate_instance', 'transfer'].includes(intent.type)) {
        return { allowed: false, reasons: ['Unknown demo action'] }
      }
      const ids = Array.isArray(intent.payload) ? intent.payload : isRecord(intent.payload) ? [intent.payload.resourceId] : []
      const critical = MOCK_EC2_INSTANCES.filter(item => ids.includes(item.id) && item.tags.critical === 'true')
      const allowed = !['ec2:TerminateInstances', 'terminate_instance'].includes(intent.type) || critical.length === 0
      return { allowed, reasons: [allowed ? 'Demo safety policy passed' : 'Cannot terminate critical instances'], enrichedContext: context }
    } },
    executor, new IdentityManager(identityProvider, store), store, undefined, undefined, governance
  )
  const client = new OpenKedgeClient(engine, store)

  app.get('/mock/ec2', (_req, res) => { res.json(MOCK_EC2_INSTANCES) })
  app.post('/intent', async (req, res) => {
    try { res.json(await client.submitIntent(req.body as Intent)) }
    catch (error) { res.status(500).json({ error: error instanceof Error ? error.message : String(error) }) }
  })
  app.get('/replay/:intentId', async (req, res) => {
    try { res.json(await client.replayIntent(req.params.intentId)) }
    catch { res.status(404).json({ error: 'Replay not found' }) }
  })
  app.post('/scenarios/capability-injection', async (_req, res) => {
    try {
      const result = await runCapabilityInjectionScenario(client)
      res.json(result)
    } catch (error) { res.status(500).json({ error: error instanceof Error ? error.message : String(error) }) }
  })
  app.post('/scenarios/temporal-budget', async (_req, res) => {
    const accountId = `demo-budget-${randomUUID()}`
    const submit = (amount: number) => client.submitIntent({ id: randomUUID(), type: 'transfer', payload: { accountId, amount },
      metadata: { actor: 'demo-agent', timestamp: Date.now() } })
    try {
      await submit(150)
      const intentId = randomUUID()
      const result = await client.submitIntent({ id: intentId, type: 'transfer', payload: { accountId, amount: 400 },
        metadata: { actor: 'second-agent', timestamp: Date.now() } })
      res.json({ intentId, result })
    } catch (error) { res.status(500).json({ error: error instanceof Error ? error.message : String(error) }) }
  })
  return { app, client, store, engine }
}

export async function runCapabilityInjectionScenario(client: OpenKedgeClient) {
  const sourceId = randomUUID()
  const actor = 'demo-agent'
  const lookup = await client.submitIntent({ id: sourceId, type: 'lookup_instance_cost', payload: { resourceId: 'i-0000016' },
    metadata: { actor, timestamp: Date.now() } })
  if (!lookup.success || !lookup.capabilities?.length) throw new Error('Demo lookup did not issue a capability')
  const intentId = randomUUID()
  const result = await client.submitIntent({ id: intentId, type: 'terminate_instance',
    payload: { resourceId: 'i-0000001', targetAccountId: 'acc-demo' }, capabilities: lookup.capabilities,
    requiredCapabilities: lookup.capabilities.map(token => token.tokenId), metadata: { actor, timestamp: Date.now() } })
  return { sourceId, intentId, lookup, result }
}

if (require.main === module) {
  const { app } = createDemoServer()
  const port = Number(process.env.PORT ?? 3001)
  // The demo trusts actor metadata and is deliberately local-only.
  app.listen(port, '127.0.0.1', () => { console.log(`Demo server running on http://127.0.0.1:${port}`) })
}
