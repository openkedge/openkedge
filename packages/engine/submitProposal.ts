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
  if (proposal.capabilities !== undefined || proposal.requiredCapabilities !== undefined || proposal.temporalBounds !== undefined) {
    return { allowed: false, reasons: ['GOVERNANCE_NOT_CONFIGURED_ERROR: Fact-only proposals do not execute temporal contracts; use ExecutionClient with a governed OpenKedgeEngine.'] }
  }
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
