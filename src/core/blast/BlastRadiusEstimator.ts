import type { Intent } from '../../interfaces/contracts'
import type { AwsContext, AwsInstanceContext } from '../../adapters/aws/AwsContextProvider'

import type { BlastRadius, BlastRiskLevel } from './BlastRadiusTypes'

function formatResourceCount(resourceCount: number): string {
  return `${resourceCount} resource${resourceCount === 1 ? '' : 's'}`
}

function extractResourceIds(intent: Intent, context: unknown): string[] {
  if (
    intent.type === 'ec2:TerminateInstances' &&
    Array.isArray((context as AwsContext | undefined)?.instances)
  ) {
    const instances = (context as AwsContext).instances

    return instances
      .map((instance) => instance.instanceId)
      .filter((instanceId): instanceId is string => typeof instanceId === 'string')
  }

  if (Array.isArray(intent.payload)) {
    return intent.payload.filter((value): value is string => typeof value === 'string')
  }

  const resourceId =
    typeof intent.payload === 'object' &&
    intent.payload !== null &&
    'resourceId' in intent.payload &&
    typeof (intent.payload as { resourceId?: unknown }).resourceId === 'string'
      ? (intent.payload as { resourceId: string }).resourceId
      : undefined

  return resourceId ? [resourceId] : []
}

function baseRiskLevel(resourceCount: number): BlastRiskLevel {
  if (resourceCount <= 1) {
    return 'LOW'
  }

  if (resourceCount <= 5) {
    return 'MEDIUM'
  }

  if (resourceCount <= 20) {
    return 'HIGH'
  }

  return 'CRITICAL'
}

function bumpRiskLevel(riskLevel: BlastRiskLevel): BlastRiskLevel {
  switch (riskLevel) {
    case 'LOW':
      return 'MEDIUM'
    case 'MEDIUM':
      return 'HIGH'
    case 'HIGH':
      return 'CRITICAL'
    case 'CRITICAL':
      return 'CRITICAL'
  }
}

function isCriticalInstance(instance: AwsInstanceContext): boolean {
  return instance.tags.critical === 'true'
}

function isProductionInstance(instance: AwsInstanceContext): boolean {
  return instance.tags.env === 'prod'
}

export class BlastRadiusEstimator {
  estimate(intent: Intent, context: unknown): BlastRadius {
    const resourceIds = extractResourceIds(intent, context)
    const reasons = [
      `Intent targets ${formatResourceCount(resourceIds.length)}`
    ]

    let riskLevel = baseRiskLevel(resourceIds.length)

    if (intent.type === 'ec2:TerminateInstances') {
      const instances = (context as AwsContext | undefined)?.instances ?? []
      const criticalInstances = instances.filter(isCriticalInstance)
      const productionInstances = instances.filter(isProductionInstance)

      if (criticalInstances.length > 0) {
        riskLevel = 'CRITICAL'
        reasons.push(
          `Critical instances detected: ${criticalInstances
            .map((instance) => instance.instanceId ?? 'unknown')
            .join(', ')}`
        )
      } else if (productionInstances.length > 0) {
        riskLevel = bumpRiskLevel(riskLevel)
        reasons.push(
          `Production instances detected: ${productionInstances
            .map((instance) => instance.instanceId ?? 'unknown')
            .join(', ')}`
        )
      }
    }

    reasons.push(
      `Blast radius evaluated as ${riskLevel} (${formatResourceCount(resourceIds.length)})`
    )

    return {
      resourceCount: resourceIds.length,
      resourceIds,
      riskLevel,
      reasons
    }
  }
}
