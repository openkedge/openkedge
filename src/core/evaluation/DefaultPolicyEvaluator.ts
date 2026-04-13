import type {
  EvaluationResult,
  Intent,
  PolicyEvaluator
} from '../../interfaces/contracts'

export class DefaultPolicyEvaluator
  implements PolicyEvaluator<Record<string, unknown>>
{
  async evaluate(
    intent: Intent,
    context: Record<string, unknown>
  ): Promise<EvaluationResult> {
    return {
      allowed: true,
      reasons: [`Allowed by default policy for intent type "${intent.type}"`],
      enrichedContext: context
    }
  }
}
