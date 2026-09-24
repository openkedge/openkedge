import { randomUUID } from 'node:crypto'
import type { ContextProvider, EvaluationResult, ExecutionResult, Intent } from '../interfaces/contracts'
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

export interface TerminateParameters { instanceId: string; skipOsShutdown: boolean }
export interface TerminateProposal extends TerminateParameters { reason?: string; memory?: string }
export interface JudgmentProvider {
  judge(intent: Intent, context: unknown): Promise<{ additionalAssurance: boolean; reasons: string[] }>
}
export interface AssuranceCheck { check(intent: Intent, context: unknown): Promise<boolean> }
export interface TerminationAdapter { terminate(params: TerminateParameters): Promise<unknown> }
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
  status: 'executed' | 'rejected' | 'failed'
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
  private readonly identity: IdentityManager
  private readonly safety = new AwsSafetyPolicyEvaluator()
  private readonly blast = new BlastRadiusEstimator()
  private readonly blastPolicy = new BlastRadiusPolicy()

  constructor(
    private readonly source: PolicySource,
    private readonly adapter: TerminationAdapter,
    private readonly signingKey: string,
    private readonly actor: string,
    store: IEECStore = new InMemoryIEECStore(),
    private readonly clock: () => number = Date.now,
    private readonly judgment?: JudgmentProvider,
    private readonly assurance?: AssuranceCheck
  ) {
    this.store = store
    const provider: IdentityProvider = {
      issueIdentity: async (intent, contract) => ({ id: randomUUID(), intentId: intent.id, issuedAt: this.clock(),
        expiresAt: contract?.temporalBounds.notAfter ?? this.clock() + 5000, permissions: [OPERATION] }),
      revokeIdentity: async (identity: ExecutionIdentity) => { identity.metadata = { revokedAt: this.clock() } }
    }
    this.identity = new IdentityManager(provider, store)
  }

  async status(): Promise<{ policyVersion: string }> {
    const snapshot = await this.source.current()
    return { policyVersion: snapshot.revision }
  }

  private governance(snapshot: PolicySnapshot): TemporalGovernance {
    return new TemporalGovernance(this.store, {
      secretKey: this.signingKey, actions: { [OPERATION]: { kind: 'MUTATION' } },
      rules: snapshot.policy.rules, contractTtlMs: 5_000, maxDurationMs: 5_000, clock: this.clock
    })
  }

  private async event(intent: Intent, type: EventType, details: Record<string, unknown>): Promise<void> {
    await this.store.append({ id: randomUUID(), type, timestamp: this.clock(), intentId: intent.id,
      payload: { intentSnapshot: intent, ...details } })
  }

  async admit(input: unknown): Promise<GatewayDecision> {
    const id = randomUUID()
    let proposal: TerminateProposal
    try { proposal = parseProposal(input) }
    catch (error) { return { status: 'denied', intentId: id, code: resultCode(error), reasons: [String(error)] } }
    const intent: Intent = { id, type: OPERATION, kind: 'MUTATION',
      payload: { instanceIds: [proposal.instanceId], skipOsShutdown: proposal.skipOsShutdown },
      metadata: { actor: this.actor, timestamp: this.clock() } }
    await this.event(intent, EventType.IntentReceived, { metadata: { untrustedProposal: proposal }, reasoningTrail: ['MCP arguments accepted as an untrusted proposal'] })
    let revision: string | undefined
    try {
      const snapshot = await this.source.current()
      revision = snapshot.revision
      const governance = this.governance(snapshot)
      const normalized = governance.normalize(intent)
      await governance.verifyCapabilities(normalized)
      const contextProvider: ContextProvider = { resolve: async () => contextFor(snapshot.policy, proposal) }
      const context = await contextProvider.resolve(normalized)
      await this.event(normalized, EventType.ContextResolved, { contextSnapshot: context, metadata: { policyVersion: revision } })
      const temporal = await governance.evaluateProposal(normalized)
      await this.event(normalized, EventType.TemporalEvaluated, { contextSnapshot: context, temporalEvaluation: temporal, metadata: { policyVersion: revision } })
      const blast = this.blast.estimate(normalized, context)
      await this.event(normalized, EventType.BlastRadiusEvaluated, { contextSnapshot: context, blastRadius: blast, metadata: { policyVersion: revision } })
      const safety = await this.safety.evaluate(normalized, context)
      const blastDecision = this.blastPolicy.evaluate(blast)
      const allowedTarget = snapshot.policy.allowedInstanceIds.includes(proposal.instanceId)
      const protectedTarget = snapshot.policy.protectedInstanceIds.includes(proposal.instanceId)
      const state = snapshot.policy.instances[proposal.instanceId]?.state
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
        temporalEvaluation: temporal, evaluationResult: evaluation, metadata: { policyVersion: revision } })
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
      await this.event(intent, EventType.EvaluationCompleted, { evaluationResult: evaluation, metadata: { policyVersion: revision } })
      await this.event(intent, EventType.ExecutionSkipped, { evaluationResult: evaluation,
        executionResult: { success: false, error: reason }, metadata: { policyVersion: revision } })
      return { status: 'denied', intentId: id, policyVersion: revision, code: resultCode(error), reasons: [reason] }
    }
  }

  async execute(grant: ExecutionContract, actualInput: unknown): Promise<GatewayExecution> {
    const id = isRecord(grant) && typeof grant.proposalId === 'string' ? grant.proposalId : ''
    const events = id ? await this.store.getEventsByIntent(id) : []
    const intent = events[0]?.payload.intentSnapshot
    const revision = grant?.policyVersion
    if (!intent) return { status: 'rejected', intentId: id, policyVersion: revision, code: 'UNKNOWN_GRANT', reason: 'No admitted proposal found' }
    let started = false
    try {
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
      const currentContext = contextFor(snapshot.policy, actual)
      if (hashJson(admissionContext) !== hashJson(currentContext)) throw new Error('STATE_GUARD_FAILED: Target state changed after admission')
      const result = await this.identity.withIdentity(intent, async (identity) => {
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
        if (hashJson(contextFor(executionPolicy.policy, actual)) !== hashJson(currentContext)) {
          throw new Error('STATE_GUARD_FAILED: Target state changed before adapter invocation')
        }
        const outcome = await this.adapter.terminate(actual)
        const executionResult: ExecutionResult = { success: true, result: outcome, executionContract: grant }
        await this.event(intent, EventType.ExecutionCompleted, { contextSnapshot: currentContext, executionContract: grant,
          executionResult, metadata: { policyVersion: revision, actualOperation: OPERATION, actual } })
        return outcome
      }, { contract: grant, clock: this.clock, assertCanUnlock: () => governance.assertCanUnlock(grant, intent) })
      return { status: 'executed', intentId: id, policyVersion: revision, result }
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      if (started) {
        await this.event(intent, EventType.ExecutionCompleted, { executionContract: grant,
          executionResult: { success: false, error: reason }, metadata: { policyVersion: revision } })
      } else {
        await this.event(intent, EventType.ExecutionRejected, { executionContract: grant, error: reason,
          metadata: { policyVersion: revision, untrustedActual: actualInput } })
      }
      return { status: started ? 'failed' : 'rejected', intentId: id, policyVersion: revision,
        code: resultCode(error), reason }
    }
  }

  async replay(intentId: string) { return new ReplayEngine().replayIntent(await this.store.getEventsByIntent(intentId)) }
}
