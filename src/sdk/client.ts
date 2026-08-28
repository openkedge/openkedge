import { DefaultContextProvider } from '../core/context/DefaultContextProvider'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { ReplayEngine } from '../core/event/ReplayEngine'
import { DefaultPolicyEvaluator } from '../core/evaluation/DefaultPolicyEvaluator'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { NoopExecutor } from '../core/execution/NoopExecutor'
import { IdentityManager } from '../core/identity/IdentityManager'
import type { TemporalGovernanceOptions } from '../core/governance/types'
import type {
  EventStore,
  ExecutionResult,
  Intent,
  ReplayResult
} from '../interfaces/contracts'
import { EventType } from '../interfaces/contracts'
import { isRecord } from '../core/crypto/canonical'

export interface OpenKedgeClientOptions {
  engine?: OpenKedgeEngine
  eventStore?: EventStore
  governance?: TemporalGovernanceOptions
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
    const replay = await this.replayEngine.replayIntent(events)
    // Rejected intents may contain malformed tokens; they must remain replayable.
    const tokens = (replay.originalIntent.capabilities ?? []).filter(token => isRecord(token) &&
      typeof token.tokenId === 'string' && typeof token.sourceProposalId === 'string' &&
      typeof token.sourceHash === 'string' && isRecord(token.boundAttributes))
    if (tokens.length) {
      replay.capabilityLinks = await Promise.all(tokens.map(async token => {
        const source = await this.eventStore.getEventsByIntent(token.sourceProposalId)
        const completion = source.find(event => event.currentHash === token.sourceHash && event.type === EventType.ExecutionCompleted && event.payload.executionResult?.success)
        const sourceVerified = !!completion && (await this.replayEngine.replayIntent(source)).integrity.valid
        return {
          tokenId: token.tokenId, sourceProposalId: token.sourceProposalId, sourceHash: token.sourceHash,
          sourceVerified, capabilityVerified: replay.integrity.valid && events.some(event => event.type === EventType.CapabilityVerified && event.payload.capabilities?.some(cap => cap.tokenId === token.tokenId)),
          boundAttributes: token.boundAttributes,
          ...(sourceVerified && completion ? { sourceAction: completion.payload.intentSnapshot.type, sourceTimestamp: completion.timestamp } : {})
        }
      }))
    }
    return replay
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
            const issuedAt = options.governance?.clock?.() ?? Date.now()

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
      eventStore,
      undefined,
      undefined,
      options.governance
    )

  return new OpenKedgeClient(engine, eventStore)
}

const defaultClient = createOpenKedgeClient()

export async function submitIntent(intent: Intent): Promise<ExecutionResult> {
  return defaultClient.submitIntent(intent)
}
