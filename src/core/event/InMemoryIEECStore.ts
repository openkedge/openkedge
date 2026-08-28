import type { EvidenceEvent } from '../../interfaces/contracts'
import type { IEECRecord, IEECStore, IEECTransaction, TraceQuery } from '../governance/types'
import { AsyncMutex, assertTraceTransition, finalizeEvent, matchesQuery, projectTrace, type EventInput } from './trace'

export class InMemoryIEECStore implements IEECStore {
  private readonly events = new Map<string, EvidenceEvent[]>()
  private readonly hashes = new Map<string, EvidenceEvent>()
  private readonly eventIds = new Set<string>()
  private readonly latest = new Map<string, IEECRecord>()
  private readonly successes = new Map<string, IEECRecord[]>()
  private readonly reservations = new Map<string, Map<string, IEECRecord>>()
  private readonly mutex = new AsyncMutex()

  async transaction<T>(work: (transaction: IEECTransaction) => Promise<T>): Promise<T> {
    return this.mutex.run(async () => {
      const pending: EvidenceEvent[] = []
      const changed = new Map<string, IEECRecord>()
      let active = true
      const assertActive = (): void => { if (!active) throw new Error('Transaction is closed') }
      const tx: IEECTransaction = {
        append: async input => {
          assertActive()
          if (this.eventIds.has(input.id) || pending.some(event => event.id === input.id)) throw new Error('Duplicate evidence event ID')
          const previous = pending.filter(event => event.intentId === input.intentId).at(-1) ?? this.events.get(input.intentId)?.at(-1) ?? null
          const event = finalizeEvent(input, previous)
          const trace = projectTrace(event)
          if (trace) assertTraceTransition(changed.get(trace.proposalId) ?? this.latest.get(trace.proposalId), trace)
          pending.push(event)
          if (trace) changed.set(trace.proposalId, trace)
          return event
        },
        getEventsByIntent: async id => {
          assertActive()
          return [...(this.events.get(id) ?? []), ...pending.filter(event => event.intentId === id)]
        },
        getTrace: async id => { assertActive(); return changed.get(id) ?? this.latest.get(id) },
        getTraceByHash: async hash => {
          assertActive()
          const event = pending.find(item => item.currentHash === hash) ?? this.hashes.get(hash)
          return event ? projectTrace(event) : undefined
        },
        queryTrace: async query => {
          assertActive()
          return [...(await this.queryTrace(query)).filter(record => !changed.has(record.proposalId)),
            ...[...changed.values()].filter(record => matchesQuery(record, query))]
        }
      }
      try {
        const result = await work(tx)
        for (const event of pending) this.commit(event)
        return result
      } finally { active = false }
    })
  }

  async append(event: EventInput): Promise<EvidenceEvent> { return this.transaction(tx => tx.append(event)) }
  async queryByIntent(intentId: string): Promise<EvidenceEvent[]> { return this.getEventsByIntent(intentId) }
  async getEventsByIntent(intentId: string): Promise<EvidenceEvent[]> { return [...(this.events.get(intentId) ?? [])] }
  async getLastEventByIntent(intentId: string): Promise<EvidenceEvent | null> { return this.events.get(intentId)?.at(-1) ?? null }
  async exportByIntent(intentId: string): Promise<string> { return JSON.stringify(await this.getEventsByIntent(intentId), null, 2) }
  async getTrace(proposalId: string): Promise<IEECRecord | undefined> { return this.latest.get(proposalId) }
  async getTraceByHash(hash: string): Promise<IEECRecord | undefined> {
    const event = this.hashes.get(hash)
    return event ? projectTrace(event) : undefined
  }

  async queryTrace(query: TraceQuery): Promise<IEECRecord[]> {
    const key = JSON.stringify([query.action, query.actorId ?? null])
    const records = this.successes.get(key) ?? []
    let low = 0
    let high = records.length
    while (low < high) {
      const middle = (low + high) >>> 1
      if (records[middle].timestamp <= query.fromExclusive) low = middle + 1
      else high = middle
    }
    const result: IEECRecord[] = []
    for (let i = low; i < records.length && records[i].timestamp <= query.toInclusive; i++) {
      const record = records[i]
      if (this.latest.get(record.proposalId) === record) result.push(record)
    }
    if (query.includeReservations) {
      for (const record of this.reservations.get(key)?.values() ?? []) {
        if (record.timestamp <= query.toInclusive) result.push(record)
      }
    }
    return result
  }

  private commit(event: EvidenceEvent): void {
    const events = this.events.get(event.intentId) ?? []
    events.push(event)
    this.events.set(event.intentId, events)
    this.hashes.set(event.currentHash, event)
    this.eventIds.add(event.id)
    const record = projectTrace(event)
    if (!record) return
    this.latest.set(record.proposalId, record)
    for (const actor of [record.actorId, null]) {
      const key = JSON.stringify([record.action, actor])
      if (record.status === 'RESERVED') {
        const entries = this.reservations.get(key) ?? new Map<string, IEECRecord>()
        entries.set(record.proposalId, record)
        this.reservations.set(key, entries)
      } else {
        this.reservations.get(key)?.delete(record.proposalId)
        if (record.status === 'SUCCESS') {
          const entries = this.successes.get(key) ?? []
          let index = entries.length
          while (index > 0 && entries[index - 1].timestamp > record.timestamp) index--
          entries.splice(index, 0, record)
          this.successes.set(key, entries)
        }
      }
    }
  }
}
