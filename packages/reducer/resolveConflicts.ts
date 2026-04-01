import { Fact } from '../core'

export function resolveConflicts(facts: Fact[]): Fact[] {
  // last-writer-wins logic or similar map aggregation
  const resolutionMap = new Map<string, Fact>()
  for (const f of facts) {
    resolutionMap.set(f.type, f)
  }
  return Array.from(resolutionMap.values())
}
