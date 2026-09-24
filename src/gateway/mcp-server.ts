import { resolve } from 'node:path'
import { McpServer } from '@modelcontextprotocol/server'
import { serveStdio } from '@modelcontextprotocol/server/stdio'
import * as z from 'zod'
import { SQLiteIEECStore } from '../core/event/SqlEventStore'
import type { ExecutionContract } from '../core/governance/types'
import { ExecutionGateway, MockTerminationAdapter } from './Gateway'
import { FilePolicySource } from './policy'
import { loadGatewayConfig } from './config'

const policyPath = resolve(process.env.OKG_POLICY_FILE ?? 'policies/gateway-local.json')
const { DatabaseSync } = require('node:sqlite') as { DatabaseSync: new (path: string) => import('../core/event/SqlEventStore').SQLiteDatabase }
const evidencePath = resolve(process.env.OKG_EVIDENCE_DB ?? '.openkedge-gateway.sqlite')
let config: ReturnType<typeof loadGatewayConfig>
try { config = loadGatewayConfig(process.env) }
catch (error) {
  // Never print environment values, especially key material.
  console.error(error instanceof Error ? error.message : 'INVALID_GATEWAY_CONFIG')
  process.exit(1)
}
const gatewayId = config.gatewayId
const store = new SQLiteIEECStore(new DatabaseSync(evidencePath))
const gateway = new ExecutionGateway(new FilePolicySource(policyPath), new MockTerminationAdapter(), config.signingKey,
  config, store)

const grantSchema = z.object({
  contractId: z.string(), proposalId: z.string(), actorId: z.string(), action: z.string(), policyVersion: z.string(),
  intentHash: z.string(), issuedAt: z.number().int(),
  temporalBounds: z.object({ notBefore: z.number().int(), notAfter: z.number().int(), maxDurationMs: z.number().int() }).strict(),
  temporalValidity: z.object({ validAfter: z.number().int(), validBefore: z.number().int() }).strict(),
  linkedCapabilities: z.array(z.string()), capabilities: z.array(z.object({
    tokenId: z.string(), sourceProposalId: z.string(), sourceHash: z.string(), issuedAt: z.number().int(),
    expiresAt: z.number().int(), actorId: z.string(), boundAttributes: z.record(z.string(), z.unknown()), signature: z.string()
  }).strict()),
  preconditionHashes: z.array(z.string()), signature: z.string()
}).strict()

const proposalSchema = z.object({
  instanceId: z.string().regex(/^i-[a-f0-9]{17}$/), skipOsShutdown: z.boolean(),
  reason: z.string().max(2000).optional(), memory: z.string().max(4000).optional()
}).strict()
const actualSchema = z.object({ instanceId: z.string().regex(/^i-[a-f0-9]{17}$/), skipOsShutdown: z.boolean() }).strict()

function response(value: object, isError = false) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(value) }], structuredContent: value,
    ...(isError ? { isError: true } : {}) }
}

function createServer(): McpServer {
  const server = new McpServer({ name: `openkedge-${gatewayId}`, version: '0.1.0' })
  server.registerTool('request_ec2_termination', {
    description: 'Submit an untrusted EC2 termination proposal for policy admission. Returns a short-lived one-use grant or denial. Reason and memory are not approval evidence.',
    inputSchema: proposalSchema,
    annotations: { destructiveHint: true, readOnlyHint: false }
  }, async args => {
    const result = await gateway.admit(args)
    return response(result, result.status !== 'allowed')
  })
  server.registerTool('execute_ec2_termination', {
    description: 'Redeem an admitted grant for exactly one mock EC2 termination. The actual operation and parameters are checked again.',
    inputSchema: z.object({ grant: grantSchema, actual: actualSchema }).strict(),
    annotations: { destructiveHint: true, readOnlyHint: false }
  }, async ({ grant, actual }) => {
    const result = await gateway.execute(grant as ExecutionContract, actual)
    return response(result, result.status !== 'executed')
  })
  server.registerTool('openkedge_policy_status', {
    description: 'Read the authoritative policy version currently enforced by this gateway.',
    inputSchema: z.object({}).strict(), annotations: { readOnlyHint: true }
  }, async () => {
    try { return response({ gatewayId, ...await gateway.status() }) }
    catch (error) { return response({ gatewayId, code: 'POLICY_UNAVAILABLE', reason: String(error) }, true) }
  })
  server.registerTool('openkedge_replay', {
    description: 'Read the IEEC evidence and integrity result for a proposal ID.',
    inputSchema: z.object({ intentId: z.string() }).strict(), annotations: { readOnlyHint: true }
  }, async ({ intentId }) => {
    try { return response(await gateway.replay(intentId)) }
    catch { return response({ code: 'EVIDENCE_ACCESS_DENIED', reason: 'Evidence unavailable to this launcher-attested caller' }, true) }
  })
  return server
}

gateway.status().then(() => { serveStdio(createServer) }).catch(() => {
  console.error('POLICY_UNAVAILABLE: Gateway policy is unavailable or malformed at startup')
  process.exitCode = 1
})
