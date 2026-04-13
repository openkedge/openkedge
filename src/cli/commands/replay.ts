import { createCliRuntime, type CliFlags } from '../utils/runtime'
import { printReplay } from '../utils/printer'

export async function runReplayCommand(
  cwd: string,
  intentId: string,
  flags: CliFlags
): Promise<number> {
  const runtime = await createCliRuntime(cwd, flags)
  const replay = await runtime.client.replayIntent(intentId)

  printReplay(replay)
  return replay.reconstructed.finalOutcome === 'blocked' ? 1 : 0
}
