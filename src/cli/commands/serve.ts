import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { randomUUID } from 'node:crypto'

import { printServerStarted } from '../utils/printer'
import {
  createCliRuntime,
  evaluateIntent,
  type CliFlags
} from '../utils/runtime'
import {
  loadOpenKedgeConfig,
  resolveServerHost,
  resolveServerPort
} from '../utils/loader'
import type { Intent } from '../../interfaces/contracts'

interface ServeFlags extends CliFlags {
  host?: string
  port?: string
}

export async function runServeCommand(
  cwd: string,
  flags: ServeFlags
): Promise<number> {
  const runtime = await createCliRuntime(cwd, flags)
  const { config } = await loadOpenKedgeConfig(cwd, flags.config)
  const host = resolveServerHost(config, flags.host)
  const port = resolveServerPort(config, flags.port)

  const server = createServer(async (request, response) => {
    try {
      if (request.method === 'POST' && request.url === '/intent') {
        const body = await readJsonBody(request)
        const mode = body.mode === 'preview' ? 'preview' : 'run'
        const intent = toIntent(
          toIntentPayload(body.intent ?? body),
          runtime.config.defaults?.actor
        )

        if (mode === 'preview') {
          const preview = await evaluateIntent(runtime, intent)
          return writeJson(response, 200, {
            mode,
            allowed: preview.allowed,
            blastRadius: preview.blastRadius,
            matchedRules: preview.matchedRules,
            reasons: preview.reasons
          })
        }

        const executionResult = await runtime.client.submitIntent(intent)
        const replay = await runtime.client.replayIntent(intent.id)

        return writeJson(response, executionResult.success ? 200 : 409, {
          mode,
          result: executionResult,
          replay
        })
      }

      if (request.method === 'GET' && request.url?.startsWith('/replay/')) {
        const intentId = decodeURIComponent(request.url.slice('/replay/'.length))
        const replay = await runtime.client.replayIntent(intentId)
        return writeJson(response, 200, replay)
      }

      return writeJson(response, 404, {
        error: 'Not found'
      })
    } catch (error) {
      return writeJson(response, 500, {
        error: error instanceof Error ? error.message : String(error)
      })
    }
  })

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, host, () => resolve())
  })

  printServerStarted(host, port)
  return await new Promise<number>(() => undefined)
}

function toIntent(
  input: Record<string, unknown>,
  defaultActor?: string
): Intent {
  if (typeof input.type !== 'string' || input.type.length === 0) {
    throw new Error('Intent payload must include a string "type" field')
  }

  return {
    id: typeof input.id === 'string' && input.id.length > 0 ? input.id : randomUUID(),
    type: input.type,
    payload: input.payload,
    metadata: {
      actor:
        typeof input.metadata === 'object' &&
        input.metadata !== null &&
        typeof (input.metadata as { actor?: unknown }).actor === 'string'
          ? ((input.metadata as { actor: string }).actor)
          : defaultActor ?? 'cli-user',
      timestamp:
        typeof input.metadata === 'object' &&
        input.metadata !== null &&
        typeof (input.metadata as { timestamp?: unknown }).timestamp === 'number'
          ? ((input.metadata as { timestamp: number }).timestamp)
          : Date.now()
    }
  }
}

function toIntentPayload(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null) {
    return {}
  }

  return value as Record<string, unknown>
}

async function readJsonBody(
  request: IncomingMessage
): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []

  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
  }

  if (chunks.length === 0) {
    return {}
  }

  return JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<
    string,
    unknown
  >
}

function writeJson(
  response: ServerResponse,
  statusCode: number,
  payload: unknown
): void {
  response.statusCode = statusCode
  response.setHeader('Content-Type', 'application/json')
  response.end(JSON.stringify(payload, null, 2))
}
