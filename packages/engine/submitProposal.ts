import { IntentProposal, PolicyDecision } from '../core'
import { EventStore } from '../store'
import { buildContext } from '../context'
import { PolicyAdapter, CedarPolicyAdapter } from '../policy'
import { toEvent } from './toEvent'

export async function submitProposal(
  proposal: IntentProposal, 
  store: EventStore,
  adapter: PolicyAdapter = new CedarPolicyAdapter()
): Promise<PolicyDecision> {
  const context = buildContext(proposal.target.id, store)

  const input = {
    intent: {
      type: proposal.intent,
      payload: proposal.proposedFacts
    },
    context,
    blastRadius: { totalImpacted: 1 }, // Generic default mock
    identity: proposal.actor
  }

  const decision = await adapter.evaluate(input)

  if (decision.allowed) {
    const event = toEvent(proposal)
    store.append(event)
  }

  return decision
}
