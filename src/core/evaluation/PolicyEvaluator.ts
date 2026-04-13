import type {
  BlastRadius,
  EvaluationResult,
  Intent
} from '../../interfaces/contracts'

export interface PolicyEvaluator {
  evaluate(
    intent: Intent,
    context: unknown,
    blastRadius?: BlastRadius
  ): Promise<EvaluationResult>
}
