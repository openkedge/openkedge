import { TruthEvent, DerivedState } from '../core'

export interface EventStore {
  append(event: TruthEvent): void
  getEvents(entityId: string): TruthEvent[]
  getDerivedState?(entityId: string): DerivedState | null
}
