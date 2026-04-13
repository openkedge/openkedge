import { randomUUID } from 'node:crypto'

import type { ContextProvider } from '../core/context/ContextProvider'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import type { PolicyEvaluator } from '../core/evaluation/PolicyEvaluator'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import type { Executor } from '../core/execution/Executor'
import { IdentityManager } from '../core/identity/IdentityManager'
import { OpenKedgeClient } from '../sdk/client'
import type {
  EvaluationResult,
  ExecutionIdentity,
  Intent
} from '../interfaces/contracts'

class DemoContextProvider implements ContextProvider {
  async resolve(intent: Intent): Promise<unknown> {
    const payload = intent.payload as { critical?: boolean; resourceId?: string }

    return {
      resourceId: payload.resourceId ?? 'unknown',
      critical: payload.critical === true,
      environment: 'demo'
    }
  }
}

class DemoPolicyEvaluator implements PolicyEvaluator {
  async evaluate(intent: Intent, context: unknown): Promise<EvaluationResult> {
    const ctx = context as { critical?: boolean; resourceId?: string }

    if (ctx.critical) {
      return {
        allowed: false,
        reasons: [
          `Resource ${ctx.resourceId} is marked critical`,
          'Destructive operation denied by demo policy'
        ],
        enrichedContext: context
      }
    }

    return {
      allowed: true,
      reasons: [
        `Resource ${ctx.resourceId} is not marked critical`,
        'Operation permitted by demo policy'
      ],
      enrichedContext: context
    }
  }
}

class DemoExecutor implements Executor {
  async execute(
    intent: Intent,
    context: unknown,
    identity: ExecutionIdentity
  ) {
    return {
      success: true,
      result: {
        message: 'Demo mutation executed',
        intentType: intent.type,
        context,
        identityId: identity.id
      }
    }
  }
}

async function runScenario(label: string, critical: boolean): Promise<void> {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine(
    new DemoContextProvider(),
    new DemoPolicyEvaluator(),
    new DemoExecutor(),
    new IdentityManager(
      {
        async issueIdentity(intent) {
          const issuedAt = Date.now()

          return {
            id: `demo-${intent.id}-${issuedAt}`,
            intentId: intent.id,
            issuedAt,
            expiresAt: issuedAt + 60_000,
            permissions: [intent.type],
            metadata: {
              provider: 'demo-local'
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
    ),
    store
  )
  const client = new OpenKedgeClient(engine, store)

  const intent: Intent = {
    id: randomUUID(),
    type: 'resource:delete',
    payload: {
      resourceId: critical ? 'db-prod-1' : 'db-dev-1',
      critical
    },
    metadata: {
      actor: 'agent-1',
      timestamp: Date.now()
    }
  }

  const result = await client.submitIntent(intent)
  const replay = await client.replayIntent(intent.id)

  console.log(`\n=== ${label} ===`)
  console.log('Result:')
  console.log(JSON.stringify(result, null, 2))

  console.log('\nReasoning trail:')
  for (const line of replay.reasoningTrail) {
    console.log(`- ${line}`)
  }

  console.log('\nIntegrity:')
  console.log(JSON.stringify(replay.integrity, null, 2))

  console.log('\nFinal outcome:')
  console.log(replay.reconstructed.finalOutcome)
}

async function main(): Promise<void> {
  await runScenario('Allowed scenario', false)
  await runScenario('Blocked scenario', true)
}

void main()
