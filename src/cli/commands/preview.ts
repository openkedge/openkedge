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

export async function runPreviewCommand(
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
  const preview = await evaluateIntent(runtime, intent)

  if (flags.interactive) {
    printInteractiveStep('Intent', 'Loaded intent for preview', intent)
    printInteractiveStep('Context', 'Resolved execution context', preview.context)
    printInteractiveStep(
      'Blast Radius',
      'Estimated blast radius for the request',
      preview.blastRadius
    )
  }

  printDecision(
    preview.allowed,
    toDecisionPayload({
      intentType: intent.type,
      blastRadius: preview.blastRadius,
      reasons: preview.reasons,
      matchedRules: preview.matchedRules
    })
  )

  return preview.allowed ? 0 : 1
}
