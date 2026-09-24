import { randomUUID } from 'node:crypto'
import { EventType, type EventStore, type Intent } from '../../interfaces/contracts'
import { mintCapabilityToken, verifyCapabilityToken } from '../crypto/capabilities'
import { canonicalJson, immutableSnapshot, isRecord, readPath, signJson } from '../crypto/canonical'
import { mintExecutionContract, verifyExecutionContract } from '../crypto/executionContracts'
import { evaluateTemporalRules, validateTemporalRule } from './temporal'
import type { CapabilityToken, ExecutionContract, IEECRecord, IEECStore, TemporalEvaluationResult, TemporalGovernanceOptions, TraceReader } from './types'

export class GovernanceError extends Error {
  constructor(readonly code: string, reason: string, readonly temporalEvaluation?: TemporalEvaluationResult) {
    super(`${code}: ${reason}`)
  }
}

export class TemporalGovernance {
  readonly history: IEECStore
  readonly clock: () => number
  private readonly options: TemporalGovernanceOptions

  constructor(store: EventStore, options: TemporalGovernanceOptions) {
    const candidate = store as Partial<IEECStore>
    if (!candidate.transaction || !candidate.queryTrace || !candidate.getTrace || !candidate.getTraceByHash) {
      throw new Error('Temporal governance requires an IEECStore (memory, SQLite, or Postgres)')
    }
    this.history = store as IEECStore
    this.clock = options.clock ?? Date.now
    signJson('key-validation', {}, options.secretKey)
    this.options = {
      ...options,
      actions: immutableSnapshot(options.actions),
      rules: (options.rules ?? []).map(rule => Object.freeze({ ...rule,
        ...(rule.type === 'PRECEDING_EVENT_REQUIRED' && rule.matches ? { matches: immutableSnapshot(rule.matches) } : {})
      }))
    }
    for (const rule of this.options.rules ?? []) validateTemporalRule(rule)
    for (const duration of [options.contractTtlMs ?? 30_000, options.maxDurationMs ?? 30_000]) {
      if (!Number.isSafeInteger(duration) || duration <= 0) throw new Error('Invalid contract lifetime')
    }
    for (const action of Object.values(this.options.actions)) {
      if (!['READ', 'PROBE', 'MUTATION'].includes(action.kind)) throw new Error('Invalid action kind')
      if (action.kind !== 'MUTATION' && (!action.capabilityBindings || !Object.keys(action.capabilityBindings).length)) {
        throw new Error('READ and PROBE actions must configure trusted result bindings')
      }
      if (action.capabilityTtlMs !== undefined && (!Number.isSafeInteger(action.capabilityTtlMs) || action.capabilityTtlMs <= 0)) throw new Error('Invalid capability TTL')
      for (const [target, source] of Object.entries(action.capabilityBindings ?? {})) { readPath({}, target); readPath({}, source) }
      for (const source of action.requiredCapabilities ?? []) {
        const kind = this.options.actions[source]?.kind
        if (kind !== 'READ' && kind !== 'PROBE') throw new Error(`Capability source ${source} must be a configured READ or PROBE`)
      }
    }
  }

  normalize(intent: Intent): Intent {
    const own = (value: object, key: string): boolean => Object.prototype.hasOwnProperty.call(value, key)
    if (!isRecord(intent) || !own(intent, 'id') || !intent.id || typeof intent.id !== 'string' || !own(intent, 'type') || !intent.type || typeof intent.type !== 'string' ||
        !isRecord(intent.metadata) || !own(intent.metadata, 'actor') || !intent.metadata.actor || typeof intent.metadata.actor !== 'string' ||
        !own(intent.metadata, 'timestamp') || !Number.isFinite(intent.metadata.timestamp) || !own(intent, 'payload')) throw new GovernanceError('INTENT_INVALID_ERROR', 'Malformed intent')
    if (intent.capabilities !== undefined && !Array.isArray(intent.capabilities)) throw new GovernanceError('CAPABILITY_INVALID_ERROR', 'Expected capability array')
    if (intent.requiredCapabilities !== undefined && (!Array.isArray(intent.requiredCapabilities) || !intent.requiredCapabilities.every(id => typeof id === 'string' && id.length))) {
      throw new GovernanceError('CAPABILITY_INVALID_ERROR', 'Expected required token IDs')
    }
    // Classification is trusted configuration, never an agent's self-declared READ.
    return immutableSnapshot({ ...intent, kind: this.options.actions[intent.type]?.kind ?? 'MUTATION' })
  }

