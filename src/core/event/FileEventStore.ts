import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

import type {
  EvidenceEvent,
  EventStore
} from '../../interfaces/contracts'
import { EventHasher } from './EventHasher'

function encodeIntentId(intentId: string): string {
  return encodeURIComponent(intentId)
}

export class FileEventStore implements EventStore {
  constructor(private readonly directory: string) {}

  async append(
    event: Omit<EvidenceEvent, 'sequence' | 'previousEventHash' | 'currentHash'>
  ): Promise<EvidenceEvent> {
    await this.ensureDirectory()

    const intentEvents = await this.queryByIntent(event.intentId)
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

    await writeFile(
      this.getIntentFilePath(event.intentId),
      JSON.stringify([...intentEvents, finalized], null, 2),
      'utf8'
    )

    return finalized
  }

  async queryByIntent(intentId: string): Promise<EvidenceEvent[]> {
    try {
      const raw = await readFile(this.getIntentFilePath(intentId), 'utf8')
      const parsed = JSON.parse(raw) as EvidenceEvent[]
      return parsed.sort((left, right) => left.sequence - right.sequence)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        return []
      }

      throw error
    }
  }

  async getEventsByIntent(intentId: string): Promise<EvidenceEvent[]> {
    return this.queryByIntent(intentId)
  }

  async getLastEventByIntent(intentId: string): Promise<EvidenceEvent | null> {
    const events = await this.queryByIntent(intentId)
    return events.at(-1) ?? null
  }

  async exportByIntent(intentId: string): Promise<string> {
    const events = await this.queryByIntent(intentId)
    return JSON.stringify(events, null, 2)
  }

  private getIntentFilePath(intentId: string): string {
    return path.join(this.directory, `${encodeIntentId(intentId)}.json`)
  }

  private async ensureDirectory(): Promise<void> {
    await mkdir(this.directory, {
      recursive: true
    })
  }
}
