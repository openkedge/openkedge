import { randomUUID } from 'node:crypto'

import { createAwsAdapter } from '../adapters/aws'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { OpenKedgeClient } from '../sdk/client'

async function run(): Promise<void> {
  const adapter = createAwsAdapter()
  const engine = new OpenKedgeEngine(
    adapter.contextProvider,
    adapter.policyEvaluator,
    adapter.executor,
    new InMemoryEventStore()
  )
  const client = new OpenKedgeClient(engine)
  const instanceId =
    process.env.OPENKEDGE_AWS_INSTANCE_ID ?? 'i-xxxxxxxxxxxx'

  const result = await client.submitIntent({
    id: randomUUID(),
    type: 'ec2:TerminateInstances',
    payload: [instanceId],
    metadata: {
      actor: 'agent-1',
      timestamp: Date.now()
    }
  })

  console.log('FINAL RESULT:', result)
}

run().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : 'Unknown error'
  console.error(message)
  process.exitCode = 1
})
