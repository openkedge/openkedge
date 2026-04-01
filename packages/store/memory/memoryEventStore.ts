import { TruthEvent, DerivedState } from '../../core'
import { EventStore } from '../interfaces'

export class MemoryEventStore implements EventStore {
  private events: TruthEvent[] = []

  append(event: TruthEvent) {
    this.events.push(event)
  }

  getEvents(entityId: string) {
    return this.events.filter(e => e.entityId === entityId)
  }
}
