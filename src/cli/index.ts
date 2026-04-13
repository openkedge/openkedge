#!/usr/bin/env node

import { runPreviewCommand } from './commands/preview'
import { runReplayCommand } from './commands/replay'
import { runRunCommand } from './commands/run'
import { runServeCommand } from './commands/serve'
import { printHelp, printSystemError } from './utils/printer'
import type { CliFlags } from './utils/runtime'

type FlagValue = string | boolean | undefined

interface ParsedArgs {
  flags: Record<string, FlagValue> & CliFlags & {
    host?: string
    port?: string
  }
  positionals: string[]
}

function parseArgs(args: string[]): ParsedArgs {
  const flags: ParsedArgs['flags'] = {}
  const positionals: string[] = []

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index]

    if (!arg.startsWith('--')) {
      positionals.push(arg)
      continue
    }

    const [rawKey, inlineValue] = arg.slice(2).split('=', 2)
    const key = camelCaseFlag(rawKey)

    if (inlineValue !== undefined) {
      flags[key] = inlineValue
      continue
    }

    const next = args[index + 1]

    if (!next || next.startsWith('--')) {
      flags[key] = true
      continue
    }

    flags[key] = next
    index += 1
  }

  return { flags, positionals }
}

function camelCaseFlag(flag: string): keyof ParsedArgs['flags'] {
  return flag.replace(/-([a-z])/g, (_, char: string) => char.toUpperCase()) as keyof ParsedArgs['flags']
}

async function main(): Promise<void> {
  const [, , command, ...args] = process.argv

  if (!command || command === 'help' || command === '--help' || command === '-h') {
    printHelp()
    return
  }

  const parsed = parseArgs(args)
  let exitCode = 0

  switch (command) {
    case 'preview': {
      const intentFile = parsed.positionals[0]

      if (!intentFile) {
        throw new Error('preview requires an <intent-file> argument')
      }

      exitCode = await runPreviewCommand(process.cwd(), intentFile, parsed.flags)
      break
    }

    case 'run': {
      const intentFile = parsed.positionals[0]

      if (!intentFile) {
        throw new Error('run requires an <intent-file> argument')
      }

      exitCode = await runRunCommand(process.cwd(), intentFile, parsed.flags)
      break
    }

    case 'replay': {
      const intentId = parsed.positionals[0]

      if (!intentId) {
        throw new Error('replay requires an <intent-id> argument')
      }

      exitCode = await runReplayCommand(process.cwd(), intentId, parsed.flags)
      break
    }

    case 'serve':
      exitCode = await runServeCommand(process.cwd(), parsed.flags)
      break

    default:
      throw new Error(`Unknown command: ${command}`)
  }

  process.exitCode = exitCode
}

void main().catch((error) => {
  printSystemError(error instanceof Error ? error.message : String(error))
  process.exitCode = 2
})
