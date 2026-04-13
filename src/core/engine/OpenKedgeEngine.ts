import { randomUUID } from 'node:crypto'

import type {
  EventStore,
  EvaluationResult,
  ExecutionResult,
  Intent
} from '../../interfaces/contracts'
import { EventType } from '../../interfaces/contracts'
import { BlastRadiusEstimator } from '../blast/BlastRadiusEstimator'
import { BlastRadiusPolicy } from '../blast/BlastRadiusPolicy'
import type { BlastRadius } from '../blast/BlastRadiusTypes'
import type { ContextProvider } from '../context/ContextProvider'
import type { PolicyEvaluator } from '../evaluation/PolicyEvaluator'
import type { Executor } from '../execution/Executor'
import type { IdentityManager } from '../identity/IdentityManager'

export class OpenKedgeEngine {
  constructor(
    private readonly contextProvider: ContextProvider,
    private readonly policyEvaluator: PolicyEvaluator,
    private readonly executor: Executor,
    private readonly identityManager: IdentityManager,
    private readonly eventStore: EventStore,
    private readonly blastRadiusEstimator: BlastRadiusEstimator = new BlastRadiusEstimator(),
    private readonly blastRadiusPolicy: BlastRadiusPolicy = new BlastRadiusPolicy()
  ) {}

  async process(intent: Intent): Promise<ExecutionResult> {
    await this.eventStore.append({
      id: randomUUID(),
      type: EventType.IntentReceived,
      timestamp: Date.now(),
      intentId: intent.id,
      payload: {
        intentSnapshot: intent,
        reasoningTrail: [
          `Intent submitted by actor=${intent.metadata.actor}`,
          `Intent type=${intent.type}`
        ]
      }
    })

    let contextSnapshot: unknown | undefined
    let blastRadius: BlastRadius | undefined
    let evaluationResult: EvaluationResult | undefined

    try {
      contextSnapshot = await this.contextProvider.resolve(intent)

      await this.eventStore.append({
        id: randomUUID(),
        type: EventType.ContextResolved,
        timestamp: Date.now(),
        intentId: intent.id,
        payload: {
          intentSnapshot: intent,
          contextSnapshot,
          reasoningTrail: [
            'Context provider resolved current execution context'
          ]
        }
      })

      blastRadius = this.blastRadiusEstimator.estimate(intent, contextSnapshot)

      await this.eventStore.append({
        id: randomUUID(),
        type: EventType.BlastRadiusEvaluated,
        timestamp: Date.now(),
        intentId: intent.id,
        payload: {
          intentSnapshot: intent,
          contextSnapshot,
          blastRadius,
          reasoningTrail: blastRadius.reasons
        }
      })

      const blastDecision = this.blastRadiusPolicy.evaluate(blastRadius)
      const safetyEvaluation = await this.policyEvaluator.evaluate(
        intent,
        contextSnapshot,
        blastRadius
      )

      evaluationResult = {
        allowed: safetyEvaluation.allowed && blastDecision.allowed,
        reasons: [...safetyEvaluation.reasons, ...blastDecision.reasons],
        enrichedContext: safetyEvaluation.enrichedContext ?? contextSnapshot
      }

      await this.eventStore.append({
        id: randomUUID(),
        type: EventType.EvaluationCompleted,
        timestamp: Date.now(),
        intentId: intent.id,
        payload: {
          intentSnapshot: intent,
          contextSnapshot,
          blastRadius,
          evaluationResult,
          reasoningTrail: [
            ...evaluationResult.reasons,
            evaluationResult.allowed
              ? 'Intent approved for execution'
              : 'Intent denied before execution'
          ]
        }
      })

      if (!evaluationResult.allowed) {
        const blockedResult: ExecutionResult = {
          success: false,
          error: `Blocked by policy: ${evaluationResult.reasons.join('; ')}`
        }

        await this.eventStore.append({
          id: randomUUID(),
          type: EventType.ExecutionSkipped,
          timestamp: Date.now(),
          intentId: intent.id,
          payload: {
            intentSnapshot: intent,
            contextSnapshot,
            blastRadius,
            evaluationResult,
            executionResult: blockedResult,
            reasoningTrail: [
              'Execution was intentionally skipped because evaluation denied the intent'
            ]
          }
        })

        return blockedResult
      }

      const executionResult = await this.identityManager.withIdentity(
        intent,
        async (identity) => {
          const result = await this.executor.execute(intent, contextSnapshot, identity)

          await this.eventStore.append({
            id: randomUUID(),
            type: EventType.ExecutionCompleted,
            timestamp: Date.now(),
            intentId: intent.id,
            payload: {
              intentSnapshot: intent,
              contextSnapshot,
              blastRadius,
              evaluationResult,
              executionResult: result,
              reasoningTrail: [
                result.success
                  ? 'Executor completed successfully'
                  : 'Executor returned a failure result'
              ]
            }
          })

          return result
        }
      )

      return executionResult
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const failedResult: ExecutionResult = {
        success: false,
        error: message
      }

      await this.eventStore.append({
        id: randomUUID(),
        type: EventType.ProcessingFailed,
        timestamp: Date.now(),
        intentId: intent.id,
        payload: {
          intentSnapshot: intent,
          contextSnapshot,
          blastRadius,
          evaluationResult,
          executionResult: failedResult,
          error: message,
          reasoningTrail: ['Processing encountered an exception', message]
        }
      })

      return failedResult
    }
  }
}
