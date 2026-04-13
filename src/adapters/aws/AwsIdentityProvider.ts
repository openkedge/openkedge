import { randomUUID } from 'node:crypto'

import {
  AssumeRoleCommand,
  STSClient,
  type AssumeRoleCommandOutput
} from '@aws-sdk/client-sts'

import type { ExecutionIdentity } from '../../core/identity/Identity'
import type { IdentityProvider } from '../../core/identity/IdentityProvider'
import type { Intent } from '../../interfaces/contracts'
import { extractInstanceIds } from './extractInstanceIds'

interface StsClientLike {
  send(command: AssumeRoleCommand): Promise<AssumeRoleCommandOutput>
}

interface AwsPolicyStatement {
  Effect: 'Allow'
  Action: string[]
  Resource: string[]
}

export interface AwsPolicyDocument {
  Version: '2012-10-17'
  Statement: AwsPolicyStatement[]
}

export interface AwsIdentityProviderOptions {
  roleArn?: string
  region?: string
  sessionDurationSeconds?: number
  ttlSeconds?: number
  stsClient?: StsClientLike
}

function maskAccessKeyId(accessKeyId: string): string {
  if (accessKeyId.length <= 4) {
    return '****'
  }

  return `${accessKeyId.slice(0, 4)}****${accessKeyId.slice(-4)}`
}

function parseAccountIdFromRoleArn(roleArn: string): string {
  const segments = roleArn.split(':')

  if (segments.length < 5 || !segments[4]) {
    throw new Error(`Unable to parse account ID from role ARN: ${roleArn}`)
  }

  return segments[4]
}

function sanitizeSessionName(intentId: string): string {
  const safeIntentId = intentId.replace(/[^A-Za-z0-9+=,.@-]/g, '-')
  return `openkedge-${safeIntentId}`.slice(0, 64)
}

function resolveRegion(explicitRegion?: string): string {
  return (
    explicitRegion ??
    process.env.AWS_REGION ??
    process.env.AWS_DEFAULT_REGION ??
    'us-east-1'
  )
}

export function generatePolicy(
  intent: Intent,
  roleArn: string,
  region: string
): AwsPolicyDocument {
  switch (intent.type) {
    case 'ec2:TerminateInstances': {
      const instanceIds = extractInstanceIds(intent)

      if (instanceIds.length === 0) {
        throw new Error(
          'ec2:TerminateInstances requires payload.instanceIds or a string[] payload of instance IDs'
        )
      }

      const accountId = parseAccountIdFromRoleArn(roleArn)

      return {
        Version: '2012-10-17',
        Statement: [
          {
            Effect: 'Allow',
            Action: ['ec2:TerminateInstances'],
            Resource: instanceIds.map(
              (instanceId) =>
                `arn:aws:ec2:${region}:${accountId}:instance/${instanceId}`
            )
          }
        ]
      }
    }

    default:
      throw new Error(
        `Unsupported AWS intent type for identity policy generation: ${intent.type}`
      )
  }
}

export class AwsIdentityProvider implements IdentityProvider {
  private readonly sts: StsClientLike

  constructor(private readonly options: AwsIdentityProviderOptions = {}) {
    this.sts =
      options.stsClient ??
      new STSClient({
        region: resolveRegion(options.region)
      })
  }

  async issueIdentity(intent: Intent): Promise<ExecutionIdentity> {
    const roleArn = this.options.roleArn ?? process.env.OPENKEDGE_AWS_EXECUTION_ROLE_ARN

    if (!roleArn) {
      throw new Error(
        'AwsIdentityProvider requires roleArn or OPENKEDGE_AWS_EXECUTION_ROLE_ARN'
      )
    }

    const region = resolveRegion(this.options.region)
    const sessionDurationSeconds = this.options.sessionDurationSeconds ?? 900
    const sessionPolicy = generatePolicy(intent, roleArn, region)
    const issuedAt = Date.now()
    const response = await this.sts.send(
      new AssumeRoleCommand({
        RoleArn: roleArn,
        RoleSessionName: sanitizeSessionName(intent.id),
        DurationSeconds: sessionDurationSeconds,
        Policy: JSON.stringify(sessionPolicy)
      })
    )
    const credentials = response.Credentials

    if (
      !credentials?.AccessKeyId ||
      !credentials.SecretAccessKey ||
      !credentials.SessionToken
    ) {
      throw new Error('AWS STS AssumeRole did not return usable credentials')
    }

    const assumedRoleExpiresAt =
      credentials.Expiration?.getTime() ??
      issuedAt + sessionDurationSeconds * 1000
    const requestedTtlMs =
      this.options.ttlSeconds === undefined
        ? Number.POSITIVE_INFINITY
        : this.options.ttlSeconds * 1000
    const expiresAt = Math.min(assumedRoleExpiresAt, issuedAt + requestedTtlMs)
    const allowedInstanceIds = extractInstanceIds(intent)

    return {
      id: randomUUID(),
      intentId: intent.id,
      issuedAt,
      expiresAt,
      permissions: sessionPolicy.Statement.flatMap((statement) => statement.Action),
      accessKeyId: credentials.AccessKeyId,
      secretAccessKey: credentials.SecretAccessKey,
      sessionToken: credentials.SessionToken,
      metadata: {
        provider: 'aws-sts',
        roleArn,
        region,
        expiration:
          credentials.Expiration?.toISOString() ??
          new Date(expiresAt).toISOString(),
        accessKeyIdMasked: maskAccessKeyId(credentials.AccessKeyId),
        allowedInstanceIds,
        permissionScope: sessionPolicy.Statement.flatMap(
          (statement) => statement.Resource
        )
      }
    }
  }

  async revokeIdentity(identity: ExecutionIdentity): Promise<void> {
    identity.metadata = {
      ...identity.metadata,
      revokedAt: Date.now()
    }
    identity.accessKeyId = undefined
    identity.secretAccessKey = undefined
    identity.sessionToken = undefined
  }
}
