import { submitProposal } from '../packages/engine'
import { MemoryEventStore } from '../packages/store'

test('full pipeline', () => {
  const store = new MemoryEventStore()
  const result = submitProposal({
    actor: { id: 'owner', type: 'owner', trust: 1 },
    target: { id: 'x', type: 'type' },
    intent: 'test',
    proposedFacts: [{ type: 'status', value: 'open' }],
    timestamp: Date.now()
  }, store)
  
  expect(result.outcome).toBe('approve')
  expect(store.getEvents('x').length).toBe(1)
})
