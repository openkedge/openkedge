import type {
  BlastRadius,
  EvaluationResult,
  Intent
} from '../../interfaces/contracts'
import type { LoadedPolicyFile } from './loader'

function normalizeEndpoint(endpoint: string): string {
  return endpoint.replace(/\/+$/, '')
}

function asStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined
  }

  return value.filter((entry): entry is string => typeof entry === 'string')
}

export class OpaPolicyPackEvaluator {
  private policiesLoaded = false

  constructor(
    private readonly endpoint: string,
    private readonly policies: LoadedPolicyFile[]
  ) {}

  async evaluate(
    intent: Intent,
    context: unknown,
    blastRadius?: BlastRadius
  ): Promise<EvaluationResult> {
    await this.ensurePoliciesLoaded()

    const response = await fetch(
      `${normalizeEndpoint(this.endpoint)}/v1/data/openkedge`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          input: {
            intent,
            context,
            blastRadius
          }
        })
      }
    )

    if (!response.ok) {
      throw new Error(
        `OPA evaluation failed with ${response.status}: ${await response.text()}`
      )
    }

    const data = (await response.json()) as {
      result?: {
        allow?: boolean
        reasons?: unknown
        matchedRules?: unknown
      }
    }
    const result = data.result ?? {}

    return {
      allowed: result.allow === true,
      reasons:
        asStringArray(result.reasons) ??
        ['OPA denied the request without returning explicit reasons'],
      matchedRules: asStringArray(result.matchedRules),
      enrichedContext: context
    }
  }

  private async ensurePoliciesLoaded(): Promise<void> {
    if (this.policiesLoaded) {
      return
    }

    for (const policy of this.policies) {
      const response = await fetch(
        `${normalizeEndpoint(this.endpoint)}/v1/policies/${encodeURIComponent(policy.id)}`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'text/plain'
          },
          body: policy.contents
        }
      )

      if (!response.ok) {
        throw new Error(
          `OPA policy upload failed for ${policy.path} with ${response.status}: ${await response.text()}`
        )
      }
    }

    this.policiesLoaded = true
  }
}
