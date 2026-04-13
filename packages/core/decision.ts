export interface PolicyDecision {
  allowed: boolean
  reasons: string[]
  raw?: any
}
