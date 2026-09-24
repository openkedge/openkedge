import { randomUUID } from 'node:crypto'
import type { EvaluationResult, ExecutionResult, Intent } from '../interfaces/contracts'
import { EventType } from '../interfaces/contracts'
import { AwsSafetyPolicyEvaluator } from '../adapters/aws/AwsSafetyPolicyEvaluator'
import { BlastRadiusEstimator } from '../core/blast/BlastRadiusEstimator'
import { BlastRadiusPolicy } from '../core/blast/BlastRadiusPolicy'
import { canonicalJson, hashJson, immutableSnapshot, isRecord } from '../core/crypto/canonical'
import { verifyExecutionContract } from '../core/crypto/executionContracts'
import { InMemoryIEECStore } from '../core/event/InMemoryIEECStore'
import { ReplayEngine } from '../core/event/ReplayEngine'
import { TemporalGovernance } from '../core/governance/TemporalGovernance'
import type { ExecutionContract, IEECStore } from '../core/governance/types'
import { IdentityManager } from '../core/identity/IdentityManager'
import type { IdentityProvider } from '../core/identity/IdentityProvider'
import type { ExecutionIdentity } from '../core/identity/Identity'
import { assertIdentityCanExecute } from '../core/identity/Identity'
import type { GatewayPolicy, PolicySnapshot, PolicySource } from './policy'
import { validateIdentity, type GatewayIdentity } from './config'
import type { DispatchAuthority, DispatchPermit } from '../control-plane/client'

export interface TerminateParameters { instanceId: string; skipOsShutdown: boolean }
export interface TerminateProposal extends TerminateParameters { reason?: string; memory?: string }
export interface JudgmentProvider {
  judge(intent: Intent, context: unknown): Promise<{ additionalAssurance: boolean; reasons: string[] }>
}
export interface AssuranceCheck { check(intent: Intent, context: unknown): Promise<boolean> }
export interface TerminationAdapter { terminate(params: TerminateParameters, identity?: ExecutionIdentity, intent?: Intent, grant?: ExecutionContract): Promise<unknown> }
export interface GatewayContextResolver { resolve(intent: Intent): Promise<{ instances: Array<{ instanceId?: string; state?: string; tags: Record<string, string | undefined> }> }> }
export class MockTerminationAdapter implements TerminationAdapter {
  readonly calls: TerminateParameters[] = []
  async terminate(params: TerminateParameters): Promise<unknown> {
    this.calls.push(immutableSnapshot(params))
    return { instanceId: params.instanceId, state: 'terminated', skipOsShutdown: params.skipOsShutdown }
  }
}

export type GatewayDecision =
  | { status: 'allowed'; intentId: string; policyVersion: string; reasons: string[]; grant: ExecutionContract }
  | { status: 'denied' | 'remediation_required'; intentId: string; policyVersion?: string; reasons: string[]; code: string }
export interface GatewayExecution {
  status: 'executed' | 'validated' | 'rejected' | 'failed' | 'uncertain'
  intentId: string
  policyVersion?: string
  code?: string
  reason?: string
  result?: unknown
}

const OPERATION = 'ec2:TerminateInstances'
const allowedKeys = new Set(['instanceId', 'skipOsShutdown', 'reason', 'memory'])

export function parseProposal(input: unknown): TerminateProposal {
  if (!isRecord(input) || Object.keys(input).some(key => !allowedKeys.has(key)) ||
    typeof input.instanceId !== 'string' || !/^i-[a-f0-9]{17}$/.test(input.instanceId) ||
    typeof input.skipOsShutdown !== 'boolean' ||
    (input.reason !== undefined && (typeof input.reason !== 'string' || input.reason.length > 2000)) ||
    (input.memory !== undefined && (typeof input.memory !== 'string' || input.memory.length > 4000))) {
    throw new Error('INVALID_PROPOSAL: Expected instanceId, skipOsShutdown, optional reason and memory')
  }
  return input as unknown as TerminateProposal
}

