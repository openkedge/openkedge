import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { InMemoryEventStore } from '../src/core/event/InMemoryEventStore'
import { PostgresIEECStore, SQLiteIEECStore, type PostgresPool, type SQLiteDatabase } from '../src/core/event/SqlEventStore'
import type { IEECStore, IEECTransaction } from '../src/core/governance/types'
import { ReplayEngine } from '../src/core/event/ReplayEngine'
import { EventType } from '../src/interfaces/contracts'
import { createDemoServer } from '../apps/demo-server/server'
import { intent, NOW, record, SECRET } from './helpers/temporal'

type Database = SQLiteDatabase & { close(): void }
// Optional drivers keep the core package portable; node:sqlite is available on Node >=22.13.
let DatabaseSync: (new (path: string) => Database) | undefined
try { DatabaseSync = (require('node:sqlite') as { DatabaseSync: new (path: string) => Database }).DatabaseSync } catch {}

function conformance(name: string, setup: () => Promise<{ store: IEECStore; cleanup(): Promise<void> }>) {
  describe(name, () => {
    let store: IEECStore
    let cleanup: () => Promise<void>
    beforeEach(async () => { ({ store, cleanup } = await setup()) })
    afterEach(async () => { await cleanup?.() })

    test('indexes windows, actor/global scope, hashes, and unresolved reservations', async () => {
      const source = intent('action', { amount: 1 })
      const pending = await record(store, source, NOW - 100, 'RESERVED')
      const successful = await record(store, source, NOW)
      const stuck = await record(store, intent('action'), NOW - 10_000, 'RESERVED')
      await record(store, intent('action', {}, { metadata: { actor: 'other', timestamp: NOW } }), NOW)
      await record(store, intent('action'), NOW + 1)
      const query = { action: 'action', fromExclusive: NOW - 10, toInclusive: NOW }
      expect(await store.queryTrace({ ...query, actorId: 'agent-1' })).toEqual([successful])
      expect(await store.queryTrace({ ...query, actorId: 'agent-1', includeReservations: true })).toEqual(expect.arrayContaining([successful, stuck]))
      expect(await store.queryTrace(query)).toHaveLength(2)
      expect(await store.getTraceByHash(pending.hash)).toEqual(pending)
      expect((await new ReplayEngine().replayIntent(await store.getEventsByIntent(source.id))).integrity.valid).toBe(true)
    })

    test('rolls back failed transactions and prevents leaked transaction use', async () => {
      const proposal = intent('action')
      let leaked: IEECTransaction | undefined
      await expect(store.transaction(async tx => {
        leaked = tx
        await tx.append({ id: randomUUID(), type: EventType.ExecutionReserved, timestamp: NOW, intentId: proposal.id, payload: { intentSnapshot: proposal } })
        expect((await tx.getTrace(proposal.id))?.status).toBe('RESERVED')
        throw new Error('rollback')
      })).rejects.toThrow('rollback')
      expect(await store.getEventsByIntent(proposal.id)).toEqual([])
      expect(await store.getTrace(proposal.id)).toBeUndefined()
      await expect(leaked!.getTrace(proposal.id)).rejects.toThrow('Transaction is closed')
    })

    test('does not expose mutable event or trace references', async () => {
      const proposal = intent('action', { amount: 1 })
      const trace = await record(store, proposal)
      ;(proposal.payload as { amount: number }).amount = 999
      const events = await store.getEventsByIntent(proposal.id)
      expect(() => { events[0].payload.intentSnapshot.type = 'changed' }).toThrow()
      expect(trace.intent.payload).toEqual({ amount: 1 })
      events.length = 0
      expect(await store.getEventsByIntent(proposal.id)).toHaveLength(1)
    })

    test('atomically checks shared quota across engines', async () => {
      const first = createDemoServer({ store, secretKey: SECRET })
      const second = createDemoServer({ store, secretKey: SECRET })
      const results = await Promise.all([first, second].map(({ client }, i) => client.submitIntent(intent('transfer', { amount: 300, accountId: 'shared' },
        { metadata: { actor: `agent-${i}`, timestamp: NOW } }))))
      expect(results.filter(result => result.success)).toHaveLength(1)
      expect(results.filter(result => result.errorCode === 'TEMPORAL_CONSTRAINT_ERROR')).toHaveLength(1)
    })

    test('cannot rewrite a reserved payload or refund a successful execution', async () => {
      const proposal = intent('action', { amount: 500 })
      await record(store, proposal, NOW, 'RESERVED')
      await expect(record(store, { ...proposal, payload: { amount: 0 } }, NOW)).rejects.toThrow('reserved intent snapshot')
      await record(store, proposal, NOW)
      await expect(record(store, proposal, NOW, 'FAILED')).rejects.toThrow('already finalized')
      expect((await store.getTrace(proposal.id))?.status).toBe('SUCCESS')
    })
  })
}

