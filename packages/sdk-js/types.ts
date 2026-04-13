import { IntentProposal, PolicyDecision, TruthEvent } from '../core'

export interface OpenKedgeEngine {
  submitProposal(proposal: IntentProposal): Promise<PolicyDecision>
  getEvents(intentId: string): Promise<TruthEvent[]>
}

export interface OpenKedgeConfig {
  engine: OpenKedgeEngine
  defaultActor?: string
  debug?: boolean
}

export interface ReplayResult {
  intentId: string
  events: TruthEvent[]
}

export interface SummaryResult {
  outcome: string
  reason: string
}
