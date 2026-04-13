import type {
  ExecutionResult,
  Executor,
  Intent
} from '../../interfaces/contracts'

export class NoopExecutor implements Executor<unknown> {
  async execute(intent: Intent, context: unknown): Promise<ExecutionResult> {
    return {
      success: true,
      result: {
        simulated: true,
        intentId: intent.id,
        intentType: intent.type,
        context
      }
    }
  }
}
