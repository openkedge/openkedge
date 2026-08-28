import type { Intent } from '../../interfaces/contracts'
import { canonicalJson, readPath } from '../crypto/canonical'
import type { IEECRecord, TemporalEvaluationResult, TemporalRule, TraceReader } from './types'

export function validateTemporalRule(rule: TemporalRule): void {
  if (!rule.targetAction || !Number.isSafeInteger(rule.windowMs) || rule.windowMs <= 0 ||
      (rule.scope !== undefined && !['ACTOR', 'GLOBAL', 'RESOURCE'].includes(rule.scope))) throw new Error('Invalid temporal rule window or scope')
  if (rule.scope === 'RESOURCE') {
    if (!rule.resourcePath) throw new Error('RESOURCE scope requires resourcePath')
    readPath({}, rule.resourcePath)
  }
  switch (rule.type) {
    case 'SLIDING_WINDOW_QUOTA':
      if (!Number.isFinite(rule.maxCumulativeValue) || rule.maxCumulativeValue < 0) throw new Error('Invalid quota limit')
      readPath({}, rule.metricPath)
      break
    case 'RATE_LIMIT':
      if (!Number.isSafeInteger(rule.maxCount) || rule.maxCount < 0) throw new Error('Invalid rate limit')
      break
    case 'PRECEDING_EVENT_REQUIRED':
      if (!rule.requiredPrecedingAction) throw new Error('Missing preceding action')
      for (const match of rule.matches ?? []) {
        readPath({}, match.currentPath)
        readPath({}, match.historicalPath)
      }
      break
    default: throw new Error('Unknown temporal rule')
  }
}

function metric(value: unknown, path: string): number {
  const amount = readPath(value, path)
  if (typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) {
    throw new Error(`Metric ${path} must be a finite non-negative number`)
  }
  return amount
}

function sameValue(left: unknown, right: unknown): boolean {
  return left !== undefined && right !== undefined && canonicalJson(left) === canonicalJson(right)
}

/** Pure decision reduction given a trace snapshot and trusted evaluation time. */
export async function evaluateTemporalRules(
  proposal: Intent,
  history: TraceReader,
  rules: readonly TemporalRule[],
  now: number = Date.now()
): Promise<TemporalEvaluationResult> {
  const result: TemporalEvaluationResult = {
    allowed: true, decision: 'ALLOW', reasons: [], budgets: [], preconditionHashes: []
  }
  try {
    if (!Number.isSafeInteger(now)) throw new Error('Invalid evaluation time')
    for (const [index, rule] of rules.entries()) {
      validateTemporalRule(rule)
      if (rule.targetAction !== proposal.type) continue
      const id = rule.id ?? `${rule.type}:${index}`
      let records = (await history.queryTrace({
        action: rule.type === 'PRECEDING_EVENT_REQUIRED' ? rule.requiredPrecedingAction : rule.targetAction,
        actorId: (rule.scope ?? 'ACTOR') === 'ACTOR' ? proposal.metadata.actor : undefined,
        fromExclusive: now - rule.windowMs, toInclusive: now,
        includeReservations: rule.type !== 'PRECEDING_EVENT_REQUIRED'
      })).filter(record => record.proposalId !== proposal.id)
      if (rule.scope === 'RESOURCE') {
        const resource = readPath(proposal.payload, rule.resourcePath!)
        if (resource === undefined) throw new Error(`Missing resource scope ${rule.resourcePath}`)
        records = records.filter(record => {
          const historicalResource = readPath(record.intent.payload, rule.resourcePath!)
          if (historicalResource === undefined) throw new Error(`Historical record is missing resource scope ${rule.resourcePath}`)
          return sameValue(resource, historicalResource)
        })
      }
      if (rule.type === 'PRECEDING_EVENT_REQUIRED') {
        const source = records.filter(record => record.status === 'SUCCESS' &&
          (rule.matches ?? []).every(match => sameValue(readPath(proposal, match.currentPath), readPath(record, match.historicalPath))) &&
          (!rule.matchPredicate || rule.matchPredicate(proposal, record))
        ).sort((a, b) => b.timestamp - a.timestamp || a.hash.localeCompare(b.hash))[0]
        if (!source) result.reasons.push(`PRECEDING_EVENT_REQUIRED: ${id} requires successful ${rule.requiredPrecedingAction}`)
        else {
          result.preconditionHashes.push(source.hash)
          result.validBefore = Math.min(result.validBefore ?? Infinity, source.timestamp + rule.windowMs)
        }
      } else {
        const used = rule.type === 'RATE_LIMIT' ? records.length : records.reduce((sum, record) => sum + metric(record, rule.metricPath), 0)
        const requested = rule.type === 'RATE_LIMIT' ? 1 : metric({ intent: proposal }, rule.metricPath)
        const limit = rule.type === 'RATE_LIMIT' ? rule.maxCount : rule.maxCumulativeValue
        if (!Number.isFinite(used + requested)) throw new Error('Temporal aggregate overflow')
        result.budgets.push({ ruleId: id, action: rule.targetAction, windowMs: rule.windowMs, used, requested, limit, ...(rule.unit ? { unit: rule.unit } : {}) })
        if (used + requested > limit) result.reasons.push(`${rule.type}: ${id} would use ${used + requested} of ${limit}`)
      }
    }
  } catch (error) {
    result.reasons.push(`TEMPORAL_INVALID_ERROR: ${error instanceof Error ? error.message : String(error)}`)
  }
  result.preconditionHashes = [...new Set(result.preconditionHashes)]
  result.allowed = result.reasons.length === 0
  result.decision = result.allowed ? 'ALLOW' : 'REJECT'
  return result
}

export async function aggregateHistoricalMetric(
  history: TraceReader, actorId: string, action: string, metricPath: string, windowMs: number,
  now: number = Date.now()
): Promise<number> {
  if (!Number.isSafeInteger(windowMs) || windowMs <= 0) throw new Error('Invalid aggregation window')
  const records: IEECRecord[] = await history.queryTrace({ actorId, action, fromExclusive: now - windowMs, toInclusive: now })
  const sum = records.reduce((total, record) => total + metric(record, metricPath), 0)
  if (!Number.isFinite(sum)) throw new Error('Temporal aggregate overflow')
  return sum
}
