export type BlastRiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export interface BlastRadius {
  resourceCount: number
  resourceIds: string[]
  riskLevel: BlastRiskLevel
  reasons: string[]
}

export interface BlastRadiusPolicyDecision {
  allowed: boolean
  reasons: string[]
  requiresApproval?: boolean
}

export interface BlastRadiusPolicyConfig {
  maxAllowed?: number
  allowHighRisk?: boolean
}
