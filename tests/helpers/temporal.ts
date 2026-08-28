import { randomUUID } from 'node:crypto'
import { EventType, type Intent } from '../../src/interfaces/contracts'
import type { IEECStore } from '../../src/core/governance/types'

export const SECRET = 'test-only-key-with-at-least-32-bytes'
export const NOW = 1_800_000_000_000

export function intent(action: string, payload: unknown = {}, overrides: Partial<Intent> = {}): Intent {
  return { id: randomUUID(), type: action, payload, metadata: { actor: 'agent-1', timestamp: NOW }, ...overrides }
}

export async function record(
  store: IEECStore, proposal: Intent, timestamp = NOW,
  status: 'SUCCESS' | 'FAILED' | 'RESERVED' = 'SUCCESS', result?: unknown
) {
  await store.append({ id: randomUUID(), intentId: proposal.id, timestamp,
    type: status === 'RESERVED' ? EventType.ExecutionReserved : EventType.ExecutionCompleted,
    payload: { intentSnapshot: proposal, ...(status !== 'RESERVED' ? { executionResult: { success: status === 'SUCCESS', result } } : {}) }
  })
  return (await store.getTrace(proposal.id))!
}
