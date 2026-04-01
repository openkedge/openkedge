import { IntentProposal, PolicyDecision } from '../core'
import { Context } from '../context'
import { checkAuthority } from './rules/authority'
import { checkTrust } from './rules/trust'
import { detectConflict } from './rules/conflict'
import { checkRecency } from './rules/temporal'

export function evaluateProposal(proposal: IntentProposal, context: Context): PolicyDecision {
  // apply rules in order
  const rules = [checkTrust, checkAuthority, checkRecency, detectConflict]
  
  for (const rule of rules) {
    const decision = rule(proposal, context)
    if (decision) {
      return decision
    }
  }

  return { outcome: 'approve', reason: 'Passed all checks' }
}
