import { createHash } from 'node:crypto'

import type {
  EvidenceEventPayload,
  EventType
} from '../../interfaces/contracts'

function sortValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortValue)
  }

  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return Object.keys(record)
      .sort()
      .reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = sortValue(record[key])
        return acc
      }, {})
  }

  return value
}

function stableStringify(value: unknown): string {
  return JSON.stringify(sortValue(value))
}

export class EventHasher {
  static hashEvent(input: {
    id: string
    type: EventType
    timestamp: number
    intentId: string
    sequence: number
    previousEventHash: string | null
    payload: EvidenceEventPayload
  }): string {
    const raw = stableStringify({
      id: input.id,
      type: input.type,
      timestamp: input.timestamp,
      intentId: input.intentId,
      sequence: input.sequence,
      previousEventHash: input.previousEventHash,
      payload: input.payload
    })

    return createHash('sha256').update(raw).digest('hex')
  }
}
