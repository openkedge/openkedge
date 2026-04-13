import type {
  ExecutionIdentity,
  IdentityAuditRecord
} from '../core/identity/Identity'
import type { BlastRadius } from '../core/blast/BlastRadiusTypes'

export interface Intent {
  id: string
  type: string
  payload: unknown
  metadata: {
    actor: string
    timestamp: number
  }
}

export interface EvaluationResult {
  allowed: boolean
  reasons: string[]
  matchedRules?: string[]
  enrichedContext?: unknown
}

export interface ExecutionResult {
  success: boolean
  result?: unknown
  error?: string
}

export interface ContextProvider {
  resolve(intent: Intent): Promise<unknown>
}

export interface PolicyEvaluator {
  evaluate(
    intent: Intent,
    context: unknown,
    blastRadius?: BlastRadius
  ): Promise<EvaluationResult>
}

export interface Executor {
  execute(
    intent: Intent,
    context: unknown,
    identity: ExecutionIdentity
  ): Promise<ExecutionResult>
}

export enum EventType {
  IntentReceived = 'IntentReceived',
  ContextResolved = 'ContextResolved',
  BlastRadiusEvaluated = 'BlastRadiusEvaluated',
  EvaluationCompleted = 'EvaluationCompleted',
  IdentityIssued = 'IdentityIssued',
  IdentityUsed = 'IdentityUsed',
  IdentityRevoked = 'IdentityRevoked',
  ExecutionCompleted = 'ExecutionCompleted',
  ExecutionSkipped = 'ExecutionSkipped',
  ProcessingFailed = 'ProcessingFailed'
}

export interface EvidenceEventPayload {
  intentSnapshot: Intent
  contextSnapshot?: unknown
  blastRadius?: BlastRadius
  evaluationResult?: EvaluationResult
  executionResult?: ExecutionResult
  identitySnapshot?: IdentityAuditRecord
  error?: string
  reasoningTrail?: string[]
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
  payload: EvidenceEventPayload
}

export interface EventStore {
  append(
    event: Omit<EvidenceEvent, 'sequence' | 'previousEventHash' | 'currentHash'>
  ): Promise<EvidenceEvent>
  queryByIntent(intentId: string): Promise<EvidenceEvent[]>
  getEventsByIntent(intentId: string): Promise<EvidenceEvent[]>
  getLastEventByIntent(intentId: string): Promise<EvidenceEvent | null>
  exportByIntent(intentId: string): Promise<string>
}

export interface ReplayStep {
  eventType: EventType
  timestamp: number
  summary: string
  reasoningTrail: string[]
}

export interface ReplayResult {
  intentId: string
  originalIntent: Intent
  events: EvidenceEvent[]
  reasoningTrail: string[]
  replayable: boolean
  reconstructed: {
    contextSnapshot?: unknown
    blastRadius?: BlastRadius
    evaluationResult?: EvaluationResult
    executionResult?: ExecutionResult
    finalOutcome: 'allowed' | 'blocked' | 'failed' | 'unknown'
  }
  steps: ReplayStep[]
  integrity: {
    valid: boolean
    brokenAtEventId?: string
  }
}

export type { ExecutionIdentity, IdentityAuditRecord }
export type { BlastRadius }
