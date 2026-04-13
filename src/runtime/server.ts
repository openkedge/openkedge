import type { Intent } from '../interfaces/contracts'
import { InMemoryEventStore } from '../core/event'
import { createOpenKedgeClient } from '../sdk/client'

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
    throw new Error(
      'Provide an intent with --intent \'{...}\' or pipe JSON into stdin.'
    )
  }

  const parsed = JSON.parse(rawIntent) as unknown

  if (!isIntent(parsed)) {
    throw new Error('Input must match the Intent contract.')
  }

  return parsed
}

async function main(): Promise<void> {
  const eventStore = new InMemoryEventStore()
  const client = createOpenKedgeClient({ eventStore })
  const intent = await readIntent(process.argv.slice(2))
  const result = await client.submitIntent(intent)
  const events = await eventStore.query({ intentId: intent.id })

  process.stdout.write(
    JSON.stringify(
      {
        result,
        events
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
