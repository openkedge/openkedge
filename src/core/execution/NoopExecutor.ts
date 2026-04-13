import type { ExecutionResult, Intent } from '../../interfaces/contracts'

import type { Executor } from './Executor'

export class NoopExecutor implements Executor {
  async execute(intent: Intent, context: unknown): Promise<ExecutionResult> {
    return {
      success: true,
      result: {
        message: 'Noop execution completed',
        intentType: intent.type,
        context
      }
    }
  }
}
