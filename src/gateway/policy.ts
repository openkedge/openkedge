import { readFile, rename, writeFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { hashJson, immutableSnapshot, isRecord } from '../core/crypto/canonical'
import type { TemporalRule } from '../core/governance/types'
import { validateTemporalRule } from '../core/governance/temporal'

export interface GatewayPolicy {
  protocolVersion: 1
  version: string
  allowedInstanceIds: string[]
  protectedInstanceIds: string[]
  allowSkipOsShutdown: boolean
  instances: Record<string, { state: string; tags: Record<string, string> }>
  rules: TemporalRule[]
}

export interface PolicySnapshot { policy: GatewayPolicy; revision: string; bundleId?: string; epoch?: number }
export interface PolicySource { current(): Promise<PolicySnapshot> }

export function validatePolicy(value: unknown): PolicySnapshot {
  if (!isRecord(value) || Object.keys(value).some(key => !['protocolVersion', 'version', 'allowedInstanceIds', 'protectedInstanceIds', 'allowSkipOsShutdown', 'instances', 'rules'].includes(key)) ||
    value.protocolVersion !== 1 || typeof value.version !== 'string' || !value.version ||
    !Array.isArray(value.allowedInstanceIds) || !value.allowedInstanceIds.every(x => typeof x === 'string') ||
    !Array.isArray(value.protectedInstanceIds) || !value.protectedInstanceIds.every(x => typeof x === 'string') ||
    typeof value.allowSkipOsShutdown !== 'boolean' || !isRecord(value.instances) || !Array.isArray(value.rules)) {
    throw new Error('POLICY_UNAVAILABLE: Invalid policy document')
  }
  for (const instance of Object.values(value.instances)) {
    if (!isRecord(instance) || Object.keys(instance).some(k => !['state', 'tags'].includes(k)) ||
      typeof instance.state !== 'string' || !isRecord(instance.tags) ||
      Object.values(instance.tags).some(tag => typeof tag !== 'string')) throw new Error('POLICY_UNAVAILABLE: Invalid instance context')
  }
  for (const rule of value.rules) {
    if (!isRecord(rule) || Object.keys(rule).some(k => !['id', 'type', 'targetAction', 'windowMs', 'scope', 'resourcePath',
      'unit', 'metricPath', 'maxCumulativeValue', 'maxCount', 'requiredPrecedingAction', 'matches'].includes(k)) ||
      typeof rule.type !== 'string' || typeof rule.targetAction !== 'string' ||
      !Number.isSafeInteger(rule.windowMs) ||
      (rule.matches !== undefined && (!Array.isArray(rule.matches) || !rule.matches.every(match =>
        isRecord(match) && Object.keys(match).sort().join(',') === 'currentPath,historicalPath' &&
        typeof match.currentPath === 'string' && typeof match.historicalPath === 'string')))) {
      throw new Error('POLICY_UNAVAILABLE: Invalid temporal rule')
    }
    try { validateTemporalRule(rule as unknown as TemporalRule) }
    catch { throw new Error('POLICY_UNAVAILABLE: Invalid temporal rule') }
  }
  const policy = immutableSnapshot(value as unknown as GatewayPolicy)
  return { policy, revision: `${policy.version}@${hashJson(policy)}` }
}

/** Each operation reads the authoritative file again; no stale cache is served. */
export class FilePolicySource implements PolicySource {
  constructor(readonly path: string) {}
  async current(): Promise<PolicySnapshot> {
    try { return validatePolicy(JSON.parse(await readFile(this.path, 'utf8')) as unknown) }
    catch (error) { throw new Error(`POLICY_UNAVAILABLE: ${error instanceof Error ? error.message : String(error)}`) }
  }
}

export async function writePolicyAtomically(path: string, policy: GatewayPolicy): Promise<void> {
  validatePolicy(policy)
  const temporary = `${path}.${randomUUID()}.tmp`
  await writeFile(temporary, JSON.stringify(policy, null, 2), { mode: 0o600 })
  await rename(temporary, path)
}
