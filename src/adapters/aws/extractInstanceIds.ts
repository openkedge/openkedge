import type { Intent } from '../../interfaces/contracts'

function isStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.every((entry) => typeof entry === 'string')
  )
}

export function extractInstanceIds(intent: Intent): string[] {
  if (isStringArray(intent.payload)) {
    return intent.payload
  }

  if (
    typeof intent.payload === 'object' &&
    intent.payload !== null &&
    'instanceIds' in intent.payload &&
    isStringArray((intent.payload as { instanceIds?: unknown }).instanceIds)
  ) {
    return [...(intent.payload as { instanceIds: string[] }).instanceIds]
  }

  return []
}
