import { assertIdentityCanExecute } from '../identity/Identity'
import type {
  ExecutionIdentity,
  ExecutionResult,
  Intent
} from '../../interfaces/contracts'

import type { Executor } from './Executor'

export class NoopExecutor implements Executor {
  async execute(
    intent: Intent,
    context: unknown,
    identity: ExecutionIdentity
  ): Promise<ExecutionResult> {
    assertIdentityCanExecute(intent, identity)

    return {
      success: true,
      result: {
        message: 'Noop execution completed',
        intentType: intent.type,
        context,
        identityId: identity.id
      }
    }
  }
}
