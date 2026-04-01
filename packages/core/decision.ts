export type PolicyDecision = {
  outcome: 'approve' | 'reject' | 'escalate'
  reason: string
}
