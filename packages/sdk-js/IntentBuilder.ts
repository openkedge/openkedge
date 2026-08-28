import { OpenKedgeClient } from './OpenKedgeClient'
import { ExecutionHandle } from './ExecutionHandle'
import type { CapabilityToken } from '../core'

export class IntentBuilder<TPayload = any, TResult = any> {
  private _payload?: TPayload
  private _actor?: string
  private _metadata?: Record<string, any>
  private _capabilities?: CapabilityToken[]
  private _requiredCapabilities?: string[]

  constructor(
    private client: OpenKedgeClient,
    private type: string
  ) {}

  payload(p: TPayload): this {
    this._payload = p
    return this
  }

  actor(a: string): this {
    this._actor = a
    return this
  }

  metadata(m: Record<string, any>): this {
    this._metadata = m
    return this
  }

  async execute(): Promise<ExecutionHandle<TResult>> {
    const payload = this._payload || ({} as TPayload)
    return this.client.execute<TPayload, TResult>(this.type, payload, {
      actor: this._actor,
      metadata: this._metadata,
      capabilities: this._capabilities,
      requiredCapabilities: this._requiredCapabilities
    })
  }

  capabilities(tokens: CapabilityToken[]): this {
    this._capabilities = tokens
    return this
  }

  requiredCapabilities(tokenIds: string[]): this {
    this._requiredCapabilities = tokenIds
    return this
  }
}
