import { DefaultContextProvider } from '../core/context/DefaultContextProvider'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { DefaultPolicyEvaluator } from '../core/evaluation/DefaultPolicyEvaluator'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { NoopExecutor } from '../core/execution/NoopExecutor'
import type { ExecutionResult, Intent } from '../interfaces/contracts'

export interface OpenKedgeClientOptions {
  engine?: OpenKedgeEngine
}

export class OpenKedgeClient {
  constructor(private readonly engine: OpenKedgeEngine) {}

  async submitIntent(intent: Intent): Promise<ExecutionResult> {
    return this.engine.process(intent)
  }
}

export function createOpenKedgeClient(
  options: OpenKedgeClientOptions = {}
): OpenKedgeClient {
  const engine =
    options.engine ??
    new OpenKedgeEngine(
      new DefaultContextProvider(),
      new DefaultPolicyEvaluator(),
      new NoopExecutor(),
      new InMemoryEventStore()
    )

  return new OpenKedgeClient(engine)
}

const defaultClient = createOpenKedgeClient()

export async function submitIntent(intent: Intent): Promise<ExecutionResult> {
  return defaultClient.submitIntent(intent)
}
