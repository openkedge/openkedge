import type {
  ExecutionIdentity,
  ExecutionResult,
  Intent
} from '../../interfaces/contracts'

export interface Executor {
  execute(
    intent: Intent,
    context: unknown,
    identity: ExecutionIdentity,
    signal?: AbortSignal
  ): Promise<ExecutionResult>
}
