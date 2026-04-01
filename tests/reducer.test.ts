import { resolveConflicts } from '../packages/reducer/resolveConflicts'

test('conflict resolution', () => {
  const result = resolveConflicts([
    { type: 'status', value: 'open' },
    { type: 'status', value: 'closed' }
  ])
  expect(result.length).toBe(1)
  expect(result[0].value).toBe('closed')
})
