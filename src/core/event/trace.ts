import { EventType, type EvidenceEvent } from '../../interfaces/contracts'
import type { IEECRecord, TraceQuery } from '../governance/types'
import { hashJson, immutableSnapshot } from '../crypto/canonical'
import { EventHasher } from './EventHasher'

export type EventInput = Omit<EvidenceEvent, 'sequence' | 'previousEventHash' | 'currentHash'>

export function finalizeEvent(input: EventInput, previous: EvidenceEvent | null): EvidenceEvent {
  const event = immutableSnapshot({ ...input, sequence: (previous?.sequence ?? 0) + 1, previousEventHash: previous?.currentHash ?? null })
  return immutableSnapshot({ ...event, currentHash: EventHasher.hashEvent(event) })
}

export function projectTrace(event: EvidenceEvent): IEECRecord | undefined {
  let status: IEECRecord['status']
  if (event.type === EventType.ExecutionReserved) status = 'RESERVED'
  else if (event.type === EventType.ExecutionStarted) status = 'RUNNING'
  else if (event.type === EventType.ExecutionCancelled) status = 'FAILED'
  else if (event.type === EventType.ExecutionCompleted) status = event.payload.executionResult?.success ? 'SUCCESS' : 'FAILED'
  else return undefined
  const intent = event.payload.intentSnapshot
  return Object.freeze({
    proposalId: event.intentId, actorId: intent.metadata.actor, action: intent.type,
    kind: intent.kind ?? 'MUTATION', timestamp: event.timestamp, status,
    intent, result: event.payload.executionResult?.result, hash: event.currentHash
  })
}

export function matchesQuery(record: IEECRecord, query: TraceQuery): boolean {
  return record.action === query.action && (query.actorId === undefined || record.actorId === query.actorId) &&
    record.timestamp <= query.toInclusive &&
    ((record.status === 'SUCCESS' && record.timestamp > query.fromExclusive) ||
      ((record.status === 'RESERVED' || record.status === 'RUNNING') && query.includeReservations === true))
}

export function assertTraceTransition(previous: IEECRecord | undefined, next: IEECRecord): void {
  if (!previous) return // Legacy successful executions need not have a reservation.
  if (!((previous.status === 'RESERVED' && next.status !== 'RESERVED') ||
    (previous.status === 'RUNNING' && (next.status === 'SUCCESS' || next.status === 'FAILED')))) {
    throw new Error('Execution trace is already finalized or reserved')
  }
  if (hashJson(previous.intent) !== hashJson(next.intent)) throw new Error('Execution must retain the reserved intent snapshot')
  if (next.timestamp < previous.timestamp) throw new Error('Execution completion cannot precede its reservation')
}

/** FIFO mutex. Database adapters additionally acquire a database-wide write lock. */
export class AsyncMutex {
  private tail: Promise<void> = Promise.resolve()

  async run<T>(work: () => Promise<T>): Promise<T> {
    const previous = this.tail
    let release!: () => void
    this.tail = new Promise<void>(resolve => { release = resolve })
    await previous
    try { return await work() } finally { release() }
  }
}
