import { randomUUID } from 'node:crypto'

import type { EventStore, EvaluationResult, ExecutionResult, Intent, EvidenceEventPayload } from '../../interfaces/contracts'
import { EventType } from '../../interfaces/contracts'
import { BlastRadiusEstimator } from '../blast/BlastRadiusEstimator'
import { BlastRadiusPolicy } from '../blast/BlastRadiusPolicy'
import type { BlastRadius } from '../blast/BlastRadiusTypes'
import type { ContextProvider } from '../context/ContextProvider'
import type { PolicyEvaluator } from '../evaluation/PolicyEvaluator'
import type { Executor } from '../execution/Executor'
import type { IdentityManager } from '../identity/IdentityManager'
import { assertIdentityCanExecute } from '../identity/Identity'
import { immutableSnapshot } from '../crypto/canonical'
import { GovernanceError, TemporalGovernance } from '../governance/TemporalGovernance'
import type { ExecutionContract, TemporalEvaluationResult, TemporalGovernanceOptions } from '../governance/types'

export class OpenKedgeEngine {
  private readonly governance?: TemporalGovernance

  constructor(
    private readonly contextProvider: ContextProvider,
    private readonly policyEvaluator: PolicyEvaluator,
    private readonly executor: Executor,
    private readonly identityManager: IdentityManager,
    private readonly eventStore: EventStore,
    private readonly blastRadiusEstimator: BlastRadiusEstimator = new BlastRadiusEstimator(),
    private readonly blastRadiusPolicy: BlastRadiusPolicy = new BlastRadiusPolicy(),
    governance?: TemporalGovernanceOptions
  ) {
    if (governance) this.governance = new TemporalGovernance(eventStore, governance)
  }

  async process(input: Intent): Promise<ExecutionResult> {
    let intent: Intent
    try {
      intent = this.governance ? this.governance.normalize(input) : immutableSnapshot(input)
      if (!this.governance && (intent.capabilities !== undefined || intent.requiredCapabilities !== undefined || intent.temporalBounds !== undefined)) {
        throw new GovernanceError('GOVERNANCE_NOT_CONFIGURED_ERROR', 'Temporal features require server governance configuration')
      }
      const received = {
        id: randomUUID(), type: EventType.IntentReceived, timestamp: this.now(), intentId: intent.id,
        payload: { intentSnapshot: intent, reasoningTrail: [
          `Intent submitted by actor=${intent.metadata.actor}`, `Intent type=${intent.type}`
        ] }
      }
      if (this.governance) {
        await this.governance.history.transaction(async tx => {
          if ((await tx.getEventsByIntent(intent.id)).length) throw new GovernanceError('DUPLICATE_PROPOSAL_ERROR', 'Use a new proposal ID for each attempt')
          await tx.append(received)
        })
      } else await this.eventStore.append(received)
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : String(error),
        ...(error instanceof GovernanceError ? { errorCode: error.code } : {}) }
    }

    let contextSnapshot: unknown
    let blastRadius: BlastRadius | undefined
    let evaluationResult: EvaluationResult | undefined
    let temporalEvaluation: TemporalEvaluationResult | undefined
    let executionContract: ExecutionContract | undefined
    let executorStarted = false
    const payload = (): EvidenceEventPayload => ({ intentSnapshot: intent, contextSnapshot, blastRadius, evaluationResult, temporalEvaluation, executionContract })

