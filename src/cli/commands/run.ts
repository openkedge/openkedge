import path from 'node:path'

import {
  printDecision,
  printInteractiveStep,
  toDecisionPayload
} from '../utils/printer'
import {
  createCliRuntime,
  evaluateIntent,
  loadCliIntent,
  type CliFlags
} from '../utils/runtime'

export async function runRunCommand(
  cwd: string,
  intentFile: string,
  flags: CliFlags
): Promise<number> {
  const runtime = await createCliRuntime(cwd, flags)
  const intent = await loadCliIntent(
    path.resolve(cwd, intentFile),
    runtime.config,
    flags.actor
  )

  if (flags.interactive) {
    const preview = await evaluateIntent(runtime, intent)
    printInteractiveStep('Intent', 'Loaded intent for execution', intent)
    printInteractiveStep('Context', 'Resolved execution context', preview.context)
    printInteractiveStep(
      'Blast Radius',
      'Estimated blast radius for the request',
      preview.blastRadius
    )
    printInteractiveStep(
      'Policy Decision',
      preview.allowed ? 'Policies allow execution to continue' : 'Policies blocked execution',
      {
        matchedRules: preview.matchedRules,
        reasons: preview.reasons
      }
    )
  }

  const executionResult = await runtime.client.submitIntent(intent)
  const replay = await runtime.client.replayIntent(intent.id)
  const evaluation = replay.reconstructed.evaluationResult
  const blastRadius = replay.reconstructed.blastRadius
  const allowed = replay.reconstructed.finalOutcome === 'allowed'

  printDecision(
    allowed,
    toDecisionPayload({
      intentType: intent.type,
      blastRadius,
      reasons: evaluation?.reasons ?? [executionResult.error ?? 'No evaluation details available'],
      matchedRules: evaluation?.matchedRules,
      executionResult
    })
  )

  if (replay.reconstructed.finalOutcome === 'blocked') {
    return 1
  }

  return executionResult.success ? 0 : 2
}
