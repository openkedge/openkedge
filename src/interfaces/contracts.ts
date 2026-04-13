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
  evaluate(intent: Intent, context: unknown): Promise<EvaluationResult>
}

export interface Executor {
  execute(intent: Intent, context: unknown): Promise<ExecutionResult>
}

export enum EventType {
  IntentReceived = 'IntentReceived',
  ContextResolved = 'ContextResolved',
  EvaluationCompleted = 'EvaluationCompleted',
  ExecutionCompleted = 'ExecutionCompleted',
  ExecutionSkipped = 'ExecutionSkipped',
  ProcessingFailed = 'ProcessingFailed'
}

export interface EvidenceEventPayload {
  intentSnapshot: Intent
  contextSnapshot?: unknown
  evaluationResult?: EvaluationResult
  executionResult?: ExecutionResult
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
