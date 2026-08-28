import { randomUUID } from 'node:crypto'

import type { EventStore, Intent } from '../../interfaces/contracts'
import { EventType } from '../../interfaces/contracts'

import {
  assertIdentityCanExecute,
  toIdentityAuditRecord,
  type ExecutionIdentity
} from './Identity'
import type { IdentityProvider } from './IdentityProvider'
import type { ExecutionContract } from '../governance/types'
import { immutableSnapshot } from '../crypto/canonical'

export interface IdentityContractGuard {
  contract: ExecutionContract
  assertCanUnlock(): Promise<void>
  clock?: () => number
}

export class IdentityManager {
  constructor(
    private readonly identityProvider: IdentityProvider,
    private readonly eventStore: EventStore
  ) {}

  async withIdentity<T>(
    intent: Intent,
    fn: (identity: ExecutionIdentity, signal?: AbortSignal) => Promise<T>,
    guard?: IdentityContractGuard
  ): Promise<T> {
    const now = guard?.clock ?? Date.now
    await guard?.assertCanUnlock()
    const identity = await this.identityProvider.issueIdentity(intent, guard?.contract)
    let timer: ReturnType<typeof setTimeout> | undefined
    const abort = new AbortController()

    try {
      if (guard) {
        identity.executionContract = immutableSnapshot(guard.contract)
        identity.expiresAt = Math.min(identity.expiresAt, guard.contract.temporalBounds.notAfter,
          now() + guard.contract.temporalBounds.maxDurationMs)
        await guard.assertCanUnlock()
      }
      assertIdentityCanExecute(intent, identity, now())
      await this.appendIdentityEvent(EventType.IdentityIssued, intent, identity,
        ['Ephemeral execution identity issued for the approved intent'])
      await guard?.assertCanUnlock()
      assertIdentityCanExecute(intent, identity, now())
      await this.appendIdentityEvent(
        EventType.IdentityUsed,
        intent,
        identity,
        [`Execution identity bound to intent type ${intent.type}`]
      )

      // Recheck after auditing too: slow storage must not extend the contract.
      await guard?.assertCanUnlock()
      assertIdentityCanExecute(intent, identity, now())
      if (!guard) return await fn(identity)
      const timeout = new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          abort.abort()
          reject(new Error('CONTRACT_EXECUTION_TIMEOUT_ERROR'))
        }, Math.max(0, Math.min(identity.expiresAt - now(), 2_147_483_647)))
      })
      return await Promise.race([fn(identity, abort.signal), timeout])
    } finally {
      if (timer) clearTimeout(timer)
      abort.abort()
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
