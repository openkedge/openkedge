import { DefaultContextProvider } from '../core/context/DefaultContextProvider'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { ReplayEngine } from '../core/event/ReplayEngine'
import { DefaultPolicyEvaluator } from '../core/evaluation/DefaultPolicyEvaluator'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { NoopExecutor } from '../core/execution/NoopExecutor'
import { IdentityManager } from '../core/identity/IdentityManager'
import type {
  EventStore,
  ExecutionResult,
  Intent,
  ReplayResult
} from '../interfaces/contracts'

export interface OpenKedgeClientOptions {
  engine?: OpenKedgeEngine
  eventStore?: EventStore
}

export class OpenKedgeClient {
  private readonly replayEngine = new ReplayEngine()

  constructor(
    private readonly engine: OpenKedgeEngine,
    private readonly eventStore: EventStore
  ) {}

  async submitIntent(intent: Intent): Promise<ExecutionResult> {
    return this.engine.process(intent)
  }

  async getEventsByIntent(intentId: string) {
    return this.eventStore.getEventsByIntent(intentId)
  }

  async exportIntentChain(intentId: string): Promise<string> {
    return this.eventStore.exportByIntent(intentId)
  }

  async replayIntent(intentId: string): Promise<ReplayResult> {
    const events = await this.eventStore.getEventsByIntent(intentId)
    return this.replayEngine.replayIntent(events)
  }
}

export function createOpenKedgeClient(
  options: OpenKedgeClientOptions = {}
): OpenKedgeClient {
  if (options.engine && !options.eventStore) {
    throw new Error(
      'createOpenKedgeClient requires eventStore when a custom engine is provided'
    )
  }

  const eventStore = options.eventStore ?? new InMemoryEventStore()
  const engine =
    options.engine ??
    new OpenKedgeEngine(
      new DefaultContextProvider(),
      new DefaultPolicyEvaluator(),
      new NoopExecutor(),
      new IdentityManager(
        {
          async issueIdentity(intent) {
            const issuedAt = Date.now()

            return {
              id: `local-${intent.id}-${issuedAt}`,
              intentId: intent.id,
              issuedAt,
              expiresAt: issuedAt + 60_000,
              permissions: [intent.type],
              metadata: {
                provider: 'local-ephemeral'
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
        eventStore
      ),
      eventStore
    )

  return new OpenKedgeClient(engine, eventStore)
}

const defaultClient = createOpenKedgeClient()

export async function submitIntent(intent: Intent): Promise<ExecutionResult> {
  return defaultClient.submitIntent(intent)
}
