import { IntentProposal, PolicyDecision } from '../../core'
import { Context } from '../../context'

export function checkRecency(proposal: IntentProposal, context: Context): PolicyDecision | null {
  // reject stale updates
  if (context.currentState && proposal.timestamp < context.currentState.lastUpdated) {
    return { outcome: 'reject', reason: 'Stale proposal timestamp' }
  }
  return null
}
