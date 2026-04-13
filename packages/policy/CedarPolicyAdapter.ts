import { PolicyDecision } from '../core'
import { PolicyAdapter, PolicyInput } from './PolicyTypes'

export class CedarPolicyAdapter implements PolicyAdapter {
  async evaluate(input: PolicyInput): Promise<PolicyDecision> {
    // Cedar Mapping:
    // principal -> actor
    // action -> intent.type
    // resource -> context.resources
    
    // For now, mocking the Cedar evaluation
    const isCritical = input.context?.currentState?.tags?.critical === 'true';
    const isTerminate = input.intent.type === 'ec2:TerminateInstances';

    if (isTerminate && isCritical) {
      return {
        allowed: false,
        reasons: ['Cedar Policy: Critical resources cannot be terminated.'],
        raw: { decision: 'Forbid' }
      }
    }

    return {
      allowed: true,
      reasons: ['Cedar Policy: Permit'],
      raw: { decision: 'Allow' }
    }
  }
}
