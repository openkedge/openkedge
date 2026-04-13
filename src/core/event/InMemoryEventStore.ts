import type {
  EvidenceEvent,
  EventStore
} from '../../interfaces/contracts'

import { EventHasher } from './EventHasher'

export class InMemoryEventStore implements EventStore {
  private readonly events: EvidenceEvent[] = []

  async append(
    event: Omit<EvidenceEvent, 'sequence' | 'previousEventHash' | 'currentHash'>
  ): Promise<EvidenceEvent> {
    const intentEvents = this.events
      .filter((existing) => existing.intentId === event.intentId)
      .sort((a, b) => a.sequence - b.sequence)

    const previous = intentEvents[intentEvents.length - 1] ?? null
    const sequence = previous ? previous.sequence + 1 : 1
    const previousEventHash = previous?.currentHash ?? null
    const currentHash = EventHasher.hashEvent({
      id: event.id,
      type: event.type,
      timestamp: event.timestamp,
      intentId: event.intentId,
      sequence,
      previousEventHash,
      payload: event.payload
    })

    const finalized: EvidenceEvent = {
      ...event,
      sequence,
      previousEventHash,
      currentHash
    }

    this.events.push(finalized)
    return finalized
  }

  async queryByIntent(intentId: string): Promise<EvidenceEvent[]> {
    return this.events
      .filter((event) => event.intentId === intentId)
      .sort((a, b) => a.sequence - b.sequence)
  }

  async getEventsByIntent(intentId: string): Promise<EvidenceEvent[]> {
    return this.queryByIntent(intentId)
  }

  async getLastEventByIntent(intentId: string): Promise<EvidenceEvent | null> {
    const events = await this.queryByIntent(intentId)
    return events.length > 0 ? events[events.length - 1] : null
  }

  async exportByIntent(intentId: string): Promise<string> {
    const events = await this.queryByIntent(intentId)
    return JSON.stringify(events, null, 2)
  }
}
