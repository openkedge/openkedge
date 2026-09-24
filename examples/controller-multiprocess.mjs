import { spawn } from 'node:child_process'
import { generateKeyPairSync, randomBytes } from 'node:crypto'
import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { Client } from '@modelcontextprotocol/client'
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio'

const directory = await mkdtemp(join(tmpdir(), 'openkedge-controller-'))
const policy = JSON.parse(await readFile(resolve('policies/gateway-local.json'), 'utf8'))
const { privateKey, publicKey } = generateKeyPairSync('ed25519')
const privatePath = join(directory, 'controller-private.pem')
const publicPath = join(directory, 'controller-public.pem')
const policyPath = join(directory, 'controller-bootstrap-policy.json')
const tokensPath = join(directory, 'gateway-tokens.json')
const dbPath = join(directory, 'controller.sqlite')
const adminToken = randomBytes(32).toString('hex')
const tokens = { one: randomBytes(32).toString('hex'), two: randomBytes(32).toString('hex') }
await Promise.all([
  writeFile(privatePath, privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 }),
  writeFile(publicPath, publicKey.export({ type: 'spki', format: 'pem' }), { mode: 0o600 }),
  writeFile(policyPath, JSON.stringify(policy), { mode: 0o600 }),
  writeFile(tokensPath, JSON.stringify(tokens), { mode: 0o600 })
])

const target = 'i-aaaaaaaaaaaaaaaaa'
const actual = { instanceId: target, skipOsShutdown: false }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const startMs = () => performance.now()
const latency = async (samples, work) => { const t = startMs(); const value = await work(); samples.push(+(startMs() - t).toFixed(2)); return value }
const admissions = [], redemptions = [], updateDelays = [], ackDelays = []
let falseRejections = 0
let controller, worker, client

function linesFrom(child) {
  const pending = []
  const waiting = []
  createInterface({ input: child.stdout }).on('line', line => {
    const value = JSON.parse(line)
    if (waiting.length) waiting.shift()(value)
    else pending.push(value)
  })
  return () => pending.length ? Promise.resolve(pending.shift()) : new Promise(resolve => waiting.push(resolve))
}

