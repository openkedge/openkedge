import { randomUUID } from 'node:crypto'

import type { EventStore, Intent } from '../../interfaces/contracts'
import { EventType } from '../../interfaces/contracts'

import {
  assertIdentityCanExecute,
  toIdentityAuditRecord,
  type ExecutionIdentity
} from './Identity'
import type { IdentityProvider } from './IdentityProvider'

export class IdentityManager {
  constructor(
    private readonly identityProvider: IdentityProvider,
    private readonly eventStore: EventStore
  ) {}

  async withIdentity<T>(
    intent: Intent,
    fn: (identity: ExecutionIdentity) => Promise<T>
  ): Promise<T> {
    const identity = await this.identityProvider.issueIdentity(intent)

    assertIdentityCanExecute(intent, identity)
    await this.appendIdentityEvent(
      EventType.IdentityIssued,
      intent,
      identity,
      ['Ephemeral execution identity issued for the approved intent']
    )

    try {
      assertIdentityCanExecute(intent, identity)
      await this.appendIdentityEvent(
        EventType.IdentityUsed,
        intent,
        identity,
        [`Execution identity bound to intent type ${intent.type}`]
      )

      return await fn(identity)
    } finally {
      await this.identityProvider.revokeIdentity(identity)
      await this.appendIdentityEvent(
        EventType.IdentityRevoked,
        intent,
        identity,
        ['Ephemeral execution identity revoked after execution']
      )
    }
  }

  assertUsableIdentity(intent: Intent, identity: ExecutionIdentity): void {
    assertIdentityCanExecute(intent, identity)
  }

  private async appendIdentityEvent(
    type: EventType.IdentityIssued | EventType.IdentityUsed | EventType.IdentityRevoked,
    intent: Intent,
    identity: ExecutionIdentity,
    reasoningTrail: string[]
  ): Promise<void> {
    await this.eventStore.append({
      id: randomUUID(),
      type,
      timestamp: Date.now(),
      intentId: intent.id,
      payload: {
        intentSnapshot: intent,
        identitySnapshot: toIdentityAuditRecord(identity),
        reasoningTrail,
        metadata: {
          identityId: identity.id,
          intentId: intent.id,
          expiration: new Date(identity.expiresAt).toISOString(),
          permissionScope: [...identity.permissions]
        }
      }
    })
  }
}
