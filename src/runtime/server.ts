import { randomUUID } from 'node:crypto'

import { DefaultContextProvider } from '../core/context/DefaultContextProvider'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { DefaultPolicyEvaluator } from '../core/evaluation/DefaultPolicyEvaluator'
import { NoopExecutor } from '../core/execution/NoopExecutor'
import { OpenKedgeClient } from '../sdk/client'

async function main(): Promise<void> {
  const eventStore = new InMemoryEventStore()
  const engine = new OpenKedgeEngine(
    new DefaultContextProvider(),
    new DefaultPolicyEvaluator(),
    new NoopExecutor(),
    eventStore
  )
  const client = new OpenKedgeClient(engine, eventStore)

  const intent = {
    id: randomUUID(),
    type: 'demo:update-resource',
    payload: {
      resourceId: 'resource-123',
      desiredState: 'updated'
    },
    metadata: {
      actor: 'developer-demo',
      timestamp: Date.now()
    }
  }

  const result = await client.submitIntent(intent)
  console.log('Execution Result:')
  console.log(JSON.stringify(result, null, 2))

  const replay = await client.replayIntent(intent.id)
  console.log('\nReplay Summary:')
  console.log(JSON.stringify(replay, null, 2))
}

void main()