function operation(input: unknown): TerminateParameters {
  if (!isRecord(input) || Object.keys(input).some(key => !['instanceId', 'skipOsShutdown'].includes(key)) ||
    typeof input.instanceId !== 'string' || !/^i-[a-f0-9]{17}$/.test(input.instanceId) || typeof input.skipOsShutdown !== 'boolean') {
    throw new Error('INVALID_OPERATION: Expected exact instanceId and skipOsShutdown')
  }
  return { instanceId: input.instanceId, skipOsShutdown: input.skipOsShutdown }
}

function contextFor(policy: GatewayPolicy, params: TerminateParameters): { instances: Array<{ instanceId: string; state: string; tags: Record<string, string> }> } {
  const entry = policy.instances[params.instanceId]
  return { instances: entry ? [{ instanceId: params.instanceId, state: entry.state, tags: entry.tags }] : [] }
}

function resultCode(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  return /^([A-Z_]+):?/.exec(message)?.[1] ?? 'EXECUTION_REJECTED'
}

export class ExecutionGateway {
  readonly store: IEECStore
  private readonly identityManager: IdentityManager
  private readonly claims: GatewayIdentity
  private readonly safety = new AwsSafetyPolicyEvaluator()
  private readonly blast = new BlastRadiusEstimator()
  private readonly blastPolicy = new BlastRadiusPolicy()

  constructor(
    private readonly source: PolicySource,
    private readonly adapter: TerminationAdapter,
    private readonly signingKey: string,
    identity: GatewayIdentity,
    store: IEECStore = new InMemoryIEECStore(),
    private readonly clock: () => number = Date.now,
    private readonly judgment?: JudgmentProvider,
    private readonly assurance?: AssuranceCheck,
    identityProvider?: IdentityProvider,
    private readonly liveContext?: GatewayContextResolver,
    private readonly contractTtlMs = 5_000,
    private readonly dispatchAuthority?: DispatchAuthority
  ) {
    this.store = store
    this.claims = validateIdentity(identity)
    const provider: IdentityProvider = {
      issueIdentity: async (intent, contract) => ({ id: randomUUID(), intentId: intent.id, issuedAt: this.clock(),
        expiresAt: contract?.temporalBounds.notAfter ?? this.clock() + 5000, permissions: [OPERATION] }),
      revokeIdentity: async (identity: ExecutionIdentity) => { identity.metadata = { revokedAt: this.clock() } }
    }
    this.identityManager = new IdentityManager(identityProvider ?? provider, store)
  }

  async status(): Promise<{ policyVersion: string; bundleId?: string; epoch?: number }> {
    const snapshot = await this.source.current()
    return { policyVersion: snapshot.revision, bundleId: snapshot.bundleId, epoch: snapshot.epoch }
  }

  private governance(snapshot: PolicySnapshot): TemporalGovernance {
    return new TemporalGovernance(this.store, {
      secretKey: this.signingKey, actions: { [OPERATION]: { kind: 'MUTATION' } },
      rules: snapshot.policy.rules, contractTtlMs: this.contractTtlMs, maxDurationMs: this.contractTtlMs, clock: this.clock
    })
  }

  private async event(intent: Intent, type: EventType, details: Record<string, unknown>) {
    return this.store.append({ id: randomUUID(), type, timestamp: this.clock(), intentId: intent.id,
      payload: { intentSnapshot: intent, ...details } })
  }

