import { DerivedState, TruthEvent } from '../core'
import { collectActiveFacts } from './collectActiveFacts'
import { composeFacts } from './composeFacts'

export function reduceEvents(entityId: string, events: TruthEvent[]): DerivedState {
  const facts = collectActiveFacts(events)
  return composeFacts(entityId, facts)
}
