import { performance } from 'node:perf_hooks'
import { randomUUID } from 'node:crypto'
import { InMemoryIEECStore } from '../src/core/event/InMemoryIEECStore'
import { SQLiteIEECStore, type SQLiteDatabase } from '../src/core/event/SqlEventStore'
import type { IEECStore, TemporalRule } from '../src/core/governance/types'
import { evaluateTemporalRules } from '../src/core/governance/temporal'
import { EventType, type Intent } from '../src/interfaces/contracts'

async function benchmark(name: string, store: IEECStore): Promise<void> {
  const now = Date.now()
  const count = 25_000
  const action = `benchmark-${randomUUID()}`
  for (let batch = 0; batch < count; batch += 100) {
    await store.transaction(async tx => {
      for (let index = batch; index < batch + 100; index++) {
        const proposal: Intent = { id: randomUUID(), type: action, payload: { amount: 1 }, metadata: { actor: 'benchmark', timestamp: now } }
        await tx.append({ id: randomUUID(), type: EventType.ExecutionCompleted, intentId: proposal.id,
          timestamp: now - (count - index - 1) * 1_000, payload: { intentSnapshot: proposal, executionResult: { success: true } } })
      }
    })
  }
  const proposal: Intent = { id: randomUUID(), type: action, payload: { amount: 1 }, metadata: { actor: 'benchmark', timestamp: now } }
  const rules: TemporalRule[] = [{ type: 'SLIDING_WINDOW_QUOTA', targetAction: action, scope: 'GLOBAL',
    windowMs: 100_000, metricPath: 'intent.payload.amount', maxCumulativeValue: 500 }]
  const samples: number[] = []
  for (let run = 0; run < 250; run++) {
    const start = performance.now()
    const result = await evaluateTemporalRules(proposal, store, rules, now)
    const elapsed = performance.now() - start
    if (!result.allowed || result.budgets[0].used !== 100) throw new Error('Incorrect benchmark aggregate')
    if (run >= 50) samples.push(elapsed)
  }
  samples.sort((a, b) => a - b)
  const p95 = samples[Math.floor(samples.length * 0.95)]
  console.log(JSON.stringify({ backend: name, totalEvents: count, windowRecords: 100, samples: samples.length,
    p50Ms: Number(samples[100].toFixed(3)), p95Ms: Number(p95.toFixed(3)), targetMet: p95 < 5 }))
  if (p95 >= 5) process.exitCode = 1
}

async function main(): Promise<void> {
  await benchmark('memory', new InMemoryIEECStore())
  const { DatabaseSync } = require('node:sqlite') as { DatabaseSync: new (path: string) => SQLiteDatabase & { close(): void } }
  const database = new DatabaseSync(':memory:')
  try { await benchmark('sqlite', new SQLiteIEECStore(database)) } finally { database.close() }
}

void main().catch(error => { console.error(error); process.exitCode = 1 })
