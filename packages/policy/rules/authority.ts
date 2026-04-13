import type { IntentProposal } from '../../core'

export interface AuthorityEvaluationContext {
  currentState: unknown
  recentEvents: unknown[]
}

export interface AuthorityDecision {
  outcome: 'allow' | 'reject'
  reason: string
}

export function checkAuthority(
  proposal: IntentProposal,
  _context: AuthorityEvaluationContext
): AuthorityDecision | null {
  if (
    proposal.actor.type === 'unverified_agent' ||
    proposal.actor.trust < 0.5
  ) {
    return {
      outcome: 'reject',
      reason: 'actor is not trusted to perform mutations'
    }
  }

  return null
}