    try {
      if (this.governance) {
        await this.governance.verifyCapabilities(intent)
        await this.record(intent, EventType.CapabilityVerified, { ...payload(), capabilities: intent.capabilities ?? [],
          reasoningTrail: ['Capability signatures, actor, bound parameters, and successful source evidence verified'] })
      }
      contextSnapshot = await this.contextProvider.resolve(intent)
      await this.record(intent, EventType.ContextResolved, { ...payload(), reasoningTrail: ['Context provider resolved current execution context'] })

      if (this.governance) {
        temporalEvaluation = await this.governance.evaluateProposal(intent)
        await this.record(intent, EventType.TemporalEvaluated, { ...payload(), reasoningTrail: temporalEvaluation.allowed ? ['Temporal preconditions and budgets satisfied'] : temporalEvaluation.reasons })
        if (!temporalEvaluation.allowed) throw new GovernanceError('TEMPORAL_CONSTRAINT_ERROR', temporalEvaluation.reasons.join('; '), temporalEvaluation)
      }
      blastRadius = this.blastRadiusEstimator.estimate(intent, contextSnapshot)
      await this.record(intent, EventType.BlastRadiusEvaluated, { ...payload(), reasoningTrail: blastRadius.reasons })
      const blastDecision = this.blastRadiusPolicy.evaluate(blastRadius)
      const safetyEvaluation = await this.policyEvaluator.evaluate(intent, contextSnapshot, blastRadius)
      evaluationResult = {
        allowed: safetyEvaluation.allowed && blastDecision.allowed,
        reasons: [...safetyEvaluation.reasons, ...blastDecision.reasons],
        enrichedContext: safetyEvaluation.enrichedContext ?? contextSnapshot
      }
      await this.record(intent, EventType.EvaluationCompleted, { ...payload(), reasoningTrail: [
        ...evaluationResult.reasons, evaluationResult.allowed ? 'Intent approved for execution' : 'Intent denied before execution'
      ] })
      if (!evaluationResult.allowed) return this.skip(intent, payload(), `Blocked by policy: ${evaluationResult.reasons.join('; ')}`)

      if (this.governance) {
        const reserved = await this.governance.reserve(intent)
        executionContract = reserved.contract
        temporalEvaluation = reserved.temporal
      }
      const governance = this.governance
      const contract = executionContract
      const executionResult = await this.identityManager.withIdentity(intent, async (identity, signal) => {
        executorStarted = true
        const raw = await this.executor.execute(intent, contextSnapshot, identity, signal)
        if (signal?.aborted) throw new Error('CONTRACT_EXECUTION_TIMEOUT_ERROR')
        // Also enforce elapsed time when a synchronous executor starves timers.
        if (contract) assertIdentityCanExecute(intent, identity, this.now())
        const result = immutableSnapshot({ ...raw, ...(contract ? { executionContract: contract } : {}) })
        await this.record(intent, EventType.ExecutionCompleted, { ...payload(), executionResult: result, reasoningTrail: [
          result.success ? 'Executor completed successfully' : 'Executor returned a failure result'
        ] })
        return result
      }, contract && governance ? {
        contract, clock: governance.clock, assertCanUnlock: () => governance.assertCanUnlock(contract, intent)
      } : undefined)

      if (executionResult.success && governance) {
        const capabilities = await governance.mintResultCapabilities(intent)
        if (capabilities.length) {
          await this.record(intent, EventType.CapabilityIssued, { ...payload(), capabilities,
            reasoningTrail: ['Attenuated capabilities minted from successful trusted executor output'] })
          return { ...executionResult, capabilities }
        }
      }
      return executionResult
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (executionContract && !executorStarted) {
        await this.record(intent, EventType.ExecutionCancelled, { ...payload(), reasoningTrail: ['Reservation cancelled: executor was never invoked'] })
      }
      const admissionCode = error instanceof GovernanceError
        ? error.code
        : /^(CAPABILITY_[A-Z_]+|CONTRACT_[A-Z_]+|TEMPORAL_[A-Z_]+)/.exec(message)?.[1]
      if (admissionCode && !executorStarted) {
        temporalEvaluation = (error instanceof GovernanceError ? error.temporalEvaluation : undefined) ?? temporalEvaluation
        evaluationResult = { allowed: false, reasons: [message] }
        await this.record(intent, EventType.EvaluationCompleted, { ...payload(), reasoningTrail: [message] })
        return this.skip(intent, payload(), message, admissionCode)
      }
      const failedResult: ExecutionResult = { success: false, error: message }
      await this.record(intent, EventType.ProcessingFailed, { ...payload(), executionResult: failedResult, error: message,
        reasoningTrail: ['Processing encountered an exception', message] })
      return failedResult
    }
  }

  private now(): number { return this.governance?.clock() ?? Date.now() }

  private async record(intent: Intent, type: EventType, payload: EvidenceEventPayload): Promise<void> {
    await this.eventStore.append({ id: randomUUID(), type, timestamp: this.now(), intentId: intent.id, payload })
  }

  private async skip(intent: Intent, payload: EvidenceEventPayload, error: string, errorCode?: string): Promise<ExecutionResult> {
    const result: ExecutionResult = { success: false, error, ...(errorCode ? { errorCode } : {}) }
    await this.record(intent, EventType.ExecutionSkipped, { ...payload, executionResult: result,
      reasoningTrail: ['Execution was intentionally skipped because evaluation denied the intent'] })
    return result
  }
}
