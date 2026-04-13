import { randomUUID } from 'node:crypto'

import { DefaultContextProvider } from '../core/context/DefaultContextProvider'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { DefaultPolicyEvaluator } from '../core/evaluation/DefaultPolicyEvaluator'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { NoopExecutor } from '../core/execution/NoopExecutor'
import type { Intent } from '../interfaces/contracts'
import { OpenKedgeClient } from '../sdk/client'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isIntent(value: unknown): value is Intent {
  if (!isRecord(value) || !isRecord(value.metadata)) {
    return false
  }

  return (
    typeof value.id === 'string' &&
    typeof value.type === 'string' &&
    typeof value.metadata.actor === 'string' &&
    typeof value.metadata.timestamp === 'number' &&
    'payload' in value
  )
}

async function readFromStdin(): Promise<string> {
  const chunks: string[] = []

  for await (const chunk of process.stdin) {
    chunks.push(String(chunk))
  }

  return chunks.join('').trim()
}

async function readIntent(argv: string[]): Promise<Intent> {
  const intentFlagIndex = argv.indexOf('--intent')
  const rawIntent =
    intentFlagIndex >= 0 ? argv[intentFlagIndex + 1] : await readFromStdin()

  if (!rawIntent) {
    return {
      id: randomUUID(),
      type: 'test:intent',
      payload: { action: 'demo' },
      metadata: {
        actor: 'user',
        timestamp: Date.now()
      }
    }
  }

  const parsed = JSON.parse(rawIntent) as unknown

  if (!isIntent(parsed)) {
    throw new Error('Input must match the Intent contract.')
  }

  return parsed
}

async function main(): Promise<void> {
  const engine = new OpenKedgeEngine(
    new DefaultContextProvider(),
    new DefaultPolicyEvaluator(),
    new NoopExecutor(),
    new InMemoryEventStore()
  )
  const client = new OpenKedgeClient(engine)
  const intent = await readIntent(process.argv.slice(2))
  const result = await client.submitIntent(intent)

  process.stdout.write(
    JSON.stringify(
      {
        result
      },
      null,
      2
    ) + '\n'
  )
}

if (require.main === module) {
  main().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : 'Unknown error'
    process.stderr.write(`${message}\n`)
    process.exitCode = 1
  })
}
