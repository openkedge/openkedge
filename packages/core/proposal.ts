import { Actor } from './actor'
import { EntityRef } from './entity'
import { Fact } from './fact'
import type { CapabilityToken, IntentKind, TemporalBounds } from '../../src/core/governance/types'

export type IntentProposal = {
  id?: string
  kind?: IntentKind
  capabilities?: CapabilityToken[]
  requiredCapabilities?: string[]
  temporalBounds?: Partial<TemporalBounds>
  actor: Actor
  target: EntityRef
  intent: string
  proposedFacts: Fact[]
  metadata?: Record<string, any>
  timestamp: number
}