  async admit(input: unknown): Promise<GatewayDecision> {
    const id = randomUUID()
    let proposal: TerminateProposal
    try { proposal = parseProposal(input) }
    catch (error) { return { status: 'denied', intentId: id, code: resultCode(error), reasons: [String(error)] } }
    const intent: Intent = { id, type: OPERATION, kind: 'MUTATION',
      payload: { instanceIds: [proposal.instanceId], skipOsShutdown: proposal.skipOsShutdown },
      metadata: { actor: this.claims.callerId, delegatedBy: this.claims.delegatedBy,
        gatewayId: this.claims.gatewayId, timestamp: this.clock() } }
    try { await this.event(intent, EventType.IntentReceived, { metadata: {
      trustClassification: 'UNTRUSTED_AGENT_INPUT', untrustedInputs: {
        reason: proposal.reason, memory: proposal.memory, instanceId: proposal.instanceId,
        skipOsShutdown: proposal.skipOsShutdown
      }, launcherClaims: this.claims
    }, reasoningTrail: ['MCP arguments are untrusted proposal data; launcher identity is separate'] }) }
    catch { return { status: 'denied', intentId: id, code: 'EVIDENCE_UNAVAILABLE', reasons: ['Evidence store unavailable'] } }
    let revision: string | undefined
    try {
      const snapshot = await this.source.current()
      revision = snapshot.revision
      const governance = this.governance(snapshot)
      const normalized = governance.normalize(intent)
      await governance.verifyCapabilities(normalized)
      const contextProvider: GatewayContextResolver = this.liveContext ?? { resolve: async () => contextFor(snapshot.policy, proposal) }
      const context = await contextProvider.resolve(normalized)
      await this.event(normalized, EventType.ContextResolved, { contextSnapshot: context,
        metadata: { policyVersion: revision, policyBundleId: snapshot.bundleId, policyEpoch: snapshot.epoch } })
      const temporal = await governance.evaluateProposal(normalized)
      await this.event(normalized, EventType.TemporalEvaluated, { contextSnapshot: context, temporalEvaluation: temporal, metadata: { policyVersion: revision } })
      const blast = this.blast.estimate(normalized, context)
      await this.event(normalized, EventType.BlastRadiusEvaluated, { contextSnapshot: context, blastRadius: blast, metadata: { policyVersion: revision } })
      const safety = await this.safety.evaluate(normalized, context)
      const blastDecision = this.blastPolicy.evaluate(blast)
      const allowedTarget = snapshot.policy.allowedInstanceIds.includes(proposal.instanceId)
      const protectedTarget = snapshot.policy.protectedInstanceIds.includes(proposal.instanceId)
      const state = context.instances.find(instance => instance.instanceId === proposal.instanceId)?.state
      const paramsAllowed = !proposal.skipOsShutdown || snapshot.policy.allowSkipOsShutdown
      const reasons = [
        ...safety.reasons, ...blastDecision.reasons,
        ...(allowedTarget ? [] : ['Target is outside the policy allowlist']),
        ...(protectedTarget ? ['Target is protected'] : []),
        ...(state === 'running' ? [] : ['Target is not known to be running']),
        ...(paramsAllowed ? [] : ['skipOsShutdown is prohibited by policy'])
      ]
      let allowed = safety.allowed && blastDecision.allowed && allowedTarget && !protectedTarget && state === 'running' && paramsAllowed && temporal.allowed
      let remediation = false
      if (this.judgment) {
        const judgment = await this.judgment.judge(normalized, context)
        reasons.push(...judgment.reasons)
        if (judgment.additionalAssurance) {
          const assured = await this.assurance?.check(normalized, context) ?? false
          if (!assured) { allowed = false; remediation = true; reasons.push('Additional assurance evidence required') }
        }
      }
      const evaluation: EvaluationResult = { allowed, reasons, matchedRules: ['gateway-local-policy'], enrichedContext: context }
      await this.event(normalized, EventType.EvaluationCompleted, { contextSnapshot: context, blastRadius: blast,
        temporalEvaluation: temporal, evaluationResult: evaluation,
        metadata: { policyVersion: revision, policyBundleId: snapshot.bundleId, policyEpoch: snapshot.epoch } })
      if (!allowed) {
        await this.event(normalized, EventType.ExecutionSkipped, { contextSnapshot: context, evaluationResult: evaluation,
          executionResult: { success: false, error: reasons.join('; ') }, metadata: { policyVersion: revision } })
        return { status: remediation ? 'remediation_required' : 'denied', intentId: id, policyVersion: revision,
          code: remediation ? 'ASSURANCE_REQUIRED' : 'POLICY_DENIED', reasons }
      }
      const { contract } = await governance.reserve(normalized, revision)
      return { status: 'allowed', intentId: id, policyVersion: revision, reasons, grant: contract }
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      const evaluation: EvaluationResult = { allowed: false, reasons: [reason] }
      try {
        await this.event(intent, EventType.EvaluationCompleted, { evaluationResult: evaluation, metadata: { policyVersion: revision } })
        await this.event(intent, EventType.ExecutionSkipped, { evaluationResult: evaluation,
          executionResult: { success: false, error: reason }, metadata: { policyVersion: revision } })
      } catch { return { status: 'denied', intentId: id, policyVersion: revision, code: 'EVIDENCE_UNAVAILABLE', reasons: ['Evidence store unavailable'] } }
      return { status: 'denied', intentId: id, policyVersion: revision, code: resultCode(error), reasons: [reason] }
    }
  }

