import { Actor } from './actor'
import { Fact } from './fact'

export type TruthEvent = {
  entityId: string
  facts: Fact[]
  source: Actor
  timestamp: number
}
