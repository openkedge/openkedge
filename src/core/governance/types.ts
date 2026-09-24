import type { EvidenceEvent, EventStore, Intent } from '../../interfaces/contracts'

export type IntentKind = 'READ' | 'PROBE' | 'MUTATION'

export interface CapabilityToken {
  tokenId: string
  sourceProposalId: string
  sourceHash: string
  issuedAt: number
  expiresAt: number
  actorId: string
  boundAttributes: Record<string, unknown>
  signature: string
}

export type ValidationResult =
  | { valid: true }
  | { valid: false; code: string; reason: string }

/** A projection of a hashed execution event, never a second source of truth. */
export interface IEECRecord {
  proposalId: string
  actorId: string
  action: string
  kind: IntentKind
  timestamp: number
  status: 'RESERVED' | 'RUNNING' | 'SUCCESS' | 'FAILED'
  intent: Intent
  result?: unknown
  hash: string
}

export interface TraceQuery {
  action: string
  actorId?: string
  fromExclusive: number
  toInclusive: number
  /** Unresolved reservations count even when older than the window. */
  includeReservations?: boolean
}

export interface TraceReader {
  queryTrace(query: TraceQuery): Promise<IEECRecord[]>
  getTrace(proposalId: string): Promise<IEECRecord | undefined>
  getTraceByHash(hash: string): Promise<IEECRecord | undefined>
}

export interface IEECTransaction extends TraceReader {
  append(event: Omit<EvidenceEvent, 'sequence' | 'previousEventHash' | 'currentHash'>): Promise<EvidenceEvent>
  getEventsByIntent(intentId: string): Promise<EvidenceEvent[]>
}

export interface IEECStore extends EventStore, TraceReader {
  /** Serializes check + reservation across every writer sharing this store. */
  transaction<T>(work: (transaction: IEECTransaction) => Promise<T>): Promise<T>
}

export interface TemporalMatch {
  currentPath: string
  historicalPath: string
}

interface TemporalRuleBase {
  id?: string
  targetAction: string
  windowMs: number
  scope?: 'ACTOR' | 'GLOBAL' | 'RESOURCE'
  /** Path within intent.payload, required for RESOURCE scope. */
  resourcePath?: string
  /** Display metadata; metric values should use integer minor units for money. */
  unit?: string
}

export type TemporalRule = TemporalRuleBase & (
  | {
      type: 'SLIDING_WINDOW_QUOTA'
      metricPath: string
      maxCumulativeValue: number
    }
  | { type: 'RATE_LIMIT'; maxCount: number }
  | {
      type: 'PRECEDING_EVENT_REQUIRED'
      requiredPrecedingAction: string
      matches?: TemporalMatch[]
      /** Trusted, in-process extension. JSON policies use matches instead. */
      matchPredicate?: (currentIntent: Intent, historicalEntry: IEECRecord) => boolean
    }
)

export interface TemporalBudget {
  ruleId: string
  action: string
  windowMs: number
  used: number
  requested: number
  limit: number
  unit?: string
}

export interface CapabilityLink {
  tokenId: string
  sourceProposalId: string
  sourceHash: string
  sourceAction?: string
  sourceTimestamp?: number
  sourceVerified: boolean
  capabilityVerified: boolean
  boundAttributes: Record<string, unknown>
}

export interface TemporalEvaluationResult {
  allowed: boolean
  decision: 'ALLOW' | 'REJECT'
  reasons: string[]
  budgets: TemporalBudget[]
  preconditionHashes: string[]
  validBefore?: number
}

export interface TemporalBounds {
  notBefore: number
  notAfter: number
  maxDurationMs: number
}

export interface ExecutionContract {
  contractId: string
  proposalId: string
  actorId: string
  action: string
  /** Authenticated authoritative policy revision for gateway admission. */
  policyVersion?: string
  intentHash: string
  issuedAt: number
  temporalBounds: TemporalBounds
  temporalValidity: { validAfter: number; validBefore: number }
  linkedCapabilities: string[]
  capabilities: CapabilityToken[]
  preconditionHashes: string[]
  signature: string
}

export interface GovernedAction {
  kind: IntentKind
  /** Successful source actions whose capabilities are mandatory. */
  requiredCapabilities?: string[]
  /** Target payload paths mapped to trusted executor result paths. */
  capabilityBindings?: Record<string, string>
  capabilityTtlMs?: number
}

export interface TemporalGovernanceOptions {
  secretKey: string
  actions: Record<string, GovernedAction>
  rules?: TemporalRule[]
  contractTtlMs?: number
  maxDurationMs?: number
  /** Trusted clock, injectable for deterministic replay/tests. */
  clock?: () => number
}
