import type { Intent } from '../src/interfaces/contracts'
import { OpenKedgeEngine } from '../src/core/engine'
import { InMemoryEventStore } from '../src/core/event'

const silentLogger = {
  info() {},
  warn() {},
  error() {}
}

const sampleIntent: Intent = {
  id: 'intent-1',
  type: 'resource:update',
  payload: { id: 'resource-1' },
  metadata: {
    actor: 'tester',
    timestamp: 1
  }
}

test('processes an allowed intent through the full pipeline', async () => {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine({
    contextProvider: {
      async resolve() {
        return { region: 'local' }
      }
    },
    policyEvaluator: {
      async evaluate(_intent, context) {
        return {
          allowed: true,
          reasons: ['approved'],
          enrichedContext: context
        }
      }
    },
    executor: {
      async execute(intent, context) {
        return {
          success: true,
          result: { intentId: intent.id, context }
        }
      }
    },
    eventStore: store,
    logger: silentLogger
  })

  const result = await engine.process(sampleIntent)
  const events = await store.query({ intentId: sampleIntent.id })

  expect(result).toEqual({
    success: true,
    result: { intentId: sampleIntent.id, context: { region: 'local' } }
  })
  expect(events.map((event) => event.type)).toEqual([
    'IntentReceived',
    'EvaluationCompleted',
    'ExecutionCompleted'
  ])
})

test('returns a blocked execution result when policy denies an intent', async () => {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine({
    contextProvider: {
      async resolve() {
        return { owner: 'platform' }
      }
    },
    policyEvaluator: {
      async evaluate() {
        return {
          allowed: false,
          reasons: ['maintenance window is closed']
        }
      }
    },
    executor: {
      async execute() {
        throw new Error('executor should not run')
      }
    },
    eventStore: store,
    logger: silentLogger
  })

  const result = await engine.process(sampleIntent)
  const executionEvents = await store.query({
    intentId: sampleIntent.id,
    type: 'ExecutionCompleted'
  })

  expect(result.success).toBe(false)
  expect(result.error).toContain('Intent blocked by policy')
  expect(executionEvents).toHaveLength(1)
})

test('captures executor failures without crashing the engine', async () => {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine({
    contextProvider: {
      async resolve() {
        return { state: 'ready' }
      }
    },
    policyEvaluator: {
      async evaluate(_intent, context) {
        return {
          allowed: true,
          reasons: ['approved'],
          enrichedContext: context
        }
      }
    },
    executor: {
      async execute() {
        throw new Error('simulated execution failure')
      }
    },
    eventStore: store,
    logger: silentLogger
  })

  const result = await engine.process(sampleIntent)
  const events = await store.query({ intentId: sampleIntent.id })

  expect(result).toEqual({
    success: false,
    error: 'Execution failed: simulated execution failure'
  })
  expect(events).toHaveLength(3)
})

test('still writes a terminal event when context resolution fails', async () => {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine({
    contextProvider: {
      async resolve() {
        throw new Error('context backend offline')
      }
    },
    policyEvaluator: {
      async evaluate() {
        return {
          allowed: true,
          reasons: ['approved']
        }
      }
    },
    executor: {
      async execute() {
        return {
          success: true
        }
      }
    },
    eventStore: store,
    logger: silentLogger
  })

  const result = await engine.process(sampleIntent)
  const events = await store.query({ intentId: sampleIntent.id })

  expect(result.success).toBe(false)
  expect(events.at(-1)?.type).toBe('ExecutionCompleted')
  expect(events.at(-1)?.payload).toMatchObject({
    execution: {
      success: false
    }
  })
})
