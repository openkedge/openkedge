import { checkAuthority } from '../packages/policy/rules/authority'
import { IntentProposal } from '../packages/core'

test('authority test', () => {
  const proposal: IntentProposal = {
    actor: { id: 'agent1', type: 'unverified_agent', trust: 0.1 },
    target: { id: 'bakery', type: 'business' },
    intent: 'try',
    proposedFacts: [],
    timestamp: Date.now()
  }
  const result = checkAuthority(proposal, { currentState: null, recentEvents: [] })
  expect(result?.outcome).toBe('reject')
})
