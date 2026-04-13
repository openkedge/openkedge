import { DefaultPolicyEvaluator, EmptyContextProvider } from '../core/evaluation'
import { OpenKedgeEngine, type OpenKedgeEngineDependencies } from '../core/engine'
import { InMemoryEventStore } from '../core/event'
import { NoopExecutor } from '../core/execution'
import type { ExecutionResult, Intent } from '../interfaces/contracts'

export type OpenKedgeClientOptions<TContext = Record<string, unknown>> = Partial<
  OpenKedgeEngineDependencies<TContext>
>

export class OpenKedgeClient<TContext = Record<string, unknown>> {
  constructor(private readonly engine: OpenKedgeEngine<TContext>) {}

  async submitIntent(intent: Intent): Promise<ExecutionResult> {
    return this.engine.process(intent)
  }
}

export function createOpenKedgeClient<TContext = Record<string, unknown>>(
  options: OpenKedgeClientOptions<TContext> = {}
): OpenKedgeClient<TContext> {
  const engine = new OpenKedgeEngine<TContext>({
    contextProvider:
      options.contextProvider ??
      (new EmptyContextProvider() as unknown as OpenKedgeEngineDependencies<TContext>['contextProvider']),
    policyEvaluator:
      options.policyEvaluator ??
      (new DefaultPolicyEvaluator() as unknown as OpenKedgeEngineDependencies<TContext>['policyEvaluator']),
    executor:
      options.executor ??
      (new NoopExecutor() as unknown as OpenKedgeEngineDependencies<TContext>['executor']),
    eventStore: options.eventStore ?? new InMemoryEventStore(),
    logger: options.logger
  })

  return new OpenKedgeClient(engine)
}

const defaultClient = createOpenKedgeClient()

export async function submitIntent(intent: Intent): Promise<ExecutionResult> {
  return defaultClient.submitIntent(intent)
}
