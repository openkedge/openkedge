import { Context } from './types'

export function buildContext(entityId: string, store: any): Context {
  const events = store.getEvents(entityId)
  const currentState = store.getDerivedState ? store.getDerivedState(entityId) : null

  return {
    currentState,
    recentEvents: events.slice(-10)
  }
}
