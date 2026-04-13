export type ReplayOutcome = 'allowed' | 'blocked' | 'failed' | 'unknown'
export type EventType =
  | 'IntentReceived'
  | 'ContextResolved'
  | 'BlastRadiusEvaluated'
  | 'EvaluationCompleted'
  | 'IdentityIssued'
  | 'IdentityUsed'
  | 'IdentityRevoked'
  | 'ExecutionCompleted'
  | 'ExecutionSkipped'
  | 'ProcessingFailed'

export interface BlastRadius {
  resourceCount: number
  resourceIds: string[]
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  reasons: string[]
}

export interface EvaluationResult {
  allowed: boolean
  reasons: string[]
  enrichedContext?: unknown
}

export interface ExecutionResult {
  success: boolean
  result?: unknown
  error?: string
}

export interface IdentitySnapshot {
  identityId: string
  intentId: string
  issuedAt: number
  expiresAt: number
  permissions: string[]
  metadata?: Record<string, unknown>
}

export interface EvidenceEvent {
  id: string
  type: EventType
  timestamp: number
  intentId: string
  sequence: number
  previousEventHash: string | null
  currentHash: string
  payload: {
    intentSnapshot: {
      id: string
      type: string
      payload: unknown
      metadata: {
        actor: string
        timestamp: number
      }
    }
    contextSnapshot?: unknown
    blastRadius?: BlastRadius
    evaluationResult?: EvaluationResult
    executionResult?: ExecutionResult
    identitySnapshot?: IdentitySnapshot
    error?: string
    reasoningTrail?: string[]
    metadata?: Record<string, unknown>
  }
}

export interface ReplayStep {
  eventType: EventType
  timestamp: number
  summary: string
  reasoningTrail: string[]
}

export interface ReplayResult {
  intentId: string
  originalIntent: EvidenceEvent['payload']['intentSnapshot']
  events: EvidenceEvent[]
  reasoningTrail: string[]
  replayable: boolean
  reconstructed: {
    contextSnapshot?: unknown
    blastRadius?: BlastRadius
    evaluationResult?: EvaluationResult
    executionResult?: ExecutionResult
    finalOutcome: ReplayOutcome
  }
  steps: ReplayStep[]
  integrity: {
    valid: boolean
    brokenAtEventId?: string
  }
}

export interface ReplayStatusTheme {
  dot: string
  badge: string
  panel: string
}
