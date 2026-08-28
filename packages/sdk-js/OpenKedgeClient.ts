import { OpenKedgeConfig } from './types'
import { IntentBuilder } from './IntentBuilder'
import { ExecutionHandle } from './ExecutionHandle'
import { IntentProposal } from '../core'
import type { CapabilityToken, IntentKind, TemporalBounds } from '../core'

export interface ExecuteOptions {
  actor?: string
  metadata?: Record<string, any>
  capabilities?: CapabilityToken[]
  requiredCapabilities?: string[]
  kind?: IntentKind
  temporalBounds?: Partial<TemporalBounds>
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
      id: intentId,
      actor: { id: actorId, type: 'unverified_agent', trust: 0 },
      target: { id: 'system', type: 'system' },
      capabilities: options?.capabilities,
      requiredCapabilities: options?.requiredCapabilities,
      kind: options?.kind,
      temporalBounds: options?.temporalBounds,
      intent: type,
      proposedFacts: [
        {
          type: 'payload',
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
