import { IntentProposal, PolicyDecision } from '../core'
import { EventStore } from '../store'
import { buildContext } from '../context'
import { evaluateProposal } from '../policy'
import { toEvent } from './toEvent'

export function submitProposal(proposal: IntentProposal, store: EventStore): PolicyDecision {
  const context = buildContext(proposal.target.id, store)

  const decision = evaluateProposal(proposal, context)

  if (decision.outcome === 'approve') {
    const event = toEvent(proposal)
    store.append(event)
  }

  return decision
}
