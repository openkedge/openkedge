import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createRequire } from 'node:module'
import { Client } from '@modelcontextprotocol/client'
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio'

const require = createRequire(import.meta.url)
const { verifyBundle, validatePermitRequest } = require('../dist/control-plane/protocol.js')
const { parseProposal } = require('../dist/gateway/Gateway.js')
const fixture = async name => JSON.parse(await readFile(resolve(`conformance/fixtures/${name}.json`), 'utf8'))
const publicKey = await readFile(resolve('conformance/fixtures/test-only-public.pem'), 'utf8')
const a = verifyBundle(await fixture('valid-bundle-epoch1'), publicKey).bundle
const b = verifyBundle(await fixture('valid-bundle-epoch2'), publicKey).bundle
if (b.epoch <= a.epoch) throw new Error('Valid epochs did not advance')
for (const name of ['invalid-unsigned-bundle', 'invalid-tampered-bundle', 'invalid-malformed-bundle']) {
  try { verifyBundle(await fixture(name), publicKey); throw new Error(`${name} was accepted`) }
  catch (error) { if (String(error).includes('was accepted')) throw error }
}
const rollback = await fixture('invalid-rollback-sequence')
if (!(rollback[1].epoch < rollback[0].epoch)) throw new Error('Rollback fixture is invalid')
parseProposal(await fixture('valid-proposal'))
validatePermitRequest(await fixture('valid-permit'))
for (const [name, validate] of [['invalid-proposal', parseProposal], ['invalid-permit', validatePermitRequest]]) {
  try { validate(await fixture(name)); throw new Error(`${name} was accepted`) }
  catch (error) { if (String(error).includes('was accepted')) throw error }
}

if (!process.env.OKG_CONFORMANCE_GATEWAY_COMMAND) {
  console.log(JSON.stringify({ fixtures: 'passed', gateway: 'not configured' }))
  process.exit(0)
}

const command = process.env.OKG_CONFORMANCE_GATEWAY_COMMAND
const args = JSON.parse(process.env.OKG_CONFORMANCE_GATEWAY_ARGS ?? '[]')
const url = process.env.OKG_CONFORMANCE_CONTROLLER_URL
const adminToken = process.env.OKG_CONFORMANCE_ADMIN_TOKEN
const policyPath = process.env.OKG_CONFORMANCE_POLICY_FILE
const gatewayId = process.env.OKG_CONFORMANCE_GATEWAY_ID
if (!url || !adminToken || !policyPath || !gatewayId || !Array.isArray(args)) {
  throw new Error('Set controller URL, admin token, policy file and gateway ID for live conformance')
}
const env = { ...process.env }
delete env.OKG_CONFORMANCE_ADMIN_TOKEN
const client = new Client({ name: 'openkedge-conformance', version: '1.0.0' })
await client.connect(new StdioClientTransport({ command, args, env }))
const call = async (name, input = {}) => (await client.callTool({ name, arguments: input })).structuredContent
async function update(policy) {
  const reply = await fetch(new URL('/v1/policy', url), { method: 'POST',
    headers: { authorization: `Bearer ${adminToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ protocolVersion: 1, policy }) })
  const value = await reply.json()
  if (!reply.ok) throw new Error(`Policy update failed: ${JSON.stringify(value)}`)
  return value
}
try {
  const discovered = (await client.listTools()).tools.map(tool => tool.name)
  for (const name of ['request_ec2_termination', 'execute_ec2_termination', 'openkedge_policy_status', 'openkedge_replay']) {
    if (!discovered.includes(name)) throw new Error(`Missing MCP tool ${name}`)
  }
  const initialStatus = await call('openkedge_policy_status')
  const initialPolicy = JSON.parse(await readFile(resolve(policyPath), 'utf8'))
  const instanceId = initialPolicy.allowedInstanceIds[0]
  if (!instanceId) throw new Error('Conformance policy must allow one mock instance')
  const actual = { instanceId, skipOsShutdown: false }
  const admitted = await call('request_ec2_termination', actual)
  if (admitted.status !== 'allowed') throw new Error('Gateway did not admit the valid proposal')
  const substituted = await call('execute_ec2_termination', { grant: admitted.grant,
    actual: { instanceId: 'i-ccccccccccccccccc', skipOsShutdown: false } })
  if (substituted.status !== 'rejected') throw new Error('Gateway accepted altered target')
  const executed = await call('execute_ec2_termination', { grant: admitted.grant, actual })
  const completedReplay = await call('openkedge_replay', { intentId: admitted.intentId })
  if (executed.status !== 'executed' || !completedReplay.integrity.valid) throw new Error('Gateway failed permitted mock execution')
  const held = await call('request_ec2_termination', actual)
  if (held.status !== 'allowed') throw new Error('Gateway did not admit held grant')
  const deniedPolicy = { ...initialPolicy, version: `${initialPolicy.version}-conformance-${Date.now()}`,
    allowedInstanceIds: [] }
  const activation = await update(deniedPolicy)
  let status
  const deadline = Date.now() + 3_000
  do { status = await call('openkedge_policy_status') } while (status.epoch !== activation.epoch && Date.now() < deadline)
  if (status.epoch !== activation.epoch) throw new Error('Gateway did not activate controller epoch')
  const old = await call('execute_ec2_termination', { grant: held.grant, actual })
  const denied = await call('request_ec2_termination', actual)
  const replay = await call('openkedge_replay', { intentId: held.intentId })
  const controllerReply = await fetch(new URL('/v1/status', url), { headers: { authorization: `Bearer ${adminToken}` } })
  const controllerState = await controllerReply.json()
  const acked = controllerState.acknowledgements.some(a => a.gateway_id === gatewayId && a.epoch === activation.epoch)
  const outcome = controllerState.outcomes.find(o => o.proposal_id === admitted.intentId)
  const evidenceMatched = completedReplay.events.some(event => event.currentHash === outcome?.evidence_hash)
  if (old.code !== 'POLICY_VERSION_CONFLICT' || denied.status !== 'denied' || !replay.integrity.valid ||
    !acked || !evidenceMatched) {
    throw new Error('Gateway failed update, old-grant, denial or evidence checks')
  }
  console.log(JSON.stringify({ fixtures: 'passed', gateway: 'passed', gatewayId,
    initialEpoch: initialStatus.epoch, activatedEpoch: activation.epoch,
    alteredTarget: substituted.code, executed: executed.status, oldGrant: old.code,
    denial: denied.status, activationAcknowledged: acked, evidenceValid: true }))
} finally {
  await update(JSON.parse(await readFile(resolve(policyPath), 'utf8'))).catch(() => {})
  await client.close()
}
