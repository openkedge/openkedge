import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { readFile } from 'node:fs/promises'
import { randomUUID, timingSafeEqual } from 'node:crypto'
import { validatePolicy, type GatewayPolicy } from '../gateway/policy'
import { policyRevision, signBundle, validatePermitRequest, type PolicyBundle } from './protocol'

interface Database {
  exec(sql: string): void
  prepare(sql: string): { get(...args: (string | number)[]): any; all(...args: (string | number)[]): any[];
    run(...args: (string | number | null)[]): unknown }
}
interface ControllerConfig {
  databasePath: string
  privateKeyPath: string
  initialPolicyPath: string
  gatewayTokensPath: string
  adminToken: string
  host: string
  port: number
}

function equalSecret(actual: string, expected: string): boolean {
  const a = Buffer.from(actual), b = Buffer.from(expected)
  return a.length === b.length && timingSafeEqual(a, b)
}

async function jsonBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  let length = 0
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    length += bytes.length
    if (length > 1_000_000) throw new Error('REQUEST_TOO_LARGE')
    chunks.push(bytes)
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown
}

function send(response: ServerResponse, status: number, value: unknown): void {
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
  response.end(JSON.stringify(value))
}

export async function startController(config: ControllerConfig): Promise<ReturnType<typeof createServer>> {
  if (!config.adminToken || config.adminToken.length < 24) throw new Error('INVALID_CONTROLLER_ADMIN_TOKEN')
  if (!['127.0.0.1', 'localhost', '::1'].includes(config.host)) throw new Error('CONTROLLER_LOOPBACK_REQUIRED')
  const privateKey = await readFile(config.privateKeyPath, 'utf8')
  const tokens = JSON.parse(await readFile(config.gatewayTokensPath, 'utf8')) as Record<string, string>
  if (!tokens || typeof tokens !== 'object' || Object.keys(tokens).length < 1 ||
    Object.values(tokens).some(token => typeof token !== 'string' || token.length < 24)) throw new Error('INVALID_GATEWAY_TOKENS')
  const { DatabaseSync } = require('node:sqlite') as { DatabaseSync: new (path: string) => Database }
  const db = new DatabaseSync(config.databasePath)
  db.exec(`PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS controller_bundle (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS controller_ack (gateway_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, bundle_id TEXT NOT NULL, activated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS controller_permit (permit_id TEXT PRIMARY KEY, gateway_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
      contract_id TEXT NOT NULL, epoch INTEGER NOT NULL, expires_at INTEGER NOT NULL, state TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS controller_outcome (permit_id TEXT PRIMARY KEY, gateway_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
      contract_id TEXT NOT NULL, status TEXT NOT NULL, evidence_hash TEXT, observed_at INTEGER NOT NULL);`)
  if (!db.prepare('SELECT data FROM controller_bundle WHERE id=1').get()) {
    const policy = validatePolicy(JSON.parse(await readFile(config.initialPolicyPath, 'utf8')) as unknown).policy
    const bundle = signBundle(policy, 1, privateKey, Date.now(), 60_000)
    db.prepare('INSERT INTO controller_bundle (id,data) VALUES (1,?)').run(JSON.stringify(bundle))
  }
  const current = (): PolicyBundle => JSON.parse(db.prepare('SELECT data FROM controller_bundle WHERE id=1').get().data) as PolicyBundle
  const prune = () => db.prepare("UPDATE controller_permit SET state='expired' WHERE state='active' AND expires_at<=?").run(Date.now())
  const active = () => { prune(); return Number(db.prepare("SELECT count(*) AS n FROM controller_permit WHERE state='active'").get().n) }
  let updatePending = false

  const server = createServer(async (request, response) => {
    try {
      const route = new URL(request.url ?? '/', `http://${request.headers.host ?? 'localhost'}`).pathname
      const bearer = /^Bearer (.+)$/.exec(String(request.headers.authorization ?? ''))?.[1] ?? ''
      const gatewayId = String(request.headers['x-okg-gateway-id'] ?? '')
      const admin = equalSecret(bearer, config.adminToken)
      const gateway = Object.hasOwn(tokens, gatewayId) && equalSecret(bearer, tokens[gatewayId])
      if (!admin && !gateway) { send(response, 401, { code: 'UNAUTHORIZED' }); return }
      if (request.method === 'GET' && route === '/v1/policy' && gateway) { send(response, 200, current()); return }
      if (request.method === 'POST' && route === '/v1/ack' && gateway) {
        const body = await jsonBody(request) as Record<string, unknown>
        const bundle = current()
        if (body.protocolVersion !== 1 || body.bundleId !== bundle.bundleId || body.epoch !== bundle.epoch ||
          body.policyVersion !== policyRevision(bundle) || !Number.isSafeInteger(body.activatedAt)) {
          send(response, 409, { code: 'ACK_MISMATCH' }); return
        }
        db.prepare('INSERT INTO controller_ack (gateway_id,epoch,bundle_id,activated_at) VALUES (?,?,?,?) ON CONFLICT(gateway_id) DO UPDATE SET epoch=excluded.epoch,bundle_id=excluded.bundle_id,activated_at=excluded.activated_at')
          .run(gatewayId, bundle.epoch, bundle.bundleId, Number(body.activatedAt))
        send(response, 200, { accepted: true, epoch: bundle.epoch }); return
      }
      if (request.method === 'POST' && route === '/v1/permit' && gateway) {
        if (updatePending) { send(response, 409, { code: 'POLICY_UPDATE_PENDING' }); return }
        const body = validatePermitRequest(await jsonBody(request))
        const bundle = current(), policy = bundle.policy
        if (body.policyVersion !== policyRevision(bundle) || body.grantNotAfter <= Date.now() ||
          !policy.allowedInstanceIds.includes(body.instanceId) || policy.protectedInstanceIds.includes(body.instanceId) ||
          (body.skipOsShutdown && !policy.allowSkipOsShutdown)) {
          send(response, 409, { code: 'DISPATCH_POLICY_CONFLICT' }); return
        }
        const permitId = randomUUID()
        const expiresAt = Math.min(body.grantNotAfter, Date.now() + 5_000)
        db.prepare('INSERT INTO controller_permit (permit_id,gateway_id,proposal_id,contract_id,epoch,expires_at,state) VALUES (?,?,?,?,?,?,?)')
          .run(permitId, gatewayId, body.proposalId, body.contractId, bundle.epoch, expiresAt, 'active')
        send(response, 200, { permitId, expiresAt, epoch: bundle.epoch }); return
      }
      if (request.method === 'POST' && route === '/v1/outcome' && gateway) {
        const body = await jsonBody(request) as Record<string, unknown>
        const row = db.prepare('SELECT * FROM controller_permit WHERE permit_id=?').get(String(body.permitId ?? ''))
        if (!row || row.gateway_id !== gatewayId || row.proposal_id !== body.proposalId || row.contract_id !== body.contractId ||
          body.protocolVersion !== 1 || !['executed', 'validated', 'failed', 'uncertain'].includes(String(body.status)) ||
          !(body.evidenceHash === null || (typeof body.evidenceHash === 'string' && /^[a-f0-9]{64}$/.test(body.evidenceHash))) ||
          !Number.isSafeInteger(body.observedAt)) { send(response, 409, { code: 'OUTCOME_MISMATCH' }); return }
        const prior = db.prepare('SELECT * FROM controller_outcome WHERE permit_id=?').get(row.permit_id)
        if (prior) {
          if (prior.status !== body.status || prior.evidence_hash !== body.evidenceHash) {
            send(response, 409, { code: 'OUTCOME_CONFLICT' }); return
          }
          send(response, 200, { accepted: true, duplicate: true }); return
        }
        db.prepare("UPDATE controller_permit SET state='done' WHERE permit_id=? AND state='active'").run(row.permit_id)
        db.prepare('INSERT INTO controller_outcome (permit_id,gateway_id,proposal_id,contract_id,status,evidence_hash,observed_at) VALUES (?,?,?,?,?,?,?)')
          .run(row.permit_id, gatewayId, row.proposal_id, row.contract_id, String(body.status), body.evidenceHash as string | null, Number(body.observedAt))
        send(response, 200, { accepted: true }); return
      }
      if (request.method === 'POST' && route === '/v1/policy' && admin) {
        if (updatePending) { send(response, 409, { code: 'POLICY_UPDATE_PENDING' }); return }
        const body = await jsonBody(request) as Record<string, unknown>
        if (body.protocolVersion !== 1 || !Object.hasOwn(body, 'policy') || Object.keys(body).length !== 2) {
          send(response, 400, { code: 'INVALID_POLICY_UPDATE' }); return
        }
        let policy: GatewayPolicy
        try { policy = validatePolicy(body.policy).policy }
        catch { send(response, 400, { code: 'INVALID_POLICY_UPDATE' }); return }
        const requestedAt = Date.now()
        updatePending = true
        try {
          while (active() > 0) await new Promise(resolve => setTimeout(resolve, 5))
          const previous = current()
          const next = signBundle(policy, previous.epoch + 1, privateKey, Date.now(), 60_000)
          db.prepare('UPDATE controller_bundle SET data=? WHERE id=1').run(JSON.stringify(next))
          send(response, 200, { bundleId: next.bundleId, epoch: next.epoch, policyVersion: policyRevision(next),
            requestedAt, activatedAt: Date.now() }); return
        } finally { updatePending = false }
      }
      if (request.method === 'GET' && route === '/v1/status' && admin) {
        const bundle = current()
        send(response, 200, { epoch: bundle.epoch, bundleId: bundle.bundleId, policyVersion: policyRevision(bundle),
          acknowledgements: db.prepare('SELECT * FROM controller_ack').all(), activePermits: active(),
          outcomes: db.prepare('SELECT * FROM controller_outcome').all() }); return
      }
      send(response, 404, { code: 'NOT_FOUND' })
    } catch (error) {
      send(response, 400, { code: error instanceof Error ? error.message.split(':')[0] : 'REQUEST_FAILED' })
    }
  })
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(config.port, config.host, resolve) })
  return server
}

if (require.main === module) {
  const config: ControllerConfig = { databasePath: process.env.OKG_CONTROLLER_DB ?? '',
    privateKeyPath: process.env.OKG_CONTROLLER_PRIVATE_KEY ?? '', initialPolicyPath: process.env.OKG_CONTROLLER_INITIAL_POLICY ?? '',
    gatewayTokensPath: process.env.OKG_CONTROLLER_TOKENS_FILE ?? '', adminToken: process.env.OKG_CONTROLLER_ADMIN_TOKEN ?? '',
    host: process.env.OKG_CONTROLLER_HOST ?? '127.0.0.1', port: Number(process.env.OKG_CONTROLLER_PORT ?? 0) }
  startController(config).then(server => { process.stdout.write(JSON.stringify({ port: (server.address() as {port: number}).port }) + '\n') })
    .catch(() => { console.error('CONTROLLER_STARTUP_FAILED'); process.exitCode = 1 })
}
