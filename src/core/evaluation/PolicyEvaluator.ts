import type {
  EvaluationResult,
  Intent
} from '../../interfaces/contracts'

export interface PolicyEvaluator {
  evaluate(intent: Intent, context: unknown): Promise<EvaluationResult>
}
