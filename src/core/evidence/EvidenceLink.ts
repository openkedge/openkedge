import { EventHasher } from '../event/EventHasher'

import type {
  EvidenceDecision,
  EvidenceEvaluation,
  EvidenceLinkInput,
  EvidenceLinkRecord,
  SovereignPolicyEvaluationResult
} from './EvidenceTypes'

export const GENESIS_PREV_LINK_HASH = EventHasher.hashValue(
  'openkedge:evidence-chain:genesis'
)

const DEFAULT_SIGNATURE = 'UNSIGNED'

function deepFreeze<T>(value: T): T {
  if (value === null || typeof value !== 'object' || Object.isFrozen(value)) {
    return value
  }

  Object.freeze(value)

  for (const key of Object.getOwnPropertyNames(value)) {
    const nested = (value as Record<string, unknown>)[key]
    deepFreeze(nested)
  }

  return value
}

function normalizeDecision(
  evaluation: Partial<EvidenceEvaluation> | SovereignPolicyEvaluationResult | undefined
): EvidenceDecision {
  if (evaluation?.decision) {
    return evaluation.decision
  }

  return evaluation && 'allowed' in evaluation && evaluation.allowed
    ? 'APPROVED'
    : 'REJECTED'
}

function cloneMetadata(
  metadata: Readonly<Record<string, unknown>> | undefined
): Readonly<Record<string, unknown>> | undefined {
  return metadata ? { ...metadata } : undefined
}

function normalizeEvaluation(
  evaluation: Partial<EvidenceEvaluation> | SovereignPolicyEvaluationResult | undefined
): EvidenceEvaluation {
  return {
    evaluatorId: evaluation?.evaluatorId ?? 'unbound-sovereign-evaluator',
    policyVersion: evaluation?.policyVersion ?? 'unbound',
    telemetrySnapshotHash:
      evaluation?.telemetrySnapshotHash ?? EventHasher.hashValue('unbound'),
    decision: normalizeDecision(evaluation),
    ...(evaluation?.reasons?.length
      ? {
          reasons: [...evaluation.reasons]
        }
      : {}),
    ...(evaluation?.matchedRules?.length
      ? {
          matchedRules: [...evaluation.matchedRules]
        }
      : {}),
    ...(evaluation?.policyId
      ? {
          policyId: evaluation.policyId
        }
      : {}),
    ...(evaluation?.metadata
      ? {
          metadata: cloneMetadata(evaluation.metadata)
        }
      : {})
  }
}

function computeJustificationHash(input: EvidenceLinkRecord): string {
  return EventHasher.hashValue({
    rawJustification: input.intent.rawJustification,
    evaluation: input.evaluation
  })
}

export class EvidenceLink {
  readonly identity: EvidenceLinkRecord['identity']
  readonly intent: EvidenceLinkRecord['intent']
  readonly evaluation: EvidenceLinkRecord['evaluation']
  readonly execution: EvidenceLinkRecord['execution']
  readonly proof: EvidenceLinkRecord['proof']

  private constructor(private readonly record: EvidenceLinkRecord) {
    this.identity = record.identity
    this.intent = record.intent
    this.evaluation = record.evaluation
    this.execution = record.execution
    this.proof = record.proof
    deepFreeze(this)
  }

  static create(input: EvidenceLinkInput): EvidenceLink {
    const base = {
      identity: {
        agentId: input.identity.agentId,
        traceId: input.identity.traceId
      },
      intent: {
        foreignModel: input.intent.foreignModel,
        rawJustification: input.intent.rawJustification ?? '',
        abstractedPayload: input.intent.abstractedPayload
      },
      evaluation: normalizeEvaluation(input.evaluation),
      execution: {
        realResource: input.execution.realResource,
        actualCommand: input.execution.actualCommand,
        timestamp: input.execution.timestamp
      }
    }

    const proof = {
      prev_link_hash: input.proof?.prev_link_hash ?? GENESIS_PREV_LINK_HASH,
      signature: input.proof?.signature ?? DEFAULT_SIGNATURE
    }

    const justification_hash = computeJustificationHash({
      ...base,
      proof: {
        ...proof,
        link_hash: '',
        justification_hash: ''
      }
    })

    const link_hash = EventHasher.hashValue({
      ...base,
      proof: {
        ...proof,
        justification_hash
      }
    })

    return new EvidenceLink(
      deepFreeze({
        ...base,
        proof: {
          ...proof,
          justification_hash,
          link_hash
        }
      })
    )
  }

  bind_justification(
    rawJustification: string,
    evaluationResult: SovereignPolicyEvaluationResult
  ): EvidenceLink {
    return EvidenceLink.create({
      identity: this.identity,
      intent: {
        foreignModel: this.intent.foreignModel,
        abstractedPayload: this.intent.abstractedPayload,
        rawJustification
      },
      evaluation: evaluationResult,
      execution: this.execution,
      proof: {
        prev_link_hash: this.proof.prev_link_hash,
        signature: this.proof.signature
      }
    })
  }

  bindJustification(
    rawJustification: string,
    evaluationResult: SovereignPolicyEvaluationResult
  ): EvidenceLink {
    return this.bind_justification(rawJustification, evaluationResult)
  }

  with_prev_link_hash(prev_link_hash: string): EvidenceLink {
    return EvidenceLink.create({
      identity: this.identity,
      intent: this.intent,
      evaluation: this.evaluation,
      execution: this.execution,
      proof: {
        prev_link_hash,
        signature: this.proof.signature
      }
    })
  }

  withPrevLinkHash(prevLinkHash: string): EvidenceLink {
    return this.with_prev_link_hash(prevLinkHash)
  }

  with_signature(signature: string): EvidenceLink {
    return EvidenceLink.create({
      identity: this.identity,
      intent: this.intent,
      evaluation: this.evaluation,
      execution: this.execution,
      proof: {
        prev_link_hash: this.proof.prev_link_hash,
        signature
      }
    })
  }

  withSignature(signature: string): EvidenceLink {
    return this.with_signature(signature)
  }

  verify_integrity(): boolean {
    return (
      this.proof.justification_hash === computeJustificationHash(this.record) &&
      this.proof.link_hash ===
        EventHasher.hashValue({
          identity: this.identity,
          intent: this.intent,
          evaluation: this.evaluation,
          execution: this.execution,
          proof: {
            prev_link_hash: this.proof.prev_link_hash,
            signature: this.proof.signature,
            justification_hash: this.proof.justification_hash
          }
        })
    )
  }

  verifyIntegrity(): boolean {
    return this.verify_integrity()
  }

  toJSON(): EvidenceLinkRecord {
    return this.record
  }

  toJSONLD(id: string, sequence: number) {
    return {
      '@id': id,
      '@type': 'okg:EvidenceLink',
      sequence,
      identity: {
        '@type': 'okg:Identity',
        ...this.identity
      },
      intent: {
        '@type': 'okg:Intent',
        ...this.intent
      },
      evaluation: {
        '@type': 'okg:Evaluation',
        ...this.evaluation
      },
      execution: {
        '@type': 'okg:Execution',
        ...this.execution
      },
      proof: {
        '@type': 'okg:Proof',
        ...this.proof
      }
    }
  }
}