  async verifyCapabilities(intent: Intent, history: TraceReader = this.history, now: number = this.clock()): Promise<IEECRecord[]> {
    const tokens = intent.capabilities ?? []
    const ids = new Set<string>()
    const sources: IEECRecord[] = []
    for (const token of tokens) {
      const validation = verifyCapabilityToken(token, intent.payload as Record<string, unknown>, this.options.secretKey, now)
      if (!validation.valid) throw new GovernanceError(validation.code, validation.reason)
      if (ids.has(token.tokenId)) throw new GovernanceError('CAPABILITY_INVALID_ERROR', 'Duplicate token ID')
      ids.add(token.tokenId)
      const source = await history.getTrace(token.sourceProposalId)
      if (token.actorId !== intent.metadata.actor || source?.actorId !== token.actorId) throw new GovernanceError('CAPABILITY_ACTOR_ERROR', 'Capability is bound to another actor')
      if (!source || source.status !== 'SUCCESS' || source.hash !== token.sourceHash || source.timestamp > token.issuedAt || source.proposalId === intent.id ||
          !['READ', 'PROBE'].includes(source.kind)) throw new GovernanceError('CAPABILITY_SOURCE_ERROR', 'Missing successful source evidence')
      const bindings = this.options.actions[source.action]?.capabilityBindings
      if (!bindings || !Object.keys(bindings).length) throw new GovernanceError('CAPABILITY_SOURCE_ERROR', 'Source action cannot mint capabilities')
      for (const [targetPath, sourcePath] of Object.entries(bindings)) {
        const value = readPath(source.result, sourcePath)
        if (!Object.hasOwn(token.boundAttributes, targetPath) || value === undefined || canonicalJson(value) !== canonicalJson(token.boundAttributes[targetPath])) {
          throw new GovernanceError('CAPABILITY_SOURCE_ERROR', 'Bindings do not match trusted source output')
        }
      }
      sources.push(source)
    }
    for (const id of intent.requiredCapabilities ?? []) {
      if (!ids.has(id)) throw new GovernanceError('CAPABILITY_REQUIRED_ERROR', `Missing capability ${id}`)
    }
    for (const action of this.options.actions[intent.type]?.requiredCapabilities ?? []) {
      if (!sources.some(source => source.action === action)) throw new GovernanceError('CAPABILITY_REQUIRED_ERROR', `A capability from ${action} is required`)
    }
    return sources
  }

  evaluateProposal(intent: Intent, history: TraceReader = this.history, now: number = this.clock()): Promise<TemporalEvaluationResult> {
    return evaluateTemporalRules(intent, history, this.options.rules ?? [], now)
  }

  async reserve(intent: Intent, policyVersion?: string): Promise<{ contract: ExecutionContract; temporal: TemporalEvaluationResult }> {
    return this.history.transaction(async tx => {
      if (await tx.getTrace(intent.id)) throw new GovernanceError('DUPLICATE_PROPOSAL_ERROR', 'Proposal has already been reserved or executed')
      const now = this.clock()
      const sources = await this.verifyCapabilities(intent, tx, now)
      const temporal = await this.evaluateProposal(intent, tx, now)
      if (!temporal.allowed) throw new GovernanceError('TEMPORAL_CONSTRAINT_ERROR', temporal.reasons.join('; '), temporal)
      const requested = intent.temporalBounds ?? {}
      if (!isRecord(requested) || Object.values(requested).some(value => !Number.isSafeInteger(value) || (value as number) < 0)) {
        throw new GovernanceError('CONTRACT_TEMPORAL_BOUNDS_ERROR', 'Invalid requested bounds')
      }
      const notBefore = Math.max(now, requested.notBefore ?? now)
      const notAfter = Math.min(now + (this.options.contractTtlMs ?? 30_000), requested.notAfter ?? Infinity,
        temporal.validBefore ?? Infinity, ...(intent.capabilities ?? []).map(token => token.expiresAt))
      const maxDurationMs = Math.min(this.options.maxDurationMs ?? 30_000, requested.maxDurationMs ?? Infinity, notAfter - notBefore)
      if (notBefore > now || notAfter <= now || maxDurationMs <= 0 || !Number.isSafeInteger(notAfter)) {
        throw new GovernanceError('CONTRACT_TEMPORAL_BOUNDS_ERROR', 'Execution window is not currently active')
      }
      const contract = mintExecutionContract(intent, intent.capabilities ?? [], [...temporal.preconditionHashes, ...sources.map(source => source.hash)],
        { notBefore, notAfter, maxDurationMs }, this.options.secretKey, now, policyVersion)
      await tx.append({ id: randomUUID(), type: EventType.ExecutionReserved, timestamp: now, intentId: intent.id,
        payload: { intentSnapshot: intent, executionContract: contract, temporalEvaluation: temporal,
          reasoningTrail: ['Temporal check and quota reservation committed atomically before credential issuance'] } })
      return { contract, temporal }
    })
  }

  async assertCanUnlock(contract: ExecutionContract, intent: Intent): Promise<void> {
    const now = this.clock()
    await verifyExecutionContract(contract, intent, this.history, this.options.secretKey, now)
    await this.verifyCapabilities(intent, this.history, now)
    const reservation = await this.history.getTrace(intent.id)
    if (reservation?.status !== 'RESERVED') throw new Error('CONTRACT_RESERVATION_ERROR')
  }

  async mintResultCapabilities(intent: Intent): Promise<CapabilityToken[]> {
    const config = this.options.actions[intent.type]
    if (!config || config.kind === 'MUTATION') return []
    const source = await this.history.getTrace(intent.id)
    if (!source || source.status !== 'SUCCESS') throw new Error('Missing successful source execution')
    const bindings = Object.fromEntries(Object.entries(config.capabilityBindings ?? {}).map(([target, path]) => {
      const value = readPath(source.result, path)
      if (value === undefined) throw new Error(`Executor result is missing capability binding ${path}`)
      return [target, value]
    }))
    return [mintCapabilityToken(source, bindings, this.options.secretKey, config.capabilityTtlMs ?? 15 * 60_000, this.clock())]
  }
}
