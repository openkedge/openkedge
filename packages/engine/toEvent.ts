import { IntentProposal, TruthEvent } from '../core'

export function toEvent(proposal: IntentProposal): TruthEvent {
  return {
    entityId: proposal.target.id,
    facts: proposal.proposedFacts,
    source: proposal.actor,
    timestamp: Date.now()
  }
}
