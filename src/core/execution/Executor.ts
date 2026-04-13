import type {
  ExecutionIdentity,
  ExecutionResult,
  Intent
} from '../../interfaces/contracts'

export interface Executor {
  execute(
    intent: Intent,
    context: unknown,
    identity: ExecutionIdentity
  ): Promise<ExecutionResult>
}
