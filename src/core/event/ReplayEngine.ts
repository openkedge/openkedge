import type {
  EvidenceEvent,
  EvaluationResult,
  ExecutionResult,
  Intent,
  ReplayResult,
  ReplayStep
} from '../../interfaces/contracts'
import { EventType } from '../../interfaces/contracts'

import { EventHasher } from './EventHasher'

export class ReplayEngine {
  async replayIntent(events: EvidenceEvent[]): Promise<ReplayResult> {
    if (events.length === 0) {
      throw new Error('Cannot replay empty event chain')
    }

    const ordered = [...events].sort((a, b) => a.sequence - b.sequence)
    const originalIntent = ordered[0].payload.intentSnapshot

    if (!originalIntent) {
      throw new Error('Missing intent snapshot in first event')
    }

    const integrity = this.validateIntegrity(ordered)
    let contextSnapshot: unknown | undefined
    let evaluationResult: EvaluationResult | undefined
    let executionResult: ExecutionResult | undefined
    const reasoningTrail: string[] = []
    const steps: ReplayStep[] = []

    for (const event of ordered) {
      if (event.payload.contextSnapshot !== undefined) {
        contextSnapshot = event.payload.contextSnapshot
      }

      if (event.payload.evaluationResult !== undefined) {
        evaluationResult = event.payload.evaluationResult
      }

      if (event.payload.executionResult !== undefined) {
        executionResult = event.payload.executionResult
      }

      if (event.payload.reasoningTrail?.length) {
        reasoningTrail.push(...event.payload.reasoningTrail)
      }

      steps.push({
        eventType: event.type,
        timestamp: event.timestamp,
        summary: this.summarizeEvent(event),
        reasoningTrail: event.payload.reasoningTrail ?? []
      })
    }

    return {
      intentId: originalIntent.id,
      originalIntent: originalIntent as Intent,
      events: ordered,
      reasoningTrail,
      replayable: integrity.valid,
      reconstructed: {
        contextSnapshot,
        evaluationResult,
        executionResult,
        finalOutcome: this.computeFinalOutcome(
          evaluationResult,
          executionResult,
          ordered
        )
      },
      steps,
      integrity
    }
  }

  private validateIntegrity(events: EvidenceEvent[]): {
    valid: boolean
    brokenAtEventId?: string
  } {
    let previousHash: string | null = null

    for (const event of events) {
      if (event.previousEventHash !== previousHash) {
        return {
          valid: false,
          brokenAtEventId: event.id
        }
      }

      const recomputed = EventHasher.hashEvent({
        id: event.id,
        type: event.type,
        timestamp: event.timestamp,
        intentId: event.intentId,
        sequence: event.sequence,
        previousEventHash: event.previousEventHash,
        payload: event.payload
      })

      if (recomputed !== event.currentHash) {
        return {
          valid: false,
          brokenAtEventId: event.id
        }
      }

      previousHash = event.currentHash
    }

    return { valid: true }
  }

  private computeFinalOutcome(
    evaluationResult: EvaluationResult | undefined,
    executionResult: ExecutionResult | undefined,
    events: EvidenceEvent[]
  ): 'allowed' | 'blocked' | 'failed' | 'unknown' {
    const hasFailure = events.some((event) => event.payload.error)

    if (hasFailure) {
      return 'failed'
    }

    if (evaluationResult && !evaluationResult.allowed) {
      return 'blocked'
    }

    if (evaluationResult?.allowed && executionResult?.success) {
      return 'allowed'
    }

    if (executionResult && !executionResult.success) {
      return 'failed'
    }

    return 'unknown'
  }

  private summarizeEvent(event: EvidenceEvent): string {
    switch (event.type) {
      case EventType.IntentReceived:
        return `Intent ${event.intentId} received`
      case EventType.ContextResolved:
        return `Context resolved for ${event.intentId}`
      case EventType.EvaluationCompleted:
        return event.payload.evaluationResult?.allowed
          ? `Policy allowed intent ${event.intentId}`
          : `Policy blocked intent ${event.intentId}`
      case EventType.IdentityIssued:
        return `Identity ${event.payload.identitySnapshot?.identityId ?? 'unknown'} issued for ${event.intentId}`
      case EventType.IdentityUsed:
        return `Identity ${event.payload.identitySnapshot?.identityId ?? 'unknown'} used for ${event.intentId}`
      case EventType.IdentityRevoked:
        return `Identity ${event.payload.identitySnapshot?.identityId ?? 'unknown'} revoked for ${event.intentId}`
      case EventType.ExecutionCompleted:
        return event.payload.executionResult?.success
          ? `Execution succeeded for ${event.intentId}`
          : `Execution failed for ${event.intentId}`
      case EventType.ExecutionSkipped:
        return `Execution skipped for ${event.intentId}`
      case EventType.ProcessingFailed:
        return `Processing failed for ${event.intentId}`
      default:
        return `Processed event ${event.type}`
    }
  }
}
