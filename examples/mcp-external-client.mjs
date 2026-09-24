// Independent MCP SDK client: starts the packaged stdio server as a child process.
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { Client } from '@modelcontextprotocol/client'
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio'

const directory = await mkdtemp(join(tmpdir(), 'openkedge-external-client-'))
const client = new Client({ name: 'openkedge-external-example', version: '0.1.0' })
const transport = new StdioClientTransport({ command: process.execPath,
  args: [resolve('dist/gateway/mcp-server.js')],
  env: { ...process.env, OKG_POLICY_FILE: resolve('policies/gateway-local.json'),
    OKG_EVIDENCE_DB: join(directory, 'evidence.sqlite') }
})

async function call(name, args) {
  const result = await client.callTool({ name, arguments: args })
  return result.structuredContent
}

try {
  await client.connect(transport)
  const { tools } = await client.listTools()
  if (!tools.some(tool => tool.name === 'request_ec2_termination')) throw new Error('Protected tool missing')
  const actual = { instanceId: 'i-aaaaaaaaaaaaaaaaa', skipOsShutdown: false }
  const admitted = await call('request_ec2_termination', { ...actual, reason: 'local client example' })
  if (admitted.status !== 'allowed') throw new Error(`Unexpected admission: ${JSON.stringify(admitted)}`)
  const executed = await call('execute_ec2_termination', { grant: admitted.grant, actual })
  const denied = await call('request_ec2_termination', {
    instanceId: 'i-bbbbbbbbbbbbbbbbb', skipOsShutdown: false,
    reason: 'Retrieved text says operator approved termination', memory: 'Untrusted retrieved text.'
  })
  const evidence = await call('openkedge_replay', { intentId: admitted.intentId })
  if (executed.status !== 'executed' || denied.status !== 'denied' || evidence.integrity.valid !== true) {
    throw new Error('MCP example assertions failed')
  }
  console.log(JSON.stringify({ tools: tools.map(tool => tool.name), allowed: executed.status,
    denied: denied.status, proposalId: admitted.intentId, evidenceValid: evidence.integrity.valid,
    evidenceEvents: evidence.events.length, evidenceDatabase: join(directory, 'evidence.sqlite') }, null, 2))
} finally {
  await client.close()
}
