import type { PolicyEvaluator } from '../../core/evaluation/PolicyEvaluator'
import type { EvaluationResult, Intent } from '../../interfaces/contracts'

import type { AwsContext, AwsInstanceContext } from './AwsContextProvider'

function isCriticalInstance(instance: AwsInstanceContext): boolean {
  return instance.tags.critical === 'true'
}

export class AwsSafetyPolicyEvaluator implements PolicyEvaluator {
  async evaluate(
    intent: Intent,
    context: unknown
  ): Promise<EvaluationResult> {
    if (intent.type !== 'ec2:TerminateInstances') {
      return {
        allowed: true,
        reasons: ['Non-destructive operation'],
        enrichedContext: context
      }
    }

    const instances = ((context as AwsContext | undefined)?.instances ?? []).filter(
      (instance): instance is AwsInstanceContext => typeof instance === 'object'
    )
    const blocked = instances.filter(isCriticalInstance)

    if (blocked.length > 0) {
      return {
        allowed: false,
        reasons: [
          `Blocked termination of critical instances: ${blocked
            .map((instance) => instance.instanceId ?? 'unknown')
            .join(', ')}`
        ],
        enrichedContext: context
      }
    }

    return {
      allowed: true,
      reasons: ['No critical instances detected'],
      enrichedContext: context
    }
  }
}
