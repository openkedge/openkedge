import { randomUUID } from 'node:crypto'
import type { Intent } from '../../interfaces/contracts'
import type { CapabilityToken, ExecutionContract, TemporalBounds, TraceReader } from '../governance/types'
import { canonicalJson, hashJson, immutableSnapshot, signJson, validSignature } from './canonical'

const DOMAIN = 'openkedge.execution-contract.v1'

export function mintExecutionContract(
  intent: Intent, capabilities: CapabilityToken[], preconditionHashes: string[],
  temporalBounds: TemporalBounds, secretKey: string, now: number = Date.now(), policyVersion?: string
): ExecutionContract {
  if (![now, temporalBounds.notBefore, temporalBounds.notAfter, temporalBounds.maxDurationMs].every(Number.isSafeInteger) ||
      temporalBounds.notAfter <= temporalBounds.notBefore || temporalBounds.maxDurationMs <= 0 ||
      temporalBounds.maxDurationMs > temporalBounds.notAfter - temporalBounds.notBefore ||
      capabilities.some(token => !token.tokenId) || new Set(capabilities.map(token => token.tokenId)).size !== capabilities.length) {
    throw new Error('Invalid execution contract bounds or capabilities')
  }
  const unsigned = {
    contractId: randomUUID(), proposalId: intent.id, actorId: intent.metadata.actor,
    action: intent.type, ...(policyVersion ? { policyVersion } : {}), intentHash: hashJson(intent), issuedAt: now, temporalBounds,
    temporalValidity: { validAfter: temporalBounds.notBefore, validBefore: temporalBounds.notAfter },
    linkedCapabilities: capabilities.map(token => token.tokenId), capabilities,
    preconditionHashes: [...new Set(preconditionHashes)]
  }
  return immutableSnapshot({ ...unsigned, signature: signJson(DOMAIN, unsigned, secretKey) })
}

/** Fast local bounds check also used by executors before each remote call. */
export function assertContractBounds(contract: ExecutionContract, intent: Intent, now: number = Date.now()): void {
  const bounds = contract.temporalBounds
  if (contract.proposalId !== intent.id || contract.actorId !== intent.metadata.actor ||
      contract.action !== intent.type || contract.intentHash !== hashJson(intent)) throw new Error('CONTRACT_INTENT_MISMATCH_ERROR')
  if (![bounds.notBefore, bounds.notAfter, bounds.maxDurationMs, now].every(Number.isSafeInteger) ||
      bounds.notAfter <= bounds.notBefore || bounds.maxDurationMs <= 0 ||
      bounds.maxDurationMs > bounds.notAfter - bounds.notBefore ||
      contract.temporalValidity.validAfter !== bounds.notBefore || contract.temporalValidity.validBefore !== bounds.notAfter ||
      now < bounds.notBefore || now >= bounds.notAfter) throw new Error('CONTRACT_TEMPORAL_BOUNDS_ERROR')
}

export async function verifyExecutionContract(
  contract: ExecutionContract, intent: Intent, history: TraceReader, secretKey: string,
  now: number = Date.now()
): Promise<void> {
  const { signature, ...unsigned } = contract
  if (!validSignature(signature, signJson(DOMAIN, unsigned, secretKey))) throw new Error('CONTRACT_SIGNATURE_ERROR')
  if (canonicalJson(contract.capabilities) !== canonicalJson(intent.capabilities ?? []) ||
      canonicalJson(contract.linkedCapabilities) !== canonicalJson(contract.capabilities.map(token => token.tokenId)) ||
      new Set(contract.preconditionHashes).size !== contract.preconditionHashes.length) throw new Error('CONTRACT_CAPABILITY_LINK_ERROR')
  assertContractBounds(contract, intent, now)
  for (const hash of contract.preconditionHashes) {
    const record = await history.getTraceByHash(hash)
    if (!record || record.status !== 'SUCCESS' || record.proposalId === intent.id || record.timestamp > contract.issuedAt) {
      throw new Error('CONTRACT_PRECONDITION_ERROR')
    }
  }
}
