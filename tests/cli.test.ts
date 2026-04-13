import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import { runPreviewCommand } from '../src/cli/commands/preview'
import { runReplayCommand } from '../src/cli/commands/replay'
import { runRunCommand } from '../src/cli/commands/run'

describe('OpenKedge CLI', () => {
  let tempDir: string
  let fetchMock: jest.Mock
  let consoleLogSpy: jest.SpyInstance

  beforeEach(async () => {
    tempDir = await mkdtemp(path.join(os.tmpdir(), 'openkedge-cli-'))
    fetchMock = jest.fn()
    consoleLogSpy = jest.spyOn(console, 'log').mockImplementation(() => undefined)
    jest.spyOn(console, 'error').mockImplementation(() => undefined)
    global.fetch = fetchMock as unknown as typeof fetch
  })

  afterEach(async () => {
    jest.restoreAllMocks()
    await rm(tempDir, {
      force: true,
      recursive: true
    })
  })

  test('preview returns exit code 1 for blocked policy pack decisions', async () => {
    await writeProjectFiles(tempDir, {
      config: `policy:
  provider: opa
  endpoint: http://opa.test
  path: ./policies/aws

defaults:
  actor: cli-user
`,
      intent: {
        type: 'ec2:TerminateInstances',
        payload: {
          instanceIds: Array.from({ length: 25 }, (_, index) => `i-${index + 1}`)
        }
      }
    })

    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes('/v1/policies/')) {
        return jsonResponse({}, 200)
      }

      const input = JSON.parse(String(init?.body)) as {
        input: { blastRadius: { resourceCount: number } }
      }

      expect(input.input.blastRadius.resourceCount).toBe(25)

      return jsonResponse(
        {
          result: {
            allow: false,
            reasons: ['blast radius exceeds threshold'],
            matchedRules: ['blast-radius-limit']
          }
        },
        200
      )
    })

    await expect(
      runPreviewCommand(tempDir, 'examples/intents/terminate.json', {
        policyEndpoint: 'http://opa.test'
      })
    ).resolves.toBe(1)
  })

  test('run persists replay data that replay can read back', async () => {
    await writeProjectFiles(tempDir, {
      config: `defaults:
  actor: cli-user
`,
      intent: {
        id: 'intent-fixed-id',
        type: 'resource:update',
        payload: {
          resourceId: 'resource-1'
        }
      }
    })

    await expect(
      runRunCommand(tempDir, 'examples/intents/terminate.json', {})
    ).resolves.toBe(0)

    await expect(runReplayCommand(tempDir, 'intent-fixed-id', {})).resolves.toBe(0)

    const replayFile = path.join(
      tempDir,
      '.openkedge',
      'replays',
      'intent-fixed-id.json'
    )
    const contents = await readFile(replayFile, 'utf8')

    expect(contents).toContain('IntentReceived')
    expect(consoleLogSpy).toHaveBeenCalled()
  })
})

async function writeProjectFiles(
  cwd: string,
  options: {
    config: string
    intent: Record<string, unknown>
  }
): Promise<void> {
  await mkdir(path.join(cwd, 'examples', 'intents'), {
    recursive: true
  })
  await mkdir(path.join(cwd, 'policies', 'aws'), {
    recursive: true
  })
  await writeFile(path.join(cwd, 'openkedge.config.yaml'), options.config, 'utf8')
  await writeFile(
    path.join(cwd, 'examples', 'intents', 'terminate.json'),
    JSON.stringify(options.intent, null, 2),
    'utf8'
  )
  await writeFile(
    path.join(cwd, 'policies', 'aws', 'safe-default.rego'),
    'package openkedge\nallow { true }\nmatchedRules["safe-default"] { true }\nreasons["approved"] { true }\n',
    'utf8'
  )
}

function jsonResponse(payload: unknown, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload
    },
    async text() {
      return JSON.stringify(payload)
    }
  } as Response
}
