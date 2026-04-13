import type { ContextProvider } from '../src/core/context/ContextProvider'
import { OpenKedgeEngine } from '../src/core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../src/core/event/InMemoryEventStore'
import { ReplayEngine } from '../src/core/event/ReplayEngine'
import type { Executor } from '../src/core/execution/Executor'
import { assertIdentityCanExecute } from '../src/core/identity/Identity'
import { IdentityManager } from '../src/core/identity/IdentityManager'
import { OpenKedgeClient } from '../src/sdk/client'
import type {
  EvaluationResult,
  ExecutionIdentity,
  Intent,
  PolicyEvaluator
} from '../src/interfaces/contracts'
import { EventType } from '../src/interfaces/contracts'

const sampleIntent: Intent = {
  id: 'intent-1',
  type: 'resource:update',
  payload: { id: 'resource-1' },
  metadata: {
    actor: 'tester',
    timestamp: 1
  }
}

function createTestIdentityManager(
  store: InMemoryEventStore,
  options: {
    ttlMs?: number
  } = {}
): IdentityManager {
  return new IdentityManager(
    {
      async issueIdentity(intent) {
        const issuedAt = Date.now()

        return {
          id: `identity-${intent.id}-${issuedAt}`,
          intentId: intent.id,
          issuedAt,
          expiresAt: issuedAt + (options.ttlMs ?? 60_000),
          permissions: [intent.type],
          metadata: {
            provider: 'test'
          }
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

test('records a full evidence chain for an allowed intent', async () => {
  const store = new InMemoryEventStore()
  const contextProvider: ContextProvider = {
    async resolve() {
      return { region: 'local' }
    }
  }
  const policyEvaluator: PolicyEvaluator = {
    async evaluate(_intent, context): Promise<EvaluationResult> {
      return {
        allowed: true,
        reasons: ['approved'],
        enrichedContext: context
      }
    }
  }
  const executor: Executor = {
    async execute(intent, context, identity) {
      return {
        success: true,
        result: { intentId: intent.id, context, identityId: identity.id }
      }
    }
  }
  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      contextProvider,
      policyEvaluator,
      executor,
      createTestIdentityManager(store),
      store
    ),
    store
  )

  const result = await client.submitIntent(sampleIntent)
  const events = await client.getEventsByIntent(sampleIntent.id)

  expect(result).toEqual({
    success: true,
    result: {
      intentId: sampleIntent.id,
      context: { region: 'local' },
      identityId: expect.stringContaining(`identity-${sampleIntent.id}-`)
    }
  })
  expect(events.map((event) => event.type)).toEqual([
    EventType.IntentReceived,
    EventType.ContextResolved,
    EventType.EvaluationCompleted,
    EventType.IdentityIssued,
    EventType.IdentityUsed,
    EventType.ExecutionCompleted,
    EventType.IdentityRevoked
  ])
  expect(events.map((event) => event.sequence)).toEqual([1, 2, 3, 4, 5, 6, 7])
  expect(events[0].previousEventHash).toBeNull()
  expect(events[1].previousEventHash).toBe(events[0].currentHash)
  expect(events[3].payload.identitySnapshot?.intentId).toBe(sampleIntent.id)
  expect(events[5].payload.executionResult).toEqual(result)
})

test('replay reconstructs a blocked intent and preserves reasoning', async () => {
  const store = new InMemoryEventStore()
  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      {
        async resolve() {
          return { owner: 'platform' }
        }
      },
      {
        async evaluate() {
          return {
            allowed: false,
            reasons: ['maintenance window is closed']
          }
        }
      },
      {
        async execute() {
          throw new Error('executor should not run')
        }
      },
      createTestIdentityManager(store),
      store
    ),
    store
  )

  const result = await client.submitIntent(sampleIntent)
  const replay = await client.replayIntent(sampleIntent.id)

  expect(result.success).toBe(false)
  expect(result.error).toContain('Blocked by policy')
  expect(replay.reconstructed.finalOutcome).toBe('blocked')
  expect(replay.events.at(-1)?.type).toBe(EventType.ExecutionSkipped)
  expect(replay.reasoningTrail).toContain('maintenance window is closed')
  expect(replay.integrity.valid).toBe(true)
})

test('processing failures are captured as terminal evidence events', async () => {
  const store = new InMemoryEventStore()
  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      {
        async resolve() {
          throw new Error('context backend offline')
        }
      },
      {
        async evaluate() {
          return {
            allowed: true,
            reasons: ['approved']
          }
        }
      },
      {
        async execute() {
          return {
            success: true
          }
        }
      },
      createTestIdentityManager(store),
      store
    ),
    store
  )

  const result = await client.submitIntent(sampleIntent)
  const replay = await client.replayIntent(sampleIntent.id)

  expect(result).toEqual({
    success: false,
    error: 'context backend offline'
  })
  expect(replay.events.at(-1)?.type).toBe(EventType.ProcessingFailed)
  expect(replay.reconstructed.finalOutcome).toBe('failed')
  expect(replay.reasoningTrail).toContain('Processing encountered an exception')
})

test('replay detects tampering in the event hash chain', async () => {
  const store = new InMemoryEventStore()
  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      {
        async resolve() {
          return { region: 'local' }
        }
      },
      {
        async evaluate(_intent, context) {
          return {
            allowed: true,
            reasons: ['approved'],
            enrichedContext: context
          }
        }
      },
      {
        async execute() {
          return {
            success: true,
            result: { ok: true }
          }
        }
      },
      createTestIdentityManager(store),
      store
    ),
    store
  )

  await client.submitIntent(sampleIntent)
  const originalEvents = await client.getEventsByIntent(sampleIntent.id)
  const tamperedEvents = originalEvents.map((event) => ({
    ...event,
    payload: {
      ...event.payload,
      reasoningTrail:
        event.sequence === 3 ? ['tampered reason'] : event.payload.reasoningTrail
    }
  }))

  const replay = await new ReplayEngine().replayIntent(tamperedEvents)

  expect(replay.integrity.valid).toBe(false)
  expect(replay.integrity.brokenAtEventId).toBe(originalEvents[2].id)
})

test('fails execution when the issued identity expires before use', async () => {
  const store = new InMemoryEventStore()
  const delayedExecutor: Executor = {
    async execute(
      intent: Intent,
      _context: unknown,
      identity: ExecutionIdentity
    ) {
      await new Promise((resolve) => setTimeout(resolve, 10))
      assertIdentityCanExecute(intent, identity)

      return {
        success: true
      }
    }
  }
  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      {
        async resolve() {
          return { owner: 'platform' }
        }
      },
      {
        async evaluate() {
          return {
            allowed: true,
            reasons: ['approved']
          }
        }
      },
      delayedExecutor,
      createTestIdentityManager(store, { ttlMs: 5 }),
      store
    ),
    store
  )

  const result = await client.submitIntent(sampleIntent)
  const events = await client.getEventsByIntent(sampleIntent.id)

  expect(result.success).toBe(false)
  expect(result.error).toContain('expired')
  expect(events.map((event) => event.type)).toEqual([
    EventType.IntentReceived,
    EventType.ContextResolved,
    EventType.EvaluationCompleted,
    EventType.IdentityIssued,
    EventType.IdentityUsed,
    EventType.IdentityRevoked,
    EventType.ProcessingFailed
  ])
})
