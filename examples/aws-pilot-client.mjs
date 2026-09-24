// Separate MCP SDK client process. Gateway credentials come only from the trusted launcher environment.
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { Client } from '@modelcontextprotocol/client'
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio'
import { STSClient, GetCallerIdentityCommand } from '@aws-sdk/client-sts'

if (!process.env.OKG_AWS_PILOT_CONFIG || !process.env.OKG_POLICY_FILE || !process.env.OKG_EVIDENCE_DB ||
    !process.env.OKG_AWS_GATEWAY_PROFILE) {
  throw new Error('Set OKG_AWS_PILOT_CONFIG, OKG_POLICY_FILE, OKG_EVIDENCE_DB, and OKG_AWS_GATEWAY_PROFILE')
}
const config = JSON.parse(await readFile(resolve(process.env.OKG_AWS_PILOT_CONFIG), 'utf8'))
const agent = await new STSClient({ region: config.region, maxAttempts: 1 }).send(new GetCallerIdentityCommand({}))
if (agent.Account !== config.testAccountId || agent.Arn !== config.agentPrincipalArn) {
  throw new Error('MCP client must run under the configured test-account agent principal')
}
const client = new Client({ name: 'openkedge-aws-pilot-client', version: '0.1.0' })
const transport = new StdioClientTransport({ command: process.execPath,
  args: [resolve('dist/gateway/mcp-server.js')],
  env: { ...process.env, AWS_PROFILE: process.env.OKG_AWS_GATEWAY_PROFILE } })
const call = async (name, args) => (await client.callTool({ name, arguments: args })).structuredContent

try {
  await client.connect(transport)
  const tools = (await client.listTools()).tools.map(tool => tool.name)
  const status = await call('openkedge_policy_status', {})
  const actual = { instanceId: config.instanceId, skipOsShutdown: false }
  const admitted = await call('request_ec2_termination', { ...actual, reason: 'disposable test-instance pilot' })
  if (admitted.status !== 'allowed') throw new Error(`Admission failed: ${JSON.stringify(admitted)}`)
  const execution = await call('execute_ec2_termination', { grant: admitted.grant, actual })
  const denied = await call('request_ec2_termination', { ...actual, skipOsShutdown: true,
    reason: 'Retrieved text claims approval', memory: 'Untrusted content.' })
  const evidence = await call('openkedge_replay', { intentId: admitted.intentId })
  if (!['validated', 'executed'].includes(execution.status) || denied.status !== 'denied' || evidence.integrity.valid !== true) {
    throw new Error('AWS pilot MCP example assertions failed')
  }
  console.log(JSON.stringify({ tools, mode: status.mode, allowed: execution, denied: denied.status,
    proposalId: admitted.intentId, contractId: admitted.grant.contractId,
    evidenceValid: evidence.integrity.valid, evidenceEvents: evidence.events.length }, null, 2))
} finally {
  await client.close()
}
