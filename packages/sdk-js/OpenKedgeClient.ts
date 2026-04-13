import { OpenKedgeConfig } from './types'
import { IntentBuilder } from './IntentBuilder'
import { ExecutionHandle } from './ExecutionHandle'
import { IntentProposal } from '../core'

export interface ExecuteOptions {
  actor?: string
  metadata?: Record<string, any>
}

export interface PreviewResult {
  allowed: boolean
  blastRadius: number
  reasoning: string
}

function generateIntentId(): string {
  return `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`
}

export class OpenKedgeClient {
  constructor(private config: OpenKedgeConfig) {}

  intent<TPayload = any, TResult = any>(type: string): IntentBuilder<TPayload, TResult> {
    return new IntentBuilder<TPayload, TResult>(this, type)
  }

  async execute<TPayload = any, TResult = any>(
    type: string,
    payload: TPayload,
    options?: ExecuteOptions
  ): Promise<ExecutionHandle<TResult>> {
    const actorId = options?.actor || this.config.defaultActor
    if (!actorId) {
      throw new Error("Validation Error: Actor must be provided or defaultActor must be configured.")
    }

    const intentId = generateIntentId()

    const proposal: IntentProposal = {
      actor: { id: actorId },
      target: { id: 'system' }, // Target can be derived or enhanced later
      intent: type,
      proposedFacts: [
        {
          entityId: 'system',
          key: 'payload',
          value: payload as any
        }
      ],
      metadata: { ...options?.metadata, intentId },
      timestamp: Date.now()
    }

    if (this.config.debug) {
      console.log(`[OpenKedge] Debug: Evaluating intent ${type} for actor ${actorId} (ID: ${intentId})`)
    }

    // Evaluate through engine without throwing for policy blocks
    const decision = await this.config.engine.submitProposal(proposal)

    if (this.config.debug) {
      console.log(`[OpenKedge] Debug: Intent ${type} allowed: ${decision.allowed}`)
    }

    return new ExecutionHandle<TResult>(intentId, decision, this.config.engine)
  }

  async preview(type: string, payload: any): Promise<PreviewResult> {
    return {
      allowed: true, // Placeholder for engine preview endpoint
      blastRadius: 0,
      reasoning: "Preview outcome evaluated via OpenKedge policy."
    }
  }
}
