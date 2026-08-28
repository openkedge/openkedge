import { randomUUID } from 'node:crypto'
import type { CapabilityToken, IEECRecord, ValidationResult } from '../governance/types'
import { canonicalJson, immutableSnapshot, isRecord, readPath, signJson, validSignature } from './canonical'

const DOMAIN = 'openkedge.capability.v1'

export function mintCapabilityToken(
  sourceRecord: IEECRecord,
  boundAttributes: Record<string, unknown>,
  secretKey: string,
  ttlMs: number,
  now: number = Date.now()
): CapabilityToken {
  if (sourceRecord.status !== 'SUCCESS' || !['READ', 'PROBE'].includes(sourceRecord.kind) || sourceRecord.timestamp > now) {
    throw new Error('Capabilities require a completed successful READ or PROBE')
  }
  if (!Number.isSafeInteger(ttlMs) || ttlMs <= 0 || !Number.isSafeInteger(now) || !Number.isSafeInteger(now + ttlMs)) {
    throw new Error('Invalid capability lifetime')
  }
  if (!isRecord(boundAttributes) || Object.keys(boundAttributes).length === 0) throw new Error('Capability bindings cannot be empty')
  canonicalJson(boundAttributes)
  for (const path of Object.keys(boundAttributes)) readPath({}, path)
  const unsigned = {
    tokenId: randomUUID(), sourceProposalId: sourceRecord.proposalId,
    sourceHash: sourceRecord.hash, issuedAt: now, expiresAt: now + ttlMs,
    actorId: sourceRecord.actorId, boundAttributes
  }
  return immutableSnapshot({ ...unsigned, signature: signJson(DOMAIN, unsigned, secretKey) })
}

/** Checks cryptography and attenuation. Source provenance and actor are checked by governance. */
export function verifyCapabilityToken(
  token: CapabilityToken,
  targetPayload: Record<string, unknown>,
  secretKey: string,
  now: number = Date.now()
): ValidationResult {
  try {
    if (!isRecord(token) || !isRecord(token.boundAttributes) || Object.keys(token.boundAttributes).length === 0 ||
        ![token.tokenId, token.sourceProposalId, token.actorId].every(value => typeof value === 'string' && value.length > 0) ||
        typeof token.sourceHash !== 'string' || !/^[a-f0-9]{64}$/.test(token.sourceHash) ||
        !Number.isSafeInteger(token.issuedAt) || !Number.isSafeInteger(token.expiresAt) || token.expiresAt <= token.issuedAt) {
      return { valid: false, code: 'CAPABILITY_INVALID_ERROR', reason: 'Malformed capability token' }
    }
    const { signature, ...unsigned } = token
    if (!validSignature(signature, signJson(DOMAIN, unsigned, secretKey))) {
      return { valid: false, code: 'CAPABILITY_SIGNATURE_ERROR', reason: 'Invalid capability signature' }
    }
    if (!Number.isSafeInteger(now) || now < token.issuedAt || now >= token.expiresAt) {
      return { valid: false, code: 'CAPABILITY_EXPIRED_ERROR', reason: 'Capability is outside its validity window' }
    }
    for (const [path, expected] of Object.entries(token.boundAttributes)) {
      const actual = readPath(targetPayload, path)
      if (actual === undefined || canonicalJson(actual) !== canonicalJson(expected)) {
        return { valid: false, code: 'CAPABILITY_MISMATCH_ERROR', reason: `Capability does not authorize payload.${path}` }
      }
    }
    return { valid: true }
  } catch {
    return { valid: false, code: 'CAPABILITY_INVALID_ERROR', reason: 'Invalid capability data' }
  }
}
