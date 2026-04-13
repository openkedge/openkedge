import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { IdentityManager } from '../core/identity/IdentityManager'
import { OpenKedgeClient } from '../sdk/client'
import { AwsSafetyPolicyEvaluator } from '../adapters/aws/AwsSafetyPolicyEvaluator'
import type { Intent } from '../interfaces/contracts'

function createIntent(label: string, instanceIds: string[]): Intent {
  return {
    id: `blast-demo-${label}`,
    type: 'ec2:TerminateInstances',
    payload: instanceIds,
    metadata: {
      actor: 'blast-demo',
      timestamp: Date.now()
    }
  }
}

function createIdentityManager(store: InMemoryEventStore): IdentityManager {
  return new IdentityManager(
    {
      async issueIdentity(intent) {
        const issuedAt = Date.now()

        return {
          id: `blast-demo-${intent.id}-${issuedAt}`,
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

async function runScenario(
  label: string,
  context: unknown,
  instanceIds: string[]
): Promise<void> {
  const store = new InMemoryEventStore()
  const engine = new OpenKedgeEngine(
    {
      async resolve() {
        return context
      }
    },
    new AwsSafetyPolicyEvaluator(),
    {
      async execute(intent, resolvedContext, identity) {
        return {
          success: true,
          result: {
            intentId: intent.id,
            identityId: identity.id,
            resolvedContext
          }
        }
      }
    },
    createIdentityManager(store),
    store
  )
  const client = new OpenKedgeClient(engine, store)
  const intent = createIntent(label, instanceIds)

  const result = await client.submitIntent(intent)
  const replay = await client.replayIntent(intent.id)

  console.log(`\n=== ${label} ===`)
  console.log(
    JSON.stringify(
      {
        finalOutcome: replay.reconstructed.finalOutcome,
        blastRadius: replay.reconstructed.blastRadius,
        result
      },
      null,
      2
    )
  )
}

async function main(): Promise<void> {
  await runScenario(
    'Terminate 1 instance -> LOW -> allowed',
    {
      instances: [
        {
          instanceId: 'i-low-1',
          state: 'running',
          tags: { env: 'dev', critical: 'false' }
        }
      ]
    },
    ['i-low-1']
  )

  await runScenario(
    'Terminate 10 instances -> HIGH -> allowed with warning',
    {
      instances: Array.from({ length: 10 }, (_, index) => ({
        instanceId: `i-high-${index + 1}`,
        state: 'running',
        tags: { env: 'dev', critical: 'false' }
      }))
    },
    Array.from({ length: 10 }, (_, index) => `i-high-${index + 1}`)
  )

  await runScenario(
    'Terminate 50 instances -> CRITICAL -> blocked',
    {
      instances: Array.from({ length: 50 }, (_, index) => ({
        instanceId: `i-critical-${index + 1}`,
        state: 'running',
        tags: { env: 'dev', critical: 'false' }
      }))
    },
    Array.from({ length: 50 }, (_, index) => `i-critical-${index + 1}`)
  )

  await runScenario(
    'Terminate 1 critical instance -> CRITICAL -> blocked',
    {
      instances: [
        {
          instanceId: 'i-critical-single',
          state: 'running',
          tags: { env: 'prod', critical: 'true' }
        }
      ]
    },
    ['i-critical-single']
  )
}

void main()