  async execute(grant: ExecutionContract, actualInput: unknown): Promise<GatewayExecution> {
    const id = isRecord(grant) && typeof grant.proposalId === 'string' ? grant.proposalId : ''
    let events
    try { events = id ? await this.store.getEventsByIntent(id) : [] }
    catch { return { status: 'rejected', intentId: id, code: 'EVIDENCE_UNAVAILABLE', reason: 'Evidence store unavailable' } }
    const intent = events[0]?.payload.intentSnapshot
    const revision = grant?.policyVersion
    if (!intent) return { status: 'rejected', intentId: id, policyVersion: revision, code: 'UNKNOWN_GRANT', reason: 'No admitted proposal found' }
    let started = false
    let adapterInvoked = false
    let adapterSucceeded = false
    let permit: DispatchPermit | undefined
    let permitFinished = false
    try {
      if (intent.metadata.actor !== this.claims.callerId || grant.actorId !== this.claims.callerId ||
        intent.metadata.delegatedBy !== this.claims.delegatedBy) {
        throw new Error('CALLER_MISMATCH: Grant belongs to a different launcher-attested caller or delegator')
      }
      if (intent.metadata.gatewayId !== this.claims.gatewayId) throw new Error('GATEWAY_MISMATCH: Grant was issued by another gateway')
      const actual = operation(actualInput)
      const payload = intent.payload as { instanceIds: string[]; skipOsShutdown: boolean }
      if (grant.action !== OPERATION || actual.instanceId !== payload.instanceIds[0] || actual.skipOsShutdown !== payload.skipOsShutdown) {
        throw new Error('OPERATION_MISMATCH: Actual operation, target or parameters differ from grant')
      }
      const reserved = events.find(event => event.type === EventType.ExecutionReserved)?.payload.executionContract
      if (!reserved || canonicalJson(reserved) !== canonicalJson(grant)) throw new Error('GRANT_MISMATCH: Grant is not the recorded reservation')
      const snapshot = await this.source.current()
      if (!revision || revision !== snapshot.revision) throw new Error('POLICY_VERSION_CONFLICT: Grant policy is no longer current')
      const governance = this.governance(snapshot)
      if ((await this.store.getTrace(id))?.status !== 'RESERVED') throw new Error('GRANT_REPLAY: Grant was already redeemed')
      await verifyExecutionContract(grant, intent, this.store, this.signingKey, this.clock())
      await governance.assertCanUnlock(grant, intent)
      const admissionContext = events.find(event => event.type === EventType.ContextResolved)?.payload.contextSnapshot
      const currentContext = this.liveContext ? await this.liveContext.resolve(intent) : contextFor(snapshot.policy, actual)
      if (hashJson(admissionContext) !== hashJson(currentContext)) throw new Error('STATE_GUARD_FAILED: Target state changed after admission')
      const result = await this.identityManager.withIdentity(intent, async (identity) => {
        assertIdentityCanExecute(intent, identity, this.clock())
        // Atomically consume the one-use contract before entering the adapter.
        await this.store.transaction(async tx => {
          if ((await tx.getTrace(id))?.status !== 'RESERVED') throw new Error('GRANT_REPLAY: Grant was already redeemed')
          await tx.append({ id: randomUUID(), type: EventType.ExecutionStarted, timestamp: this.clock(), intentId: id,
            payload: { intentSnapshot: intent, executionContract: grant, contextSnapshot: currentContext,
              metadata: { policyVersion: revision, actualOperation: OPERATION, actual } } })
        })
        started = true
        const executionPolicy = await this.source.current()
        if (executionPolicy.revision !== revision) throw new Error('POLICY_VERSION_CONFLICT: Policy changed before adapter invocation')
        const dispatchContext = this.liveContext ? await this.liveContext.resolve(intent) : contextFor(executionPolicy.policy, actual)
        if (hashJson(dispatchContext) !== hashJson(currentContext)) {
          throw new Error('STATE_GUARD_FAILED: Target state changed before adapter invocation')
        }
        let redemptionState
        try { redemptionState = await this.store.getTrace(id) }
        catch { throw new Error('EVIDENCE_UNAVAILABLE: Redemption state cannot be verified') }
        if (redemptionState?.status !== 'RUNNING') throw new Error('EVIDENCE_UNAVAILABLE: Redemption state cannot be verified')
        permit = await this.dispatchAuthority?.begin(intent, grant, actual)
        if (permit && permit.expiresAt <= this.clock()) throw new Error('PERMIT_EXPIRED: Dispatch permit expired before adapter invocation')
        adapterInvoked = true
        const outcome = await this.adapter.terminate(actual, identity, intent, grant)
        adapterSucceeded = true
        const executionResult: ExecutionResult = { success: true, result: outcome, executionContract: grant }
        const completed = await this.event(intent, EventType.ExecutionCompleted, { contextSnapshot: currentContext, executionContract: grant,
          executionResult, metadata: { policyVersion: revision, actualOperation: OPERATION, actual, dispatchPermitId: permit?.permitId } })
        if (permit) { await permit.finish(isRecord(outcome) && outcome.mode === 'dry-run' ? 'validated' : 'executed', completed.currentHash); permitFinished = true }
        return outcome
      }, { contract: grant, clock: this.clock, assertCanUnlock: () => governance.assertCanUnlock(grant, intent) })
      return { status: isRecord(result) && result.mode === 'dry-run' ? 'validated' : 'executed', intentId: id, policyVersion: revision, result }
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      try {
        if (started && (!adapterInvoked || (adapterInvoked && !adapterSucceeded))) {
          const failed = await this.event(intent, EventType.ExecutionCompleted, { executionContract: grant,
            executionResult: { success: false, error: reason }, metadata: { policyVersion: revision, dispatchPermitId: permit?.permitId } })
          if (permit && !permitFinished) {
            const definitive = error instanceof Error && 'definitive' in error && error.definitive === true
            await permit.finish(adapterInvoked && !definitive ? 'uncertain' : 'failed', failed.currentHash)
            permitFinished = true
          }
        } else if (!started) {
          await this.event(intent, EventType.ExecutionRejected, { executionContract: grant, error: reason,
            metadata: { policyVersion: revision, trustClassification: 'UNTRUSTED_AGENT_INPUT', untrustedActual: actualInput } })
        }
      } catch {
        return { status: adapterInvoked ? 'uncertain' : 'rejected', intentId: id, policyVersion: revision, code: 'EVIDENCE_UNAVAILABLE',
          reason: adapterInvoked ? 'Adapter outcome uncertain; evidence store unavailable' : 'Evidence store unavailable' }
      }
      if (adapterInvoked && error instanceof Error && 'definitive' in error && error.definitive === true) {
        return { status: 'failed', intentId: id, policyVersion: revision, code: resultCode(error), reason }
      }
      if (adapterInvoked) return { status: 'uncertain', intentId: id, policyVersion: revision,
        code: 'OUTCOME_UNCERTAIN', reason: 'Adapter was invoked; inspect evidence and adapter state before retrying' }
      return { status: started ? 'failed' : 'rejected', intentId: id, policyVersion: revision,
        code: resultCode(error), reason }
    }
  }

  async replay(intentId: string) {
    const events = await this.store.getEventsByIntent(intentId)
    const metadata = events[0]?.payload.intentSnapshot.metadata
    if (!metadata || metadata.actor !== this.claims.callerId || metadata.delegatedBy !== this.claims.delegatedBy ||
      metadata.gatewayId !== this.claims.gatewayId) throw new Error('EVIDENCE_ACCESS_DENIED')
    return new ReplayEngine().replayIntent(events)
  }
}
