import { IntentProposal, PolicyDecision } from '../../core'

export function checkTrust(proposal: IntentProposal): PolicyDecision | null {
  // escalate low trust
  if (proposal.actor.trust < 0.5) {
    return { outcome: 'escalate', reason: 'Actor trust score too low' }
  }
  return null
}
