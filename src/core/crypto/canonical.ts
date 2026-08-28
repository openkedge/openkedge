import { createHash, createHmac, timingSafeEqual } from 'node:crypto'

/** Canonical JSON; rejects values that JSON would silently coerce or discard. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value)
  if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value)
  if (Array.isArray(value)) return `[${Array.from(value, canonicalJson).join(',')}]`
  if (isRecord(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`
  }
  throw new Error('Expected finite JSON data')
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function readPath(value: unknown, path: string): unknown {
  if (!path || path.split('.').some(key => !key || ['__proto__', 'constructor', 'prototype'].includes(key))) {
    throw new Error(`Invalid attribute path: ${path}`)
  }
  for (const key of path.split('.')) {
    if ((!isRecord(value) && !Array.isArray(value)) || !Object.prototype.hasOwnProperty.call(value, key)) return undefined
    value = (value as Record<string, unknown>)[key]
  }
  return value
}

export function hashJson(value: unknown): string {
  return createHash('sha256').update(canonicalJson(value)).digest('hex')
}

export function signJson(domain: string, value: unknown, secretKey: string): string {
  if (Buffer.byteLength(secretKey) < 32) throw new Error('Signing key must contain at least 32 bytes')
  return createHmac('sha256', secretKey).update(`${domain}\n${canonicalJson(value)}`).digest('hex')
}

export function validSignature(actual: unknown, expected: string): boolean {
  return typeof actual === 'string' && /^[a-f0-9]{64}$/.test(actual) &&
    timingSafeEqual(Buffer.from(actual, 'hex'), Buffer.from(expected, 'hex'))
}

export function immutableSnapshot<T>(value: T): T {
  // Event payloads historically contain optional undefined fields; preserve JSON semantics.
  const snapshot = JSON.parse(JSON.stringify(value)) as T
  const freeze = (item: unknown): void => {
    if (item !== null && typeof item === 'object') {
      Object.values(item).forEach(freeze)
      Object.freeze(item)
    }
  }
  freeze(snapshot)
  return snapshot
}
