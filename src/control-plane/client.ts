import { open, readFile, rename } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { dirname } from 'node:path'
import type { Intent } from '../interfaces/contracts'
import type { ExecutionContract } from '../core/governance/types'
import type { PolicySnapshot, PolicySource } from '../gateway/policy'
import type { TerminateParameters } from '../gateway/Gateway'
import { policyRevision, validatePermitRequest, verifyBundle, type ActionClass, type PolicyBundle } from './protocol'

export interface ControllerClientConfig {
  url: string
  gatewayId: string
  token: string
  publicKey: string
  statePath: string
  timeoutMs?: number
}

export interface DispatchPermit {
  permitId: string
  expiresAt: number
  finish(status: 'executed' | 'validated' | 'failed' | 'uncertain', evidenceHash?: string): Promise<void>
}
export interface DispatchAuthority {
  begin(intent: Intent, grant: ExecutionContract, actual: TerminateParameters): Promise<DispatchPermit>
}

export class ControllerPolicyClient implements PolicySource, DispatchAuthority {
  private cached?: PolicyBundle
  private initialized = false
  private lastSyncAt?: number
  private acknowledgedEpoch?: number

  constructor(readonly config: ControllerClientConfig) {
    if (!/^https?:\/\//.test(config.url) || config.token.length < 24 || !config.gatewayId || !config.publicKey || !config.statePath) {
      throw new Error('INVALID_CONTROLLER_CONFIG')
    }
    if (new URL(config.url).protocol !== 'https:' && !['127.0.0.1', 'localhost', '[::1]'].includes(new URL(config.url).hostname)) {
      throw new Error('CONTROLLER_TLS_REQUIRED: HTTP is accepted only on loopback')
    }
  }

  private async initialize(): Promise<void> {
    if (this.initialized) return
    try {
      const value = JSON.parse(await readFile(this.config.statePath, 'utf8')) as unknown
      this.cached = verifyBundle(value, this.config.publicKey).bundle
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
    }
    this.initialized = true
  }

  private async request(path: string, method = 'GET', body?: unknown): Promise<any> {
    let response: Response
    try {
      response = await fetch(new URL(path, this.config.url), { method,
        redirect: 'error',
        headers: { authorization: `Bearer ${this.config.token}`, 'x-okg-gateway-id': this.config.gatewayId,
          ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(this.config.timeoutMs ?? 1_500) })
    } catch { throw new Error('CONTROLLER_UNAVAILABLE: Controller request failed') }
    if (response.status === 401 || response.status === 403) throw new Error('CONTROLLER_UNAUTHORIZED')
    if (!response.ok) {
      const error = await response.json().catch(() => ({})) as { code?: string }
      throw new Error(`${error.code ?? 'CONTROLLER_REQUEST_FAILED'}: HTTP ${response.status}`)
    }
    return response.json()
  }

  async current(): Promise<PolicySnapshot> { return this.currentFor('consequential') }

  async currentFor(actionClass: ActionClass): Promise<PolicySnapshot> {
    await this.initialize()
    if (actionClass === 'bounded' && this.cached && Date.now() < this.cached.leaseUntil) {
      return { policy: this.cached.policy, revision: policyRevision(this.cached), bundleId: this.cached.bundleId, epoch: this.cached.epoch }
    }
    const received = await this.request('/v1/policy') as unknown
    const { bundle, snapshot } = verifyBundle(received, this.config.publicKey)
    if (this.cached && (bundle.epoch < this.cached.epoch ||
      (bundle.epoch === this.cached.epoch && (bundle.bundleId !== this.cached.bundleId || bundle.signature !== this.cached.signature)))) {
      throw new Error('POLICY_ROLLBACK_REJECTED')
    }
    if (!this.cached || bundle.epoch > this.cached.epoch) {
      const temporary = `${this.config.statePath}.${randomUUID()}.tmp`
      const file = await open(temporary, 'wx', 0o600)
      try { await file.writeFile(JSON.stringify(bundle)); await file.sync() }
      finally { await file.close() }
      await rename(temporary, this.config.statePath)
      const directory = await open(dirname(this.config.statePath), 'r')
      try { await directory.sync() } finally { await directory.close() }
      this.cached = bundle
      this.acknowledgedEpoch = undefined
    }
    this.lastSyncAt = Date.now()
    if (this.acknowledgedEpoch !== bundle.epoch) {
      await this.request('/v1/ack', 'POST', { protocolVersion: 1, bundleId: bundle.bundleId, epoch: bundle.epoch,
        policyVersion: snapshot.revision, activatedAt: Date.now() })
      this.acknowledgedEpoch = bundle.epoch
    }
    if (actionClass === 'bounded' && Date.now() >= bundle.leaseUntil) throw new Error('POLICY_LEASE_EXPIRED')
    return { ...snapshot, bundleId: bundle.bundleId, epoch: bundle.epoch }
  }

  async status(): Promise<{ epoch?: number; policyVersion?: string; leaseUntil?: number; lastSyncAt?: number; acknowledgedEpoch?: number }> {
    await this.initialize()
    return { epoch: this.cached?.epoch, policyVersion: this.cached ? policyRevision(this.cached) : undefined,
      leaseUntil: this.cached?.leaseUntil, lastSyncAt: this.lastSyncAt, acknowledgedEpoch: this.acknowledgedEpoch }
  }

  async begin(intent: Intent, grant: ExecutionContract, actual: TerminateParameters): Promise<DispatchPermit> {
    const payload = validatePermitRequest({ protocolVersion: 1, proposalId: intent.id, contractId: grant.contractId,
      actorId: grant.actorId, action: grant.action, policyVersion: grant.policyVersion, instanceId: actual.instanceId,
      skipOsShutdown: actual.skipOsShutdown, grantNotAfter: grant.temporalBounds.notAfter })
    const reply = await this.request('/v1/permit', 'POST', payload) as { permitId: string; expiresAt: number }
    if (typeof reply.permitId !== 'string' || !Number.isSafeInteger(reply.expiresAt) || reply.expiresAt <= Date.now()) {
      throw new Error('INVALID_CONTROLLER_PERMIT')
    }
    return { ...reply, finish: async (status, evidenceHash) => {
      await this.request('/v1/outcome', 'POST', { protocolVersion: 1, permitId: reply.permitId,
        proposalId: intent.id, contractId: grant.contractId, status, evidenceHash: evidenceHash ?? null,
        observedAt: Date.now() })
    } }
  }
}
