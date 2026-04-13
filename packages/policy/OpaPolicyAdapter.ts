import { PolicyDecision } from '../core'
import { PolicyAdapter, PolicyInput } from './PolicyTypes'

export class OpaPolicyAdapter implements PolicyAdapter {
  constructor(private endpoint: string) {}

  async evaluate(input: PolicyInput): Promise<PolicyDecision> {
    const response = await fetch(this.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input })
    })

    const data = await response.json()
    const result = data.result || { allow: false, reasons: ['OPA Decision Failed'] }

    return {
      allowed: result.allow === true,
      reasons: result.reasons || [],
      raw: data
    }
  }
}
