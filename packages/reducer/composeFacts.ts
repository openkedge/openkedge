import { DerivedState, Fact } from '../core'
import { resolveConflicts } from './resolveConflicts'

export function composeFacts(entityId: string, facts: Fact[]): DerivedState {
  // build final state
  const resolved = resolveConflicts(facts)
  return {
    entityId,
    facts: resolved,
    lastUpdated: Date.now()
  }
}
