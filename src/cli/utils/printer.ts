import type {
  BlastRadius,
  ExecutionResult,
  ReplayResult
} from '../../interfaces/contracts'

export interface DecisionPrintPayload {
  intentType: string
  resourceCount: number
  riskLevel: string
  reasons: string[]
  matchedRules?: string[]
  executionResult?: ExecutionResult
}

export function printHelp(): void {
  console.log(`OpenKedge CLI

Usage:
  okg run <intent-file> [--interactive]
  okg preview <intent-file>
  okg replay <intent-id>
  okg serve [--host 127.0.0.1] [--port 4010]

Common options:
  --config <path>           Path to openkedge.config.yaml
  --policy-path <path>      Override the policy pack directory
  --policy-endpoint <url>   Override the OPA endpoint
  --store-path <path>       Override the replay event directory
  --actor <name>            Override the default actor`)
}

export function printInteractiveStep(
  title: string,
  message: string,
  details?: unknown
): void {
  console.log(`\n[interactive] ${title}`)
  console.log(message)

  if (details !== undefined) {
    console.log(JSON.stringify(details, null, 2))
  }
}

export function printDecision(
  allowed: boolean,
  payload: DecisionPrintPayload
): void {
  console.log(allowed ? 'ALLOWED ✅' : 'BLOCKED ❌')
  console.log('-----------------')
  console.log(`Intent: ${payload.intentType}`)
  console.log(`Resources: ${payload.resourceCount}`)
  console.log(`Risk: ${payload.riskLevel}`)

  if (payload.matchedRules && payload.matchedRules.length > 0) {
    console.log('')
    console.log('Matched Rules:')
    for (const rule of payload.matchedRules) {
      console.log(`- ${rule}`)
    }
  }

  console.log('')
  console.log('Reasons:')
  for (const reason of payload.reasons) {
    console.log(`- ${reason}`)
  }

  if (payload.executionResult) {
    console.log('')
    console.log('Execution:')
    console.log(
      payload.executionResult.success
        ? '- executor completed successfully'
        : `- ${payload.executionResult.error ?? 'executor failed'}`
    )
  }
}

export function printReplay(replay: ReplayResult): void {
  console.log(`Replay: ${replay.intentId}`)
  console.log('-----------------')
  console.log(`Intent: ${replay.originalIntent.type}`)
  console.log(`Outcome: ${replay.reconstructed.finalOutcome}`)
  console.log(`Integrity: ${replay.integrity.valid ? 'valid' : 'broken'}`)

  if (replay.reconstructed.blastRadius) {
    console.log(`Risk: ${replay.reconstructed.blastRadius.riskLevel}`)
  }

  console.log('')
  console.log('Reasoning Trail:')
  for (const reason of replay.reasoningTrail) {
    console.log(`- ${reason}`)
  }

  console.log('')
  console.log('Events:')
  for (const step of replay.steps) {
    console.log(`- ${new Date(step.timestamp).toISOString()} ${step.summary}`)
  }
}

export function printSystemError(message: string): void {
  console.error('SYSTEM ERROR')
  console.error('-----------------')
  console.error(message)
}

export function printServerStarted(host: string, port: number): void {
  console.log(`OpenKedge server listening on http://${host}:${port}`)
  console.log('Endpoints:')
  console.log('- POST /intent')
  console.log('- GET /replay/:id')
}

export function toDecisionPayload(args: {
  intentType: string
  blastRadius?: BlastRadius
  reasons: string[]
  matchedRules?: string[]
  executionResult?: ExecutionResult
}): DecisionPrintPayload {
  return {
    intentType: args.intentType,
    resourceCount: args.blastRadius?.resourceCount ?? 0,
    riskLevel: args.blastRadius?.riskLevel ?? 'UNKNOWN',
    reasons: args.reasons,
    matchedRules: args.matchedRules,
    executionResult: args.executionResult
  }
}
