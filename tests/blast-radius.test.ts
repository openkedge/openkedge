import { BlastRadiusEstimator } from '../src/core/blast/BlastRadiusEstimator'
import { BlastRadiusPolicy } from '../src/core/blast/BlastRadiusPolicy'
import { OpenKedgeEngine } from '../src/core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../src/core/event/InMemoryEventStore'
import { IdentityManager } from '../src/core/identity/IdentityManager'
import { OpenKedgeClient } from '../src/sdk/client'
import type { Intent } from '../src/interfaces/contracts'
import { EventType } from '../src/interfaces/contracts'

function createTerminateIntent(instanceIds: string[]): Intent {
  return {
    id: `intent-${instanceIds.length}-${instanceIds[0] ?? 'none'}`,
    type: 'ec2:TerminateInstances',
    payload: instanceIds,
    metadata: {
      actor: 'blast-test',
      timestamp: 1
    }
  }
}

function createIdentityManager(store: InMemoryEventStore): IdentityManager {
  return new IdentityManager(
    {
      async issueIdentity(intent) {
        const issuedAt = Date.now()

        return {
          id: `blast-${intent.id}`,
          intentId: intent.id,
          issuedAt,
          expiresAt: issuedAt + 60_000,
          permissions: [intent.type]
        }
      },
      async revokeIdentity(identity) {
        identity.metadata = {
          ...identity.metadata,
          revokedAt: Date.now()
        }
      }
    },
    store
  )
}

test('estimates a low-risk blast radius for a single instance', () => {
  const estimator = new BlastRadiusEstimator()
  const blastRadius = estimator.estimate(createTerminateIntent(['i-1']), {
    instances: [
      {
        instanceId: 'i-1',
        state: 'running',
        tags: { env: 'dev', critical: 'false' }
      }
    ]
  })

  expect(blastRadius).toEqual({
    resourceCount: 1,
    resourceIds: ['i-1'],
    riskLevel: 'LOW',
    reasons: [
      'Intent targets 1 resource',
      'Blast radius evaluated as LOW (1 resource)'
    ]
  })
})

test('escalates production workloads to high risk', () => {
  const estimator = new BlastRadiusEstimator()
  const blastRadius = estimator.estimate(
    createTerminateIntent(['i-1', 'i-2', 'i-3', 'i-4', 'i-5']),
    {
      instances: [
        {
          instanceId: 'i-1',
          state: 'running',
          tags: { env: 'prod', critical: 'false' }
        },
        {
          instanceId: 'i-2',
          state: 'running',
          tags: { env: 'prod', critical: 'false' }
        },
        {
          instanceId: 'i-3',
          state: 'running',
          tags: { env: 'prod', critical: 'false' }
        },
        {
          instanceId: 'i-4',
          state: 'running',
          tags: { env: 'prod', critical: 'false' }
        },
        {
          instanceId: 'i-5',
          state: 'running',
          tags: { env: 'prod', critical: 'false' }
        }
      ]
    }
  )

  expect(blastRadius.riskLevel).toBe('HIGH')
  expect(blastRadius.reasons).toContain(
    'Production instances detected: i-1, i-2, i-3, i-4, i-5'
  )
})

test('blocks critical blast radius by policy', () => {
  const policy = new BlastRadiusPolicy()

  expect(
    policy.evaluate({
      resourceCount: 50,
      resourceIds: Array.from({ length: 50 }, (_, index) => `i-${index + 1}`),
      riskLevel: 'CRITICAL',
      reasons: ['Blast radius evaluated as CRITICAL (50 resources)']
    })
  ).toEqual({
    allowed: false,
    reasons: [
      'Blast radius evaluated as CRITICAL (50 resources)',
      'Blocked due to CRITICAL blast radius'
    ]
  })
})

test('blocks execution when blast radius is critical even if safety allows it', async () => {
  const store = new InMemoryEventStore()
  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      {
        async resolve() {
          return {
            instances: Array.from({ length: 50 }, (_, index) => ({
              instanceId: `i-${index + 1}`,
              state: 'running',
              tags: { env: 'dev', critical: 'false' }
            }))
          }
        }
      },
      {
        async evaluate(_intent, context) {
          return {
            allowed: true,
            reasons: ['safety evaluator would allow this mutation'],
            enrichedContext: context
          }
        }
      },
      {
        async execute() {
          throw new Error('executor should not run')
        }
      },
      createIdentityManager(store),
      store
    ),
    store
  )

  const intent = createTerminateIntent(
    Array.from({ length: 50 }, (_, index) => `i-${index + 1}`)
  )
  const result = await client.submitIntent(intent)
  const replay = await client.replayIntent(intent.id)

  expect(result.success).toBe(false)
  expect(result.error).toContain('Blocked due to CRITICAL blast radius')
  expect(replay.reconstructed.blastRadius?.riskLevel).toBe('CRITICAL')
  expect(replay.events.map((event) => event.type)).toContain(
    EventType.BlastRadiusEvaluated
  )
})
