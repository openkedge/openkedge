import { DerivedState, TruthEvent } from '../core'

export type Context = {
  currentState: DerivedState | null
  recentEvents: TruthEvent[]
}
