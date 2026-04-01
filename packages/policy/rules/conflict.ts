import { IntentProposal, PolicyDecision } from '../../core'
import { Context } from '../../context'

export function detectConflict(proposal: IntentProposal, context: Context): PolicyDecision | null {
  // compare fact types
  const existingFacts = context.currentState?.facts || []
  for (const newFact of proposal.proposedFacts) {
    const conflict = existingFacts.find(f => f.type === newFact.type && f.value !== newFact.value)
    if (conflict) {
      if (proposal.actor.type === 'owner') {
        // Owner overrides
        continue
      }
      return { outcome: 'reject', reason: 'Conflict detected for fact type: ' + newFact.type }
    }
  }
  return null
}
