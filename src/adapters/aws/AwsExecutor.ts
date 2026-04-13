import {
  DescribeInstancesCommand,
  EC2Client,
  TerminateInstancesCommand,
  type DescribeInstancesCommandOutput,
  type TerminateInstancesCommandOutput
} from '@aws-sdk/client-ec2'

import type { Executor } from '../../core/execution/Executor'
import { assertIdentityCanExecute } from '../../core/identity/Identity'
import type {
  ExecutionIdentity,
  ExecutionResult,
  Intent
} from '../../interfaces/contracts'
import { extractInstanceIds } from './extractInstanceIds'

type Ec2Command = DescribeInstancesCommand | TerminateInstancesCommand

interface Ec2ClientLike {
  send(
    command: Ec2Command
  ): Promise<DescribeInstancesCommandOutput | TerminateInstancesCommandOutput>
}

export interface AwsExecutorOptions {
  region?: string
  clientFactory?: (identity: ExecutionIdentity) => Ec2ClientLike
}

function normalizeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }

  return typeof error === 'string' ? error : 'Unknown error'
}

function extractAllowedInstanceIds(identity: ExecutionIdentity): string[] {
  const allowed = identity.metadata?.allowedInstanceIds

  return Array.isArray(allowed)
    ? allowed.filter((value): value is string => typeof value === 'string')
    : []
}

function assertAwsCredentialMaterial(
  identity: ExecutionIdentity
): asserts identity is ExecutionIdentity & {
  accessKeyId: string
  secretAccessKey: string
  sessionToken: string
} {
  if (
    !identity.accessKeyId ||
    !identity.secretAccessKey ||
    !identity.sessionToken
  ) {
    throw new Error(
      `Execution identity ${identity.id} is missing AWS session credentials`
    )
  }
}

function assertIntentResourceScope(
  intent: Intent,
  identity: ExecutionIdentity
): void {
  if (intent.type !== 'ec2:TerminateInstances') {
    return
  }

  const requestedInstanceIds = extractInstanceIds(intent)
  const allowedInstanceIds = extractAllowedInstanceIds(identity)

  if (allowedInstanceIds.length === 0) {
    throw new Error(
      `Execution identity ${identity.id} is missing an EC2 resource scope`
    )
  }

  const unauthorized = requestedInstanceIds.filter(
    (instanceId) => !allowedInstanceIds.includes(instanceId)
  )

  if (unauthorized.length > 0) {
    throw new Error(
      `Execution identity ${identity.id} does not cover instance IDs: ${unauthorized.join(', ')}`
    )
  }
}

export class AwsExecutor implements Executor {
  constructor(private readonly options: AwsExecutorOptions = {}) {}

  async execute(
    intent: Intent,
    _context: unknown,
    identity: ExecutionIdentity
  ): Promise<ExecutionResult> {
    assertIdentityCanExecute(intent, identity)
    assertIntentResourceScope(intent, identity)

    try {
      switch (intent.type) {
        case 'ec2:DescribeInstances': {
          const ec2 = this.createEc2Client(identity)
          const result = await ec2.send(new DescribeInstancesCommand({}))
          return {
            success: true,
            result
          }
        }

        case 'ec2:TerminateInstances': {
          const instanceIds = extractInstanceIds(intent)

          if (instanceIds.length === 0) {
            return {
              success: false,
              error:
                'TerminateInstances requires payload.instanceIds or a string[] payload of instance IDs'
            }
          }

          const ec2 = this.createEc2Client(identity)
          const result = await ec2.send(
            new TerminateInstancesCommand({
              InstanceIds: instanceIds
            })
          )

          return {
            success: true,
            result
          }
        }

        default:
          return {
            success: false,
            error: `Unsupported intent type: ${intent.type}`
          }
      }
    } catch (error) {
      return {
        success: false,
        error: normalizeError(error)
      }
    }
  }

  private createEc2Client(identity: ExecutionIdentity): Ec2ClientLike {
    if (this.options.clientFactory) {
      return this.options.clientFactory(identity)
    }

    assertAwsCredentialMaterial(identity)

    return new EC2Client({
      region: this.options.region ?? identity.metadata?.region,
      credentials: {
        accessKeyId: identity.accessKeyId,
        secretAccessKey: identity.secretAccessKey,
        sessionToken: identity.sessionToken
      }
    })
  }
}
