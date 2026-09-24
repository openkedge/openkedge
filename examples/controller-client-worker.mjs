// Independent MCP SDK client process used by the controller conformance scenario.
import { createInterface } from 'node:readline'
import { resolve } from 'node:path'
import { Client } from '@modelcontextprotocol/client'
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio'

const client = new Client({ name: 'openkedge-second-controller-client', version: '0.1.0' })
await client.connect(new StdioClientTransport({ command: process.execPath,
  args: [resolve('dist/gateway/mcp-server.js')], env: { ...process.env } }))
process.stdout.write(JSON.stringify({ ready: true }) + '\n')
const lines = createInterface({ input: process.stdin })
for await (const line of lines) {
  try {
    const message = JSON.parse(line)
    const result = message.name === 'listTools' ? await client.listTools()
      : message.name === 'close' ? await client.close()
        : await client.callTool({ name: message.name, arguments: message.arguments ?? {} })
    process.stdout.write(JSON.stringify({ id: message.id, result: message.name === 'listTools' ? result
      : message.name === 'close' ? { closed: true } : result.structuredContent }) + '\n')
    if (message.name === 'close') break
  } catch (error) {
    process.stdout.write(JSON.stringify({ id: JSON.parse(line).id, error: error instanceof Error ? error.message : String(error) }) + '\n')
  }
}
