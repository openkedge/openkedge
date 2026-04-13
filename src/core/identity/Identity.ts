import type { Intent } from '../../interfaces/contracts'

export interface ExecutionIdentity {
  id: string
  intentId: string
  issuedAt: number
  expiresAt: number
  permissions: string[]
  metadata?: Record<string, any>
  accessKeyId?: string
  secretAccessKey?: string
  sessionToken?: string
}

export interface IdentityAuditRecord {
  identityId: string
  intentId: string
  issuedAt: number
  expiresAt: number
  permissions: string[]
  metadata?: Record<string, unknown>
}

export function isIdentityExpired(
  identity: ExecutionIdentity,
  now: number = Date.now()
): boolean {
  return identity.expiresAt <= now
}

export function assertIdentityCanExecute(
  intent: Intent,
  identity: ExecutionIdentity | undefined,
  now: number = Date.now()
): asserts identity is ExecutionIdentity {
  if (!identity) {
    throw new Error('Execution identity is required')
  }

  if (identity.intentId !== intent.id) {
    throw new Error(
      `Execution identity ${identity.id} is bound to intent ${identity.intentId}, not ${intent.id}`
    )
  }

  if (identity.metadata?.revokedAt !== undefined) {
    throw new Error(`Execution identity ${identity.id} has been revoked`)
  }

  if (isIdentityExpired(identity, now)) {
    throw new Error(
      `Execution identity ${identity.id} expired at ${new Date(
        identity.expiresAt
      ).toISOString()}`
    )
  }

  if (!identity.permissions.includes(intent.type)) {
    throw new Error(
      `Execution identity ${identity.id} does not permit ${intent.type}`
    )
  }
}

export function toIdentityAuditRecord(
  identity: ExecutionIdentity
): IdentityAuditRecord {
  return {
    identityId: identity.id,
    intentId: identity.intentId,
    issuedAt: identity.issuedAt,
    expiresAt: identity.expiresAt,
    permissions: [...identity.permissions],
    metadata:
      identity.metadata === undefined
        ? undefined
        : { ...stripSecretsFromMetadata(identity.metadata) }
  }
}

function stripSecretsFromMetadata(
  metadata: Record<string, any>
): Record<string, unknown> {
  const {
    secretAccessKey,
    sessionToken,
    accessKeyId,
    ...safeMetadata
  } = metadata

  return safeMetadata
}
