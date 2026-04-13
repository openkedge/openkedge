import { randomUUID } from 'node:crypto'

import type {
  Event,
  EventType,
  ExecutionResult,
  EvaluationResult,
  Intent
} from '../../interfaces/contracts'

export interface IntentReceivedPayload {
  intent: Intent
}

export interface EvaluationCompletedPayload {
  intent: Intent
  context: unknown
  evaluation: EvaluationResult
}

export interface ExecutionCompletedPayload {
  intent: Intent
  context: unknown
  evaluation: EvaluationResult
  execution: ExecutionResult
}

export type OpenKedgeEventPayload =
  | IntentReceivedPayload
  | EvaluationCompletedPayload
  | ExecutionCompletedPayload

function createEventId(): string {
  try {
    return randomUUID()
  } catch {
    return `event-${Date.now()}-${Math.random().toString(16).slice(2)}`
  }
}

export function createEvent(
  type: EventType,
  intentId: string,
  payload: OpenKedgeEventPayload
): Event<OpenKedgeEventPayload> {
  return {
    id: createEventId(),
    type,
    timestamp: Date.now(),
    intentId,
    payload
  }
}
