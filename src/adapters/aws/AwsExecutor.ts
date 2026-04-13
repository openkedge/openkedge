import {
  DescribeInstancesCommand,
  EC2Client,
  TerminateInstancesCommand,
  type DescribeInstancesCommandOutput,
  type TerminateInstancesCommandOutput
} from '@aws-sdk/client-ec2'

import type { Executor } from '../../core/execution/Executor'
import type { ExecutionResult, Intent } from '../../interfaces/contracts'

interface Ec2ClientLike {
  send(
    command: DescribeInstancesCommand | TerminateInstancesCommand
  ): Promise<DescribeInstancesCommandOutput | TerminateInstancesCommandOutput>
}

function extractInstanceIds(intent: Intent): string[] {
  return Array.isArray(intent.payload)
    ? intent.payload.filter((value): value is string => typeof value === 'string')
    : []
}

function normalizeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }

  return typeof error === 'string' ? error : 'Unknown error'
}

export class AwsExecutor implements Executor {
  private readonly ec2: Ec2ClientLike

  constructor(ec2: Ec2ClientLike = new EC2Client({})) {
    this.ec2 = ec2
  }

  async execute(intent: Intent, _context: unknown): Promise<ExecutionResult> {
    try {
      switch (intent.type) {
        case 'ec2:DescribeInstances': {
          const result = await this.ec2.send(new DescribeInstancesCommand({}))
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
              error: 'TerminateInstances requires a string[] payload of instance IDs'
            }
          }

          const result = await this.ec2.send(
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
}
