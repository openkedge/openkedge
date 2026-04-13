import type { Event, EventStore } from '../../interfaces/contracts'

export class InMemoryEventStore implements EventStore {
  private readonly events: Event[] = []

  async append(event: Event): Promise<void> {
    this.events.push(event)
  }

  async query(intentId: string): Promise<Event[]> {
    return this.events.filter((event) => event.intentId === intentId)
  }
}
