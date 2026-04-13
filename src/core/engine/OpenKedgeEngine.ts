import type {
  EvaluationResult,
  EventStore,
  ExecutionResult,
  Intent
} from '../../interfaces/contracts'
import type { ContextProvider } from '../context/ContextProvider'
import type { PolicyEvaluator } from '../evaluation/PolicyEvaluator'
import { createEvent, EventType } from '../event/Event'
import type { Executor } from '../execution/Executor'

type LogLevel = 'info' | 'warn' | 'error'

function normalizeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }

  return typeof error === 'string' ? error : 'Unknown error'
}

export class OpenKedgeEngine {
  constructor(
    private readonly contextProvider: ContextProvider,
    private readonly policyEvaluator: PolicyEvaluator,
    private readonly executor: Executor,
    private readonly eventStore: EventStore
  ) {}

  async process(intent: Intent): Promise<ExecutionResult> {
    let context: unknown = {}
    let evaluation: EvaluationResult = {
      allowed: false,
      reasons: ['Evaluation did not complete']
    }
    let execution: ExecutionResult = {
      success: false,
      error: 'Execution was not attempted'
    }

    await this.appendEvent(EventType.IntentReceived, intent.id, { intent })
    this.log('info', 'Intent received', {
      actor: intent.metadata.actor,
      intentId: intent.id,
      intentType: intent.type
    })

    try {
      context = await this.contextProvider.resolve(intent)
      this.log('info', 'Context resolved', {
        intentId: intent.id
      })

      evaluation = await this.policyEvaluator.evaluate(intent, context)
    } catch (error) {
      evaluation = {
        allowed: false,
        reasons: [`Pipeline failed: ${normalizeError(error)}`],
        enrichedContext: context
      }
      this.log('error', 'Evaluation pipeline failed', {
        error: normalizeError(error),
        intentId: intent.id
      })
    }

    if (evaluation.enrichedContext === undefined) {
      evaluation = {
        ...evaluation,
        enrichedContext: context
      }
    }

    await this.appendEvent(EventType.EvaluationCompleted, intent.id, {
      intent,
      context,
      evaluation
    })
    this.log('info', 'Evaluation completed', {
      allowed: evaluation.allowed,
      intentId: intent.id,
      reasons: evaluation.reasons
    })

    if (evaluation.allowed) {
      try {
        execution = await this.executor.execute(
          intent,
          evaluation.enrichedContext ?? context
        )
        this.log('info', 'Execution completed', {
          intentId: intent.id,
          success: execution.success
        })
      } catch (error) {
        execution = {
          success: false,
          error: `Execution failed: ${normalizeError(error)}`
        }
        this.log('error', 'Execution failed', {
          error: normalizeError(error),
          intentId: intent.id
        })
      }
    } else {
      execution = {
        success: false,
        error: `Intent blocked by policy: ${evaluation.reasons.join('; ')}`
      }
      this.log('warn', 'Intent blocked', {
        intentId: intent.id,
        reasons: evaluation.reasons
      })
    }

    await this.appendEvent(EventType.ExecutionCompleted, intent.id, {
      intent,
      context,
      evaluation,
      execution
    })

    return execution
  }

  private async appendEvent(
    type: EventType,
    intentId: string,
    payload: unknown
  ): Promise<void> {
    try {
      await this.eventStore.append(createEvent(type, intentId, payload))
    } catch (error) {
      this.log('error', 'Event append failed', {
        error: normalizeError(error),
        eventType: type,
        intentId
      })
    }
  }

  private log(
    level: LogLevel,
    message: string,
    fields: Record<string, unknown>
  ): void {
    const entry = JSON.stringify({
      level,
      message,
      timestamp: new Date().toISOString(),
      ...fields
    })

    if (level === 'error') {
      console.error(entry)
      return
    }

    if (level === 'warn') {
      console.warn(entry)
      return
    }

    console.log(entry)
  }
}
