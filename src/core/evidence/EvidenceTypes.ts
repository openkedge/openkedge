export type EvidenceDecision = 'APPROVED' | 'REJECTED'

export interface EvidenceIdentity {
  readonly agentId: string
  readonly traceId: string
}

export interface EvidenceIntent {
  readonly foreignModel: string
  readonly rawJustification: string
  readonly abstractedPayload: string
}

export interface EvidenceEvaluation {
  readonly evaluatorId: string
  readonly policyVersion: string
  readonly telemetrySnapshotHash: string
  readonly decision: EvidenceDecision
  readonly reasons?: readonly string[]
  readonly matchedRules?: readonly string[]
  readonly policyId?: string
  readonly metadata?: Readonly<Record<string, unknown>>
}

export interface EvidenceExecution {
  readonly realResource: string
  readonly actualCommand: string
  readonly timestamp: string
}

export interface EvidenceProof {
  readonly prev_link_hash: string
  readonly link_hash: string
  readonly justification_hash: string
  readonly signature: string
}

export interface EvidenceLinkRecord {
  readonly identity: EvidenceIdentity
  readonly intent: EvidenceIntent
  readonly evaluation: EvidenceEvaluation
  readonly execution: EvidenceExecution
  readonly proof: EvidenceProof
}

export interface EvidenceLinkInput {
  readonly identity: EvidenceIdentity
  readonly intent: Omit<EvidenceIntent, 'rawJustification'> & {
    readonly rawJustification?: string
  }
  readonly evaluation?: Partial<EvidenceEvaluation>
  readonly execution: EvidenceExecution
  readonly proof?: Partial<Pick<EvidenceProof, 'prev_link_hash' | 'signature'>>
}

export interface SovereignPolicyEvaluationResult {
  readonly evaluatorId: string
  readonly policyVersion: string
  readonly telemetrySnapshotHash: string
  readonly decision?: EvidenceDecision
  readonly allowed?: boolean
  readonly reasons?: readonly string[]
  readonly matchedRules?: readonly string[]
  readonly policyId?: string
  readonly metadata?: Readonly<Record<string, unknown>>
}
