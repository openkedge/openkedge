import { IntentProposal, PolicyDecision } from '../core'

export interface PolicyInput {
  intent: {
    type: string
    payload: any
  }
  context: any
  blastRadius: any
  identity?: any
}

export interface PolicyAdapter {
  evaluate(input: PolicyInput): Promise<PolicyDecision>
}