async function launchController(port = 0) {
  const child = spawn(process.execPath, [resolve('dist/control-plane/controller.js')], { env: { ...process.env,
    OKG_CONTROLLER_DB: dbPath, OKG_CONTROLLER_PRIVATE_KEY: privatePath, OKG_CONTROLLER_INITIAL_POLICY: policyPath,
    OKG_CONTROLLER_TOKENS_FILE: tokensPath, OKG_CONTROLLER_ADMIN_TOKEN: adminToken,
    OKG_CONTROLLER_PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'] })
  const next = linesFrom(child)
  child.stderr.on('data', () => {})
  const ready = await Promise.race([next(), new Promise((_, reject) => child.once('exit', code => reject(new Error(`Controller exited: ${code}`))))])
  if (!ready.port) throw new Error('Controller did not report a port')
  return { child, port: ready.port }
}

function gatewayEnv(id, port, delay = 0) {
  return { ...process.env, OKG_GATEWAY_ID: id, OKG_CALLER_ID: `agent-${id}`, OKG_DELEGATED_BY: 'test-operator',
    OKG_SIGNING_KEY_HEX: randomBytes(32).toString('hex'), OKG_EVIDENCE_DB: join(directory, `${id}-evidence.sqlite`),
    OKG_CONTROLLER_URL: `http://127.0.0.1:${port}`, OKG_CONTROLLER_GATEWAY_TOKEN: tokens[id],
    OKG_CONTROLLER_PUBLIC_KEY: publicPath, OKG_CONTROLLER_STATE_FILE: join(directory, `${id}-policy-cache.json`),
    OKG_CONTROLLER_TEST_DELAY_MS: String(delay) }
}

async function admin(port, path, method = 'GET', body) {
  const reply = await fetch(`http://127.0.0.1:${port}${path}`, { method,
    headers: { authorization: `Bearer ${adminToken}`, ...(body ? { 'content-type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined })
  const value = await reply.json()
  if (!reply.ok) throw new Error(`Controller ${path}: ${JSON.stringify(value)}`)
  return value
}
async function update(port, nextPolicy) {
  const t = startMs()
  const result = await admin(port, '/v1/policy', 'POST', { protocolVersion: 1, policy: nextPolicy })
  updateDelays.push(+(startMs() - t).toFixed(2))
  return result
}
async function waitFor(port, predicate, timeout = 3_000) {
  const until = Date.now() + timeout
  while (Date.now() < until) {
    const status = await admin(port, '/v1/status')
    if (predicate(status)) return status
    await sleep(10)
  }
  throw new Error('Controller condition timed out')
}
async function stop(child) {
  if (!child || child.exitCode !== null) return
  child.kill('SIGTERM')
  await new Promise(resolve => child.once('exit', resolve))
}

try {
  controller = await launchController()
  const port = controller.port
  client = new Client({ name: 'openkedge-controller-client-one', version: '0.1.0' })
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: [resolve('dist/gateway/mcp-server.js')], env: gatewayEnv('one', port, 300) }))
  worker = spawn(process.execPath, [resolve('examples/controller-client-worker.mjs')],
    { env: gatewayEnv('two', port), stdio: ['pipe', 'pipe', 'pipe'] })
  worker.stderr.on('data', () => {})
  const nextWorker = linesFrom(worker)
  if (!(await nextWorker()).ready) throw new Error('Second MCP client did not connect')
  let workerId = 0
  async function second(name, args = {}) {
    const id = ++workerId
    worker.stdin.write(JSON.stringify({ id, name, arguments: args }) + '\n')
    const reply = await nextWorker()
    if (reply.id !== id || reply.error) throw new Error(reply.error ?? 'Second client response mismatch')
    return reply.result
  }
  const first = async (name, args = {}) => (await client.callTool({ name, arguments: args })).structuredContent
  const toolsOne = (await client.listTools()).tools.map(t => t.name)
  const toolsTwo = (await second('listTools')).tools.map(t => t.name)
  if (!toolsOne.includes('request_ec2_termination') || !toolsTwo.includes('request_ec2_termination')) throw new Error('MCP tool discovery failed')
  const s1 = await first('openkedge_policy_status'), s2 = await second('openkedge_policy_status')
  if (s1.epoch !== 1 || s2.epoch !== 1) throw new Error('Initial epoch not activated')
  const initialAck = await waitFor(port, s => s.acknowledgements.length === 2)
  const unauthorizedUpdate = await fetch(`http://127.0.0.1:${port}/v1/policy`, { method: 'POST',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify({ protocolVersion: 1, policy }) })
  const malformedUpdate = await fetch(`http://127.0.0.1:${port}/v1/policy`, { method: 'POST',
    headers: { authorization: `Bearer ${adminToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ protocolVersion: 1, policy: { ...policy, protocolVersion: 999 } }) })
  if (unauthorizedUpdate.status !== 401 || malformedUpdate.status !== 400 ||
    (await admin(port, '/v1/status')).epoch !== 1) throw new Error('Controller accepted unauthorized or malformed update')
  const initial = await latency(admissions, () => first('request_ec2_termination', actual))
  const denial = await latency(admissions, () => second('request_ec2_termination',
    { instanceId: 'i-bbbbbbbbbbbbbbbbb', skipOsShutdown: false }))
  if (initial.status !== 'allowed' || denial.status !== 'denied') throw new Error('Initial admission/denial failed')
  const deniedPolicy = { ...policy, version: 'controller-denied', allowedInstanceIds: [] }
  let updateStarted = startMs()
  const epoch2 = await update(port, deniedPolicy)
  await first('openkedge_policy_status'); await second('openkedge_policy_status')
  const ack2 = await waitFor(port, s => s.acknowledgements.every(a => a.epoch === 2))
  ackDelays.push(+(startMs() - updateStarted).toFixed(2))
  const oldGrant = await latency(redemptions, () => first('execute_ec2_termination', { grant: initial.grant, actual }))
  if (oldGrant.code !== 'POLICY_VERSION_CONFLICT') throw new Error('Old grant survived policy activation')
  const allowedPolicy = { ...policy, version: 'controller-allowed-again' }
  updateStarted = startMs()
  const epoch3 = await update(port, allowedPolicy)
  await first('openkedge_policy_status'); await second('openkedge_policy_status')
  await waitFor(port, s => s.acknowledgements.every(a => a.epoch === 3))
  ackDelays.push(+(startMs() - updateStarted).toFixed(2))
  const racing = await latency(admissions, () => first('request_ec2_termination', actual))
  const staleAdmission = await latency(admissions, () => second('request_ec2_termination', actual))
  if (racing.status !== 'allowed' || staleAdmission.status !== 'allowed') throw new Error('Race grant denied before update')
  const inFlight = latency(redemptions, () => first('execute_ec2_termination', { grant: racing.grant, actual }))
  await waitFor(port, s => s.activePermits === 1)
  updateStarted = startMs()
  const pendingUpdate = update(port, deniedPolicy)
  await sleep(30)
  const during = await admin(port, '/v1/status')
  if (during.epoch !== 3) throw new Error('Policy activated during fenced dispatch')
  const duringAdmission = await latency(admissions, () => second('request_ec2_termination', actual))
  if (duringAdmission.status !== 'allowed') falseRejections++
  const pendingRedemption = await latency(redemptions, () => second('execute_ec2_termination',
    { grant: duringAdmission.grant, actual }))
  if (pendingRedemption.code !== 'POLICY_UPDATE_PENDING') throw new Error('Controller issued a new permit during update')
  falseRejections++
  const racedOutcome = await inFlight
  const epoch4 = await pendingUpdate
  if (racedOutcome.status !== 'executed' || epoch4.epoch !== 4) throw new Error('Fenced dispatch result mismatch')
  await first('openkedge_policy_status'); await second('openkedge_policy_status')
  await waitFor(port, s => s.acknowledgements.every(a => a.epoch === 4))
  ackDelays.push(+(startMs() - updateStarted).toFixed(2))
  const stale = await latency(redemptions, () => second('execute_ec2_termination', { grant: staleAdmission.grant, actual }))
  if (stale.code !== 'POLICY_VERSION_CONFLICT') throw new Error('Stale second-gateway grant survived')
  await stop(controller.child)
  const outageStatus = await first('openkedge_policy_status')
  const outageAdmission = await latency(admissions, () => second('request_ec2_termination', actual))
  if (outageStatus.controllerReachable !== false || outageAdmission.code !== 'CONTROLLER_UNAVAILABLE') {
    throw new Error('Consequential action did not fail closed during outage')
  }
  controller = await launchController(port)
  const reconnectOne = await first('openkedge_policy_status'), reconnectTwo = await second('openkedge_policy_status')
  if (reconnectOne.epoch !== 4 || reconnectTwo.epoch !== 4) throw new Error('Reconnect lost controller epoch')
  updateStarted = startMs()
  const epoch5 = await update(port, { ...policy, version: 'controller-reconnected' })
  await first('openkedge_policy_status'); await second('openkedge_policy_status')
  await waitFor(port, s => s.acknowledgements.every(a => a.epoch === 5))
  ackDelays.push(+(startMs() - updateStarted).toFixed(2))
  const allowedAgain = await latency(admissions, () => second('request_ec2_termination', actual))
  const secondOutcome = await latency(redemptions, () => second('execute_ec2_termination', { grant: allowedAgain.grant, actual }))
  if (allowedAgain.status !== 'allowed' || secondOutcome.status !== 'executed') throw new Error('Reconnect execution failed')
  const revokedByEpoch = await latency(admissions, () => second('request_ec2_termination', actual))
  if (revokedByEpoch.status !== 'allowed') throw new Error('Expected grant before same-policy epoch advance')
  updateStarted = startMs()
  const epoch6 = await update(port, { ...policy, version: 'controller-reconnected' })
  await first('openkedge_policy_status'); await second('openkedge_policy_status')
  await waitFor(port, s => s.acknowledgements.every(a => a.epoch === 6))
  ackDelays.push(+(startMs() - updateStarted).toFixed(2))
  const samePolicyOldGrant = await latency(redemptions, () => second('execute_ec2_termination',
    { grant: revokedByEpoch.grant, actual }))
  if (samePolicyOldGrant.code !== 'POLICY_VERSION_CONFLICT') throw new Error('Unchanged policy epoch did not revoke old grant')
  const replayOne = await first('openkedge_replay', { intentId: racing.intentId })
  const replayTwo = await second('openkedge_replay', { intentId: allowedAgain.intentId })
  const deniedReplay = await second('openkedge_replay', { intentId: denial.intentId })
  const finalStatus = await admin(port, '/v1/status')
  const hashes = [replayOne, replayTwo].flatMap(replay => replay.events.map(event => event.currentHash))
  const evidenceMatched = finalStatus.outcomes.filter(o => hashes.includes(o.evidence_hash)).length
  if (evidenceMatched !== 2 || !replayOne.integrity.valid || !replayTwo.integrity.valid || !deniedReplay.integrity.valid) {
    throw new Error('Evidence reconciliation failed')
  }
  const conformanceEnv = { ...gatewayEnv('one', port),
    OKG_EVIDENCE_DB: join(directory, 'conformance-evidence.sqlite'),
    OKG_CONTROLLER_STATE_FILE: join(directory, 'conformance-policy-cache.json'),
    OKG_CONFORMANCE_GATEWAY_COMMAND: process.execPath,
    OKG_CONFORMANCE_GATEWAY_ARGS: JSON.stringify([resolve('dist/gateway/mcp-server.js')]),
    OKG_CONFORMANCE_GATEWAY_ID: 'one', OKG_CONFORMANCE_CONTROLLER_URL: `http://127.0.0.1:${port}`,
    OKG_CONFORMANCE_ADMIN_TOKEN: adminToken, OKG_CONFORMANCE_POLICY_FILE: policyPath }
  const conformance = await new Promise((resolveResult, reject) => {
    const child = spawn(process.execPath, [resolve('conformance/run.mjs')],
      { env: conformanceEnv, stdio: ['ignore', 'pipe', 'pipe'] })
    let output = '', error = ''
    child.stdout.on('data', chunk => { output += String(chunk) })
    child.stderr.on('data', chunk => { error += String(chunk) })
    child.on('exit', code => code === 0 ? resolveResult(JSON.parse(output.trim())) : reject(new Error(`Conformance runner failed: ${error}`)))
  })
  if (conformance.gateway !== 'passed') throw new Error('Live conformance runner failed')
  console.log(JSON.stringify({ conditions: { controllerProcesses: 2, gatewayProcessesDuringMeasurement: 2,
      conformanceGatewayProcessesAfterMeasurement: 1, mcpClientProcessesDuringMeasurement: 2,
      transport: 'loopback HTTP plus MCP stdio', storage: 'controller SQLite plus separate gateway SQLite/cache files',
      action: 'mock ec2:TerminateInstances', injectedPreApiDelayMs: 300 },
    epochs: [initialAck.epoch, epoch2.epoch, epoch3.epoch, epoch4.epoch, epoch5.epoch, epoch6.epoch],
    cases: { admission: initial.status, denial: denial.status, activationAcks: ack2.acknowledgements.length,
      oldGrant: oldGrant.code, duringFinalCheckGap: racedOutcome.status, updateWaitedMs: updateDelays[2],
      pendingRedemption: pendingRedemption.code, secondOldGrant: stale.code,
      samePolicyEpochRevocation: samePolicyOldGrant.code, liveConformance: conformance.gateway,
      outage: outageAdmission.code, reconnect: secondOutcome.status },
    metrics: { controllerActivationMs: updateDelays, gatewayAckDelayMs: ackDelays,
      admissionLatencyMs: admissions, redemptionLatencyMs: redemptions,
      falseRejectionsDuringRollout: falseRejections, evidenceCompleteness: `${evidenceMatched}/2` },
    evidenceDirectory: directory }, null, 2))
  await second('close')
} finally {
  await client?.close().catch(() => {})
  if (worker) await stop(worker)
  if (controller) await stop(controller.child)
}
