import { randomUUID, sign, verify } from 'node:crypto'
import { canonicalJson, isRecord } from '../core/crypto/canonical'
import { validatePolicy, type GatewayPolicy, type PolicySnapshot } from '../gateway/policy'

export type ActionClass = 'bounded' | 'consequential'
export interface PolicyBundle {
  protocolVersion: 1
  bundleId: string
  epoch: number
  issuedAt: number
  leaseUntil: number
  policy: GatewayPolicy
  signature: string
}

export function policyRevision(bundle: PolicyBundle): string {
  return `${bundle.epoch}:${validatePolicy(bundle.policy).revision}`
}

export function signBundle(policy: GatewayPolicy, epoch: number, privateKey: string,
  issuedAt = Date.now(), leaseMs = 2_000): PolicyBundle {
  validatePolicy(policy)
  if (!Number.isSafeInteger(epoch) || epoch < 1 || !Number.isSafeInteger(leaseMs) || leaseMs < 1 || leaseMs > 60_000 ||
    !Number.isSafeInteger(issuedAt) || issuedAt < 0) {
    throw new Error('INVALID_POLICY_BUNDLE')
  }
  const unsigned = { protocolVersion: 1 as const, bundleId: randomUUID(), epoch, issuedAt,
    leaseUntil: issuedAt + leaseMs, policy }
  return { ...unsigned, signature: sign(null, Buffer.from(canonicalJson(unsigned)), privateKey).toString('base64') }
}

export function verifyBundle(input: unknown, publicKey: string): { bundle: PolicyBundle; snapshot: PolicySnapshot } {
  if (!isRecord(input) || Object.keys(input).sort().join(',') !==
    ['bundleId', 'epoch', 'issuedAt', 'leaseUntil', 'policy', 'protocolVersion', 'signature'].sort().join(',') ||
    input.protocolVersion !== 1 || typeof input.bundleId !== 'string' || !/^[0-9a-f-]{36}$/.test(input.bundleId) ||
    !Number.isSafeInteger(input.epoch) || Number(input.epoch) < 1 ||
    !Number.isSafeInteger(input.issuedAt) || Number(input.issuedAt) < 0 || !Number.isSafeInteger(input.leaseUntil) ||
    Number(input.leaseUntil) <= Number(input.issuedAt) || Number(input.leaseUntil) - Number(input.issuedAt) > 60_000 ||
    typeof input.signature !== 'string' ||
    !/^[A-Za-z0-9+/]+={0,2}$/.test(input.signature)) throw new Error('INVALID_POLICY_BUNDLE')
  const { signature, ...unsigned } = input
  const snapshot = validatePolicy(input.policy)
  if (!verify(null, Buffer.from(canonicalJson(unsigned)), publicKey, Buffer.from(signature, 'base64'))) {
    throw new Error('POLICY_SIGNATURE_INVALID')
  }
  const bundle = input as unknown as PolicyBundle
  return { bundle, snapshot: { policy: snapshot.policy, revision: policyRevision(bundle) } }
}

export interface PermitRequest {
  protocolVersion: 1
  proposalId: string
  contractId: string
  actorId: string
  action: 'ec2:TerminateInstances'
  policyVersion: string
  instanceId: string
  skipOsShutdown: boolean
  grantNotAfter: number
}

export function validatePermitRequest(input: unknown): PermitRequest {
  if (!isRecord(input) || Object.keys(input).sort().join(',') !==
    ['protocolVersion', 'proposalId', 'contractId', 'actorId', 'action', 'policyVersion', 'instanceId', 'skipOsShutdown', 'grantNotAfter'].sort().join(',') ||
    input.protocolVersion !== 1 ||
    input.action !== 'ec2:TerminateInstances' ||
    !['proposalId', 'contractId', 'actorId', 'policyVersion'].every(k => typeof input[k] === 'string' && (input[k] as string).length > 0) ||
    typeof input.instanceId !== 'string' || !/^i-[a-f0-9]{17}$/.test(input.instanceId) ||
    typeof input.skipOsShutdown !== 'boolean' || !Number.isSafeInteger(input.grantNotAfter)) {
    throw new Error('INVALID_PERMIT_REQUEST')
  }
  return input as unknown as PermitRequest
}
