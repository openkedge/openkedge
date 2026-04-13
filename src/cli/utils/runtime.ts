import { randomUUID } from 'node:crypto'

import { createAwsAdapter } from '../../adapters/aws'
import { BlastRadiusEstimator } from '../../core/blast/BlastRadiusEstimator'
import { BlastRadiusPolicy } from '../../core/blast/BlastRadiusPolicy'
import { DefaultContextProvider } from '../../core/context/DefaultContextProvider'
import { OpenKedgeEngine } from '../../core/engine/OpenKedgeEngine'
import { FileEventStore } from '../../core/event/FileEventStore'
import { NoopExecutor } from '../../core/execution/NoopExecutor'
import { IdentityManager } from '../../core/identity/IdentityManager'
import type {
  BlastRadius,
  ContextProvider,
  EvaluationResult,
  Intent,
  PolicyEvaluator
} from '../../interfaces/contracts'
import { OpenKedgeClient } from '../../sdk/client'
import {
  loadIntentFile,
  loadOpenKedgeConfig,
  loadPolicyFiles,
  resolvePolicyEndpoint,
  resolvePolicyPath,
  resolveStorePath,
  type OpenKedgeConfig
} from './loader'
import { OpaPolicyPackEvaluator } from './opa'

export interface CliFlags {
  actor?: string
  config?: string
  interactive?: boolean
  policyEndpoint?: string
  policyPath?: string
  storePath?: string
}

export interface CliRuntime {
  client: OpenKedgeClient
  config: OpenKedgeConfig
  configPath?: string
  contextProvider: ContextProvider
  policyEvaluator: PolicyEvaluator
  blastRadiusEstimator: BlastRadiusEstimator
  blastRadiusPolicy: BlastRadiusPolicy
  store: FileEventStore
}

export interface IntentEvaluation {
  intent: Intent
  context: unknown
  blastRadius: BlastRadius
  evaluationResult: EvaluationResult
  allowed: boolean
  reasons: string[]
  matchedRules: string[]
}

export async function createCliRuntime(
  cwd: string,
  flags: CliFlags = {}
): Promise<CliRuntime> {
  const { config, path: configPath } = await loadOpenKedgeConfig(
    cwd,
    flags.config
  )
  const contextProvider = createContextProvider(config)
  const policyEvaluator = await createPolicyEvaluator(cwd, config, flags)
  const store = new FileEventStore(resolveStorePath(cwd, config, flags.storePath))
  const blastRadiusEstimator = new BlastRadiusEstimator()
  const blastRadiusPolicy = new BlastRadiusPolicy()
  const identityManager = new IdentityManager(
    {
      async issueIdentity(intent) {
        const issuedAt = Date.now()

        return {
          id: `cli-${intent.id}-${issuedAt}`,
          intentId: intent.id,
          issuedAt,
          expiresAt: issuedAt + 60_000,
          permissions: [intent.type],
          metadata: {
            provider: 'cli-local'
          }
        }
      },
      async revokeIdentity(identity) {
        identity.metadata = {
          ...identity.metadata,
          revokedAt: Date.now()
        }
      }
    },
    store
  )

  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      contextProvider,
      policyEvaluator,
      new NoopExecutor(),
      identityManager,
      store,
      blastRadiusEstimator,
      blastRadiusPolicy
    ),
    store
  )

  return {
    client,
    config,
    configPath,
    contextProvider,
    policyEvaluator,
    blastRadiusEstimator,
    blastRadiusPolicy,
    store
  }
}

export async function loadCliIntent(
  intentPath: string,
  config: OpenKedgeConfig,
  actorOverride?: string
): Promise<Intent> {
  const parsedIntent = await loadIntentFile(intentPath)

  return {
    id: parsedIntent.id ?? randomUUID(),
    type: parsedIntent.type,
    payload: parsedIntent.payload,
    metadata: {
      actor:
        actorOverride ??
        parsedIntent.metadata?.actor ??
        config.defaults?.actor ??
        'cli-user',
      timestamp: parsedIntent.metadata?.timestamp ?? Date.now()
    }
  }
}

export async function evaluateIntent(
  runtime: CliRuntime,
  intent: Intent
): Promise<IntentEvaluation> {
  const context = await runtime.contextProvider.resolve(intent)
  const blastRadius = runtime.blastRadiusEstimator.estimate(intent, context)
  const policyEvaluation = await runtime.policyEvaluator.evaluate(
    intent,
    context,
    blastRadius
  )
  const blastDecision = runtime.blastRadiusPolicy.evaluate(blastRadius)
  const reasons = [...policyEvaluation.reasons, ...blastDecision.reasons]
  const allowed = policyEvaluation.allowed && blastDecision.allowed

  return {
    intent,
    context,
    blastRadius,
    evaluationResult: {
      ...policyEvaluation,
      allowed,
      reasons
    },
    allowed,
    reasons,
    matchedRules: policyEvaluation.matchedRules ?? []
  }
}

function createContextProvider(config: OpenKedgeConfig): ContextProvider {
  if (config.context?.provider === 'aws') {
    return createAwsAdapter().contextProvider
  }

  return new DefaultContextProvider()
}

async function createPolicyEvaluator(
  cwd: string,
  config: OpenKedgeConfig,
  flags: CliFlags
): Promise<PolicyEvaluator> {
  if (config.policy?.provider === 'opa') {
    const policyPath = resolvePolicyPath(cwd, config, flags.policyPath)
    const policies = await loadPolicyFiles(policyPath)
    return new OpaPolicyPackEvaluator(
      resolvePolicyEndpoint(config, flags.policyEndpoint),
      policies
    )
  }

  if (config.context?.provider === 'aws') {
    return createAwsAdapter().policyEvaluator
  }

  return {
    async evaluate(_intent, context) {
      return {
        allowed: true,
        reasons: ['No policy provider configured; default allow applied'],
        enrichedContext: context
      }
    }
  }
}
