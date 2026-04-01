import { Actor } from './actor'
import { EntityRef } from './entity'
import { Fact } from './fact'

export type IntentProposal = {
  actor: Actor
  target: EntityRef
  intent: string
  proposedFacts: Fact[]
  metadata?: Record<string, any>
  timestamp: number
}
