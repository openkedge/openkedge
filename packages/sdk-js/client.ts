import { IntentProposal } from '../core'

export async function submitProposal(apiUrl: string, proposal: IntentProposal) {
  const response = await fetch(apiUrl + '/proposals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(proposal)
  })
  return response.json()
}
