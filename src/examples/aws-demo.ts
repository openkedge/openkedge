import { randomUUID } from 'node:crypto'

import { createAwsAdapter } from '../adapters/aws'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { IdentityManager } from '../core/identity/IdentityManager'
import { OpenKedgeClient } from '../sdk/client'

async function run(): Promise<void> {
  const store = new InMemoryEventStore()
  const adapter = createAwsAdapter({
    roleArn: process.env.OPENKEDGE_AWS_EXECUTION_ROLE_ARN,
    region: process.env.AWS_REGION ?? process.env.AWS_DEFAULT_REGION
  })
  const engine = new OpenKedgeEngine(
    adapter.contextProvider,
    adapter.policyEvaluator,
    adapter.executor,
    new IdentityManager(adapter.identityProvider, store),
    store
  )
  const client = new OpenKedgeClient(engine, store)
  const instanceId =
    process.env.OPENKEDGE_AWS_INSTANCE_ID ?? 'i-xxxxxxxxxxxx'

  const intent = {
    id: randomUUID(),
    type: 'ec2:TerminateInstances',
    payload: [instanceId],
    metadata: {
      actor: 'agent-1',
      timestamp: Date.now()
    }
  }

  const result = await client.submitIntent(intent)

  console.log('FINAL RESULT:', result)
  const replay = await client.replayIntent(intent.id)
  console.log('REPLAY OUTCOME:', replay.reconstructed.finalOutcome)
  console.log('REASONING TRAIL:')
  for (const line of replay.reasoningTrail) {
    console.log(`- ${line}`)
  }
  console.log('INTEGRITY:', replay.integrity.valid)
}

run().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : 'Unknown error'
  console.error(message)
  process.exitCode = 1
})