conformance('memory', async () => ({ store: new InMemoryEventStore(), async cleanup() {} }))

if (DatabaseSync) {
  conformance('SQLite', async () => {
    const database = new DatabaseSync!(':memory:')
    return { store: new SQLiteIEECStore(database), async cleanup() { database.close() } }
  })
}

const sqliteTest = DatabaseSync ? test : test.skip
sqliteTest('SQLite persists evidence and serializes two separate database connections', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'okg-sqlite-'))
  const path = join(directory, 'ieec.sqlite')
  const one = new DatabaseSync!(path)
  const two = new DatabaseSync!(path)
  try {
    const first = createDemoServer({ store: new SQLiteIEECStore(one), secretKey: SECRET })
    const second = createDemoServer({ store: new SQLiteIEECStore(two), secretKey: SECRET })
    const proposals = [intent('transfer', { amount: 300, accountId: 'shared' }), intent('transfer', { amount: 300, accountId: 'shared' })]
    const results = await Promise.all([first.client.submitIntent(proposals[0]), second.client.submitIntent(proposals[1])])
    expect(results.filter(result => result.success)).toHaveLength(1)
    expect(results.filter(result => result.errorCode === 'TEMPORAL_CONSTRAINT_ERROR')).toHaveLength(1)
    const reopened = new DatabaseSync!(path)
    try {
      const store = new SQLiteIEECStore(reopened)
      const winner = proposals[results.findIndex(result => result.success)]
      expect((await store.getTrace(winner.id))?.status).toBe('SUCCESS')
      expect((await new ReplayEngine().replayIntent(await store.getEventsByIntent(winner.id))).integrity.valid).toBe(true)
    } finally { reopened.close() }
  } finally { one.close(); two.close(); await rm(directory, { recursive: true, force: true }) }
})

// Real PostgreSQL conformance is opt-in and must use a disposable test database.
// Install pg in the test environment and set OPENKEDGE_TEST_POSTGRES_URL.
if (process.env.OPENKEDGE_TEST_POSTGRES_URL) {
  conformance('PostgreSQL', async () => {
    const { Pool } = require('pg') as { Pool: new (options: { connectionString: string; options?: string }) => PostgresPool & { end(): Promise<void> } }
    const root = new Pool({ connectionString: process.env.OPENKEDGE_TEST_POSTGRES_URL! })
    const schema = `okg_test_${randomUUID().replace(/-/g, '')}`
    await root.query(`CREATE SCHEMA ${schema}`)
    const pool = new Pool({ connectionString: process.env.OPENKEDGE_TEST_POSTGRES_URL!, options: `-c search_path=${schema}` })
    const store = new PostgresIEECStore(pool)
    await store.initialize()
    return { store, async cleanup() { await pool.end(); await root.query(`DROP SCHEMA ${schema} CASCADE`); await root.end() } }
  })
} else test.skip('PostgreSQL conformance requires OPENKEDGE_TEST_POSTGRES_URL', () => {})
