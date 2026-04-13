import type {
  ContextProvider,
  EvaluationResult,
  Event,
  EventStore,
  ExecutionResult,
  Executor,
  Intent,
  PolicyEvaluator
} from '../../interfaces/contracts'
import { createEvent } from '../event'
import { consoleLogger, type Logger } from './logger'

export interface OpenKedgeEngineDependencies<TContext = unknown> {
  contextProvider: ContextProvider<TContext>
  policyEvaluator: PolicyEvaluator<TContext>
  executor: Executor<TContext>
  eventStore: EventStore
  logger?: Logger
}

function normalizeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }

  return typeof error === 'string' ? error : 'Unknown error'
}

export class OpenKedgeEngine<TContext = unknown> {
  private readonly logger: Logger

  constructor(private readonly dependencies: OpenKedgeEngineDependencies<TContext>) {
    this.logger = dependencies.logger ?? consoleLogger
  }

  async process(intent: Intent): Promise<ExecutionResult> {
    let context: TContext | unknown = {}
    let evaluation: EvaluationResult = {
      allowed: false,
      reasons: ['Evaluation did not complete']
    }
    let execution: ExecutionResult = {
      success: false,
      error: 'Execution was not attempted'
    }

    await this.safeAppend(
      createEvent('IntentReceived', intent.id, {
        intent
      })
    )
    this.logger.info('Intent received', {
      actor: intent.metadata.actor,
      intentId: intent.id,
      intentType: intent.type
    })

    try {
      context = await this.dependencies.contextProvider.resolve(intent)
      this.logger.info('Context resolved', {
        intentId: intent.id
      })

      evaluation = await this.dependencies.policyEvaluator.evaluate(
        intent,
        context as TContext
      )
    } catch (error) {
      evaluation = {
        allowed: false,
        reasons: [`Pipeline failed: ${normalizeError(error)}`],
        enrichedContext: context
      }
      this.logger.error('Evaluation pipeline failed', {
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

    await this.safeAppend(
      createEvent('EvaluationCompleted', intent.id, {
        context,
        evaluation,
        intent
      })
    )
    this.logger.info('Evaluation completed', {
      allowed: evaluation.allowed,
      intentId: intent.id,
      reasons: evaluation.reasons
    })

    if (evaluation.allowed) {
      try {
        execution = await this.dependencies.executor.execute(
          intent,
          (evaluation.enrichedContext ?? context) as TContext
        )
        this.logger.info('Execution completed', {
          intentId: intent.id,
          success: execution.success
        })
      } catch (error) {
        execution = {
          success: false,
          error: `Execution failed: ${normalizeError(error)}`
        }
        this.logger.error('Execution failed', {
          error: normalizeError(error),
          intentId: intent.id
        })
      }
    } else {
      execution = {
        success: false,
        error: `Intent blocked by policy: ${evaluation.reasons.join('; ')}`
      }
      this.logger.warn('Intent blocked', {
        intentId: intent.id,
        reasons: evaluation.reasons
      })
    }

    await this.safeAppend(
      createEvent('ExecutionCompleted', intent.id, {
        context,
        evaluation,
        execution,
        intent
      })
    )

    return execution
  }

  private async safeAppend(event: Event): Promise<void> {
    try {
      await this.dependencies.eventStore.append(event)
    } catch (error) {
      this.logger.error('Event append failed', {
        error: normalizeError(error),
        eventType: event.type,
        intentId: event.intentId
      })
    }
  }
}
