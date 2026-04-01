import { TruthEvent, Fact } from '../core'

export function collectActiveFacts(events: TruthEvent[]): Fact[] {
  // filter by validity window
  const now = Date.now()
  return events.flatMap(e => e.facts).filter(f => {
    if (!f.validity) return true;
    if (f.validity.start > now) return false;
    if (f.validity.end && f.validity.end < now) return false;
    return true;
  })
}
