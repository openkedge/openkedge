import type { EvaluationResult, Intent } from '../../interfaces/contracts'

import type { PolicyEvaluator } from './PolicyEvaluator'

export class DefaultPolicyEvaluator implements PolicyEvaluator {
  async evaluate(intent: Intent, context: unknown): Promise<EvaluationResult> {
    return {
      allowed: true,
      reasons: [`Default allow policy applied to intent type: ${intent.type}`],
      enrichedContext: context
    }
  }
}
