import { randomUUID } from 'node:crypto'

import type { Event } from '../../interfaces/contracts'

export enum EventType {
  IntentReceived = 'IntentReceived',
  EvaluationCompleted = 'EvaluationCompleted',
  ExecutionCompleted = 'ExecutionCompleted'
}

export function createEvent(
  type: EventType,
  intentId: string,
  payload: unknown
): Event {
  return {
    id: randomUUID(),
    type,
    timestamp: Date.now(),
    intentId,
    payload
  }
}
