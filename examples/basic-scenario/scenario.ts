import { submitProposal } from '../../packages/engine'
import { MemoryEventStore } from '../../packages/store'
import { reduceEvents } from '../../packages/reducer'
import { IntentProposal } from '../../packages/core'

const store = new MemoryEventStore()

const ownerProposal: IntentProposal = {
  actor: { id: 'owner1', type: 'owner', trust: 1 },
  target: { id: 'bakery', type: 'business' },
  intent: 'open bakery',
  proposedFacts: [{ type: 'operating_status', value: 'open' }],
  timestamp: Date.now()
}

const agentProposal: IntentProposal = {
  actor: { id: 'agent1', type: 'unverified_agent', trust: 0.1 },
  target: { id: 'bakery', type: 'business' },
  intent: 'close bakery early',
  proposedFacts: [{ type: 'operating_status', value: 'closed' }],
  timestamp: Date.now() + 1000
}

console.log('Owner Proposal:', submitProposal(ownerProposal, store))
console.log('Agent Proposal:', submitProposal(agentProposal, store))

const state = reduceEvents('bakery', store.getEvents('bakery'))
console.log('Final State:', JSON.stringify(state, null, 2))
