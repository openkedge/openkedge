import type { Event, EventFilter, EventStore } from '../../interfaces/contracts'

export class InMemoryEventStore implements EventStore {
  private readonly events: Event[] = []

  async append(event: Event): Promise<void> {
    this.events.push(event)
  }

  async query(filter: EventFilter = {}): Promise<Event[]> {
    return this.events.filter((event) => {
      if (filter.intentId && event.intentId !== filter.intentId) {
        return false
      }

      if (filter.type && event.type !== filter.type) {
        return false
      }

      return true
    })
  }
}
