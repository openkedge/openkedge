import type { ContextProvider } from '../src/core/context/ContextProvider'
import { OpenKedgeEngine } from '../src/core/engine/OpenKedgeEngine'
import type { PolicyEvaluator } from '../src/core/evaluation/PolicyEvaluator'
import { InMemoryEventStore } from '../src/core/event/InMemoryEventStore'
import type { Executor } from '../src/core/execution/Executor'
import type { Intent } from '../src/interfaces/contracts'

let infoSpy: jest.SpyInstance
let warnSpy: jest.SpyInstance
let errorSpy: jest.SpyInstance

beforeEach(() => {
  infoSpy = jest.spyOn(console, 'log').mockImplementation(() => {})
  warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {})
  errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  infoSpy.mockRestore()
  warnSpy.mockRestore()
  errorSpy.mockRestore()
})

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
  const contextProvider: ContextProvider = {
    async resolve() {
      return { region: 'local' }
    }
  }
  const policyEvaluator: PolicyEvaluator = {
    async evaluate(_intent, context) {
      return {
        allowed: true,
        reasons: ['approved'],
        enrichedContext: context
      }
    }
  }
  const executor: Executor = {
    async execute(intent, context) {
      return {
        success: true,
        result: { intentId: intent.id, context }
      }
    }
  }
  const engine = new OpenKedgeEngine(
    contextProvider,
    policyEvaluator,
    executor,
    store
  )

  const result = await engine.process(sampleIntent)
  const events = await store.query(sampleIntent.id)

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
  const engine = new OpenKedgeEngine(
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
    store
  )

  const result = await engine.process(sampleIntent)
  const executionEvents = (await store.query(sampleIntent.id)).filter(
    (event) => event.type === 'ExecutionCompleted'
  )

  expect(result.success).toBe(false)
  expect(result.error).toContain('Intent blocked by policy')
  expect(executionEvents).toHaveLength(1)
})

test('captures executor failures without crashing the engine', async () => {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine(
    {
      async resolve() {
        return { state: 'ready' }
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
        throw new Error('simulated execution failure')
      }
    },
    store
  )

  const result = await engine.process(sampleIntent)
  const events = await store.query(sampleIntent.id)

  expect(result).toEqual({
    success: false,
    error: 'Execution failed: simulated execution failure'
  })
  expect(events).toHaveLength(3)
})

test('still writes a terminal event when context resolution fails', async () => {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine(
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
    store
  )

  const result = await engine.process(sampleIntent)
  const events = await store.query(sampleIntent.id)

  expect(result.success).toBe(false)
  expect(events.at(-1)?.type).toBe('ExecutionCompleted')
  expect(events.at(-1)?.payload).toMatchObject({
    execution: {
      success: false
    }
  })
})
