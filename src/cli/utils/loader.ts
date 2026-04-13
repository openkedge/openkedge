import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'

import type { Intent } from '../../interfaces/contracts'

export interface OpenKedgeConfig {
  policy?: {
    provider?: 'opa' | 'cedar' | string
    endpoint?: string
    path?: string
  }
  context?: {
    provider?: 'aws' | 'default' | string
  }
  defaults?: {
    actor?: string
  }
  storage?: {
    path?: string
  }
  server?: {
    host?: string
    port?: number
  }
}

export interface LoadedPolicyFile {
  id: string
  path: string
  contents: string
}

export async function loadIntentFile(intentPath: string): Promise<Omit<Intent, 'id' | 'metadata'> & {
  id?: string
  metadata?: Partial<Intent['metadata']>
}> {
  const raw = await readFile(intentPath, 'utf8')
  const parsed = JSON.parse(raw) as Omit<Intent, 'id' | 'metadata'> & {
    id?: string
    metadata?: Partial<Intent['metadata']>
  }

  if (!parsed || typeof parsed !== 'object') {
    throw new Error(`Intent file ${intentPath} must contain a JSON object`)
  }

  if (typeof parsed.type !== 'string' || parsed.type.length === 0) {
    throw new Error(`Intent file ${intentPath} is missing a string "type" field`)
  }

  return parsed
}

export async function loadOpenKedgeConfig(
  cwd: string,
  explicitPath?: string
): Promise<{ config: OpenKedgeConfig; path?: string }> {
  const candidatePaths = explicitPath
    ? [path.resolve(cwd, explicitPath)]
    : [
        path.join(cwd, 'openkedge.config.yaml'),
        path.join(cwd, 'openkedge.config.yml'),
        path.join(cwd, 'openkedge.config.json')
      ]

  for (const candidatePath of candidatePaths) {
    try {
      const raw = await readFile(candidatePath, 'utf8')
      return {
        config: parseConfig(raw, candidatePath),
        path: candidatePath
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        continue
      }

      throw error
    }
  }

  return { config: {} }
}

export async function loadPolicyFiles(
  policyDirectory: string
): Promise<LoadedPolicyFile[]> {
  const policyFiles = await collectPolicyFiles(policyDirectory, policyDirectory)

  if (policyFiles.length === 0) {
    throw new Error(`No .rego policy files found in ${policyDirectory}`)
  }

  return policyFiles.sort((left, right) => left.path.localeCompare(right.path))
}

export function resolvePolicyPath(
  cwd: string,
  config: OpenKedgeConfig,
  override?: string
): string {
  if (override) {
    return path.resolve(cwd, override)
  }

  if (config.policy?.path) {
    return path.resolve(cwd, config.policy.path)
  }

  if (config.context?.provider === 'aws') {
    return path.join(cwd, 'policies', 'aws')
  }

  return path.join(cwd, 'policies')
}

export function resolvePolicyEndpoint(
  config: OpenKedgeConfig,
  override?: string
): string {
  return override ?? config.policy?.endpoint ?? 'http://localhost:8181'
}

export function resolveStorePath(
  cwd: string,
  config: OpenKedgeConfig,
  override?: string
): string {
  return path.resolve(
    cwd,
    override ?? config.storage?.path ?? '.openkedge/replays'
  )
}

export function resolveServerHost(
  config: OpenKedgeConfig,
  override?: string
): string {
  return override ?? config.server?.host ?? '127.0.0.1'
}

export function resolveServerPort(
  config: OpenKedgeConfig,
  override?: string
): number {
  if (override) {
    const parsed = Number(override)

    if (!Number.isInteger(parsed) || parsed <= 0) {
      throw new Error(`Invalid server port: ${override}`)
    }

    return parsed
  }

  return config.server?.port ?? 4010
}

async function collectPolicyFiles(
  rootDirectory: string,
  currentDirectory: string
): Promise<LoadedPolicyFile[]> {
  const entries = await readdir(currentDirectory, {
    withFileTypes: true
  })
  const policyFiles: LoadedPolicyFile[] = []

  for (const entry of entries) {
    const entryPath = path.join(currentDirectory, entry.name)

    if (entry.isDirectory()) {
      policyFiles.push(...(await collectPolicyFiles(rootDirectory, entryPath)))
      continue
    }

    if (!entry.isFile() || !entry.name.endsWith('.rego')) {
      continue
    }

    policyFiles.push({
      id: toPolicyId(path.relative(rootDirectory, entryPath)),
      path: entryPath,
      contents: await readFile(entryPath, 'utf8')
    })
  }

  return policyFiles
}

function parseConfig(raw: string, filePath: string): OpenKedgeConfig {
  if (filePath.endsWith('.json')) {
    return JSON.parse(raw) as OpenKedgeConfig
  }

  return parseSimpleYaml(raw) as OpenKedgeConfig
}

function parseSimpleYaml(raw: string): Record<string, unknown> {
  const root: Record<string, unknown> = {}
  const stack: Array<{ indent: number; value: Record<string, unknown> }> = [
    { indent: -1, value: root }
  ]

  for (const rawLine of raw.split(/\r?\n/)) {
    const line = rawLine.replace(/#.*$/, '')

    if (!line.trim()) {
      continue
    }

    const indent = line.match(/^ */)?.[0].length ?? 0
    const trimmed = line.trim()
    const separatorIndex = trimmed.indexOf(':')

    if (separatorIndex === -1) {
      throw new Error(`Unsupported YAML line: ${rawLine}`)
    }

    const key = trimmed.slice(0, separatorIndex).trim()
    const remainder = trimmed.slice(separatorIndex + 1).trim()

    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) {
      stack.pop()
    }

    const parent = stack[stack.length - 1].value

    if (remainder === '') {
      const child: Record<string, unknown> = {}
      parent[key] = child
      stack.push({ indent, value: child })
      continue
    }

    parent[key] = parseYamlScalar(remainder)
  }

  return root
}

function parseYamlScalar(value: string): string | number | boolean | null {
  if (value === 'true') {
    return true
  }

  if (value === 'false') {
    return false
  }

  if (value === 'null') {
    return null
  }

  if (/^-?\d+$/.test(value)) {
    return Number(value)
  }

  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1)
  }

  return value
}

function toPolicyId(relativePath: string): string {
  return relativePath
    .replace(/\\/g, '/')
    .replace(/\.rego$/i, '')
    .replace(/[^A-Za-z0-9/_-]+/g, '-')
}
