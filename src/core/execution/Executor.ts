import type {
  ExecutionResult,
  Intent
} from '../../interfaces/contracts'

export interface Executor {
  execute(intent: Intent, context: unknown): Promise<ExecutionResult>
}
