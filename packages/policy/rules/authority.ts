import { IntentProposal, PolicyDecision } from '../../core'
import { Context } from '../../context'

export function checkAuthority(proposal: IntentProposal, context: Context): PolicyDecision | null {
  // owner > agent logic
  if (proposal.actor.type === 'unverified_agent') {
    return { outcome: 'reject', reason: 'Action requires higher authority' }
  }
  return null
}
