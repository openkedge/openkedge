import type { EvidenceEvent } from '../../interfaces/contracts'
import type { IEECRecord, IEECStore, IEECTransaction, TraceQuery } from '../governance/types'
import { immutableSnapshot } from '../crypto/canonical'
import { AsyncMutex, assertTraceTransition, finalizeEvent, projectTrace, type EventInput } from './trace'

export interface SqlConnection {
  query(sql: string, parameters?: unknown[]): Promise<{ rows: Record<string, unknown>[] }>
}

/** pg.Pool can be injected directly; no mandatory database driver dependency. */
export interface PostgresPool extends SqlConnection {
  connect(): Promise<SqlConnection & { release(): void }>
}

/** Compatible with node:sqlite DatabaseSync and better-sqlite3 Database. */
export interface SQLiteDatabase {
  exec(sql: string): unknown
  prepare(sql: string): {
    all(...parameters: (string | number | null)[]): unknown[]
    run(...parameters: (string | number | null)[]): unknown
  }
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS okg_events (
  event_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, sequence BIGINT NOT NULL,
  action TEXT NOT NULL, actor_id TEXT NOT NULL, ts BIGINT NOT NULL,
  trace_status TEXT, hash TEXT NOT NULL UNIQUE, data TEXT NOT NULL,
  UNIQUE(intent_id, sequence)
);
CREATE INDEX IF NOT EXISTS okg_trace_actor ON okg_events(action, actor_id, trace_status, ts);
CREATE INDEX IF NOT EXISTS okg_trace_global ON okg_events(action, trace_status, ts);
CREATE INDEX IF NOT EXISTS okg_trace_latest ON okg_events(intent_id, sequence) WHERE trace_status IS NOT NULL;
`

function parseEvent(row: Record<string, unknown>): EvidenceEvent {
  return immutableSnapshot(JSON.parse(String(row.data)) as EvidenceEvent)
}

class SqlTransaction implements IEECTransaction {
  active = true
  constructor(private readonly connection: SqlConnection, private readonly dialect: 'sqlite' | 'postgres') {}

  async rows(sql: string, values: unknown[] = []): Promise<Record<string, unknown>[]> {
    if (!this.active) throw new Error('Transaction is closed')
    let index = 0
    const statement = this.dialect === 'postgres' ? sql.replace(/\?/g, () => `$${++index}`) : sql
    return (await this.connection.query(statement, values)).rows
  }

  async append(input: EventInput): Promise<EvidenceEvent> {
    const previous = await this.rows('SELECT data FROM okg_events WHERE intent_id = ? ORDER BY sequence DESC LIMIT 1', [input.intentId])
    const event = finalizeEvent(input, previous.length ? parseEvent(previous[0]) : null)
    const trace = projectTrace(event)
    if (trace) assertTraceTransition(await this.getTrace(event.intentId), trace)
    await this.rows('INSERT INTO okg_events (event_id, intent_id, sequence, action, actor_id, ts, trace_status, hash, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [event.id, event.intentId, event.sequence, event.payload.intentSnapshot.type, event.payload.intentSnapshot.metadata.actor,
        event.timestamp, trace?.status ?? null, event.currentHash, JSON.stringify(event)])
    return event
  }

  async getEventsByIntent(id: string): Promise<EvidenceEvent[]> {
    return (await this.rows('SELECT data FROM okg_events WHERE intent_id = ? ORDER BY sequence', [id])).map(parseEvent)
  }
  async getTrace(id: string): Promise<IEECRecord | undefined> {
    const rows = await this.rows('SELECT data FROM okg_events WHERE intent_id = ? AND trace_status IS NOT NULL ORDER BY sequence DESC LIMIT 1', [id])
    return rows.length ? projectTrace(parseEvent(rows[0])) : undefined
  }
  async getTraceByHash(hash: string): Promise<IEECRecord | undefined> {
    const rows = await this.rows('SELECT data FROM okg_events WHERE hash = ?', [hash])
    return rows.length ? projectTrace(parseEvent(rows[0])) : undefined
  }
  async queryTrace(query: TraceQuery): Promise<IEECRecord[]> {
    const values: unknown[] = []
    const clauses = (status: 'SUCCESS' | 'RESERVED' | 'RUNNING'): string => {
      values.push(query.action, status, query.toInclusive)
      let where = 'e.action = ? AND e.trace_status = ? AND e.ts <= ?'
      if (query.actorId !== undefined) { where += ' AND e.actor_id = ?'; values.push(query.actorId) }
      if (status === 'SUCCESS') { where += ' AND e.ts > ?'; values.push(query.fromExclusive) }
      return `SELECT e.data FROM okg_events e WHERE ${where} AND NOT EXISTS (
        SELECT 1 FROM okg_events newer WHERE newer.intent_id = e.intent_id AND newer.trace_status IS NOT NULL AND newer.sequence > e.sequence
      )`
    }
    const success = clauses('SUCCESS')
    const sql = query.includeReservations ? `${success} UNION ALL ${clauses('RESERVED')} UNION ALL ${clauses('RUNNING')}` : success
    return (await this.rows(sql, values)).map(row => projectTrace(parseEvent(row))!)
  }
}

abstract class SqlEventStore implements IEECStore {
  protected abstract read<T>(work: (reader: SqlTransaction) => Promise<T>): Promise<T>
  abstract transaction<T>(work: (transaction: IEECTransaction) => Promise<T>): Promise<T>
  async append(input: EventInput): Promise<EvidenceEvent> { return this.transaction(tx => tx.append(input)) }
  async getEventsByIntent(id: string): Promise<EvidenceEvent[]> { return this.read(tx => tx.getEventsByIntent(id)) }
  async queryByIntent(id: string): Promise<EvidenceEvent[]> { return this.getEventsByIntent(id) }
  async getLastEventByIntent(id: string): Promise<EvidenceEvent | null> {
    return this.read(async tx => {
      const rows = await tx.rows('SELECT data FROM okg_events WHERE intent_id = ? ORDER BY sequence DESC LIMIT 1', [id])
      return rows.length ? parseEvent(rows[0]) : null
    })
  }
  async exportByIntent(id: string): Promise<string> { return JSON.stringify(await this.getEventsByIntent(id), null, 2) }
  async getTrace(id: string): Promise<IEECRecord | undefined> { return this.read(tx => tx.getTrace(id)) }
  async getTraceByHash(hash: string): Promise<IEECRecord | undefined> { return this.read(tx => tx.getTraceByHash(hash)) }
  async queryTrace(query: TraceQuery): Promise<IEECRecord[]> { return this.read(tx => tx.queryTrace(query)) }
}

const sqliteMutexes = new WeakMap<SQLiteDatabase, AsyncMutex>()

export class SQLiteIEECStore extends SqlEventStore {
  private readonly mutex: AsyncMutex
  private readonly connection: SqlConnection

  constructor(private readonly database: SQLiteDatabase, private readonly lockTimeoutMs = 5_000) {
    super()
    this.mutex = sqliteMutexes.get(database) ?? new AsyncMutex()
    sqliteMutexes.set(database, this.mutex)
    // Never synchronously wait on another connection owned by this event loop.
    database.exec('PRAGMA busy_timeout = 0; PRAGMA journal_mode = WAL;')
    database.exec(SCHEMA)
    this.connection = {
      query: async (sql, parameters = []) => {
        const statement = database.prepare(sql)
        const args = parameters as (string | number | null)[]
        if (/^\s*SELECT/i.test(sql)) return { rows: statement.all(...args) as Record<string, unknown>[] }
        statement.run(...args)
        return { rows: [] }
      }
    }
  }

  protected async read<T>(work: (reader: SqlTransaction) => Promise<T>): Promise<T> {
    return this.mutex.run(() => work(new SqlTransaction(this.connection, 'sqlite')))
  }

  async transaction<T>(work: (transaction: IEECTransaction) => Promise<T>): Promise<T> {
    return this.mutex.run(async () => {
      const start = Date.now()
      for (;;) {
        try { this.database.exec('BEGIN IMMEDIATE'); break } catch (error) {
          // Native sqlite errors can originate in another VM realm (e.g. Jest).
          const message = error && typeof error === 'object' && 'message' in error ? String(error.message) : String(error)
          if (!/busy|locked/i.test(message) || Date.now() - start >= this.lockTimeoutMs) throw error
          await new Promise(resolve => setTimeout(resolve, 2))
        }
      }
      const tx = new SqlTransaction(this.connection, 'sqlite')
      try {
        const result = await work(tx)
        this.database.exec('COMMIT')
        return result
      } catch (error) {
        this.database.exec('ROLLBACK')
        throw error
      } finally { tx.active = false }
    })
  }
}

export class PostgresIEECStore extends SqlEventStore {
  constructor(private readonly pool: PostgresPool) { super() }
  async initialize(): Promise<void> { await this.pool.query(SCHEMA) }
  protected async read<T>(work: (reader: SqlTransaction) => Promise<T>): Promise<T> {
    return work(new SqlTransaction(this.pool, 'postgres'))
  }
  async transaction<T>(work: (transaction: IEECTransaction) => Promise<T>): Promise<T> {
    const connection = await this.pool.connect()
    const tx = new SqlTransaction(connection, 'postgres')
    try {
      await connection.query('BEGIN ISOLATION LEVEL READ COMMITTED')
      // Every writer takes this lock, including GLOBAL and cross-resource rules.
      // External execution never holds a database transaction open.
      await connection.query("SET LOCAL lock_timeout = '5s'")
      await connection.query('SELECT pg_advisory_xact_lock(1869309745)')
      const result = await work(tx)
      await connection.query('COMMIT')
      return result
    } catch (error) {
      await connection.query('ROLLBACK')
      throw error
    } finally { tx.active = false; connection.release() }
  }
}

export { SQLiteIEECStore as SqliteIEECStore }
export { PostgresIEECStore as PostgresEventStore }
