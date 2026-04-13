import {
  DescribeInstancesCommand,
  EC2Client,
  type Instance
} from '@aws-sdk/client-ec2'

import type { ContextProvider } from '../../core/context/ContextProvider'
import type { Intent } from '../../interfaces/contracts'

export interface AwsInstanceContext {
  instanceId?: string
  state?: string
  tags: Record<string, string | undefined>
}

export interface AwsContext {
  instances: AwsInstanceContext[]
}

interface Ec2ClientLike {
  send(command: DescribeInstancesCommand): Promise<{
    Reservations?: Array<{ Instances?: Instance[] }>
  }>
}

function extractInstanceIds(intent: Intent): string[] {
  return Array.isArray(intent.payload)
    ? intent.payload.filter((value): value is string => typeof value === 'string')
    : []
}

function mapTags(tags: Instance['Tags']): Record<string, string | undefined> {
  return (tags ?? []).reduce<Record<string, string | undefined>>((acc, tag) => {
    if (tag.Key) {
      acc[tag.Key] = tag.Value
    }

    return acc
  }, {})
}

export class AwsContextProvider implements ContextProvider {
  private readonly ec2: Ec2ClientLike

  constructor(ec2: Ec2ClientLike = new EC2Client({})) {
    this.ec2 = ec2
  }

  async resolve(intent: Intent): Promise<AwsContext | Record<string, never>> {
    if (intent.type !== 'ec2:TerminateInstances') {
      return {}
    }

    const instanceIds = extractInstanceIds(intent)

    if (instanceIds.length === 0) {
      return { instances: [] }
    }

    const result = await this.ec2.send(
      new DescribeInstancesCommand({
        InstanceIds: instanceIds
      })
    )

    const instances =
      result.Reservations?.flatMap(
        (reservation) => reservation.Instances ?? []
      ) ?? []

    return {
      instances: instances.map((instance) => ({
        instanceId: instance.InstanceId,
        state: instance.State?.Name,
        tags: mapTags(instance.Tags)
      }))
    }
  }
}
