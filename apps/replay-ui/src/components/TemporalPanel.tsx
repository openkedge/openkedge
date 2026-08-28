import type { ReplayResult } from '../types'

function windowLabel(ms: number): string {
  if (ms % 3_600_000 === 0) return `${ms / 3_600_000}h`
  if (ms % 60_000 === 0) return `${ms / 60_000}m`
  return `${ms / 1_000}s`
}

export function TemporalPanel({ replay }: { replay: ReplayResult }) {
  const temporal = replay.reconstructed.temporalEvaluation
  const contract = replay.reconstructed.executionContract
  if (!temporal?.budgets.length && !contract) return null
  return (
    <section className="rounded-[24px] border border-cyan-300/20 bg-panel/80 p-5" aria-label="Temporal invariants">
      <h2 className="text-xs uppercase tracking-[0.3em] text-accent">Temporal invariants</h2>
      <p className="mt-2 text-xs text-slate-400">Snapshot at evaluation time; includes unresolved reservations.</p>
      {temporal?.budgets.map(budget => {
        const exceeded = budget.used + budget.requested > budget.limit
        const amount = (value: number) => budget.unit === 'USD' ? `$${value.toLocaleString()}` : `${value.toLocaleString()}${budget.unit ? ` ${budget.unit}` : ''}`
        const percent = budget.limit > 0 ? Math.min(100, 100 * budget.used / budget.limit) : 0
        return (
          <div key={budget.ruleId} className="mt-5">
            <div className="flex flex-wrap justify-between gap-2 text-sm">
              <span className="text-slate-200">{budget.action} · {windowLabel(budget.windowMs)} window</span>
              <strong className="text-ink">{amount(budget.used)} / {amount(budget.limit)} used</strong>
            </div>
            <div className="mt-3 h-3 overflow-hidden rounded-full bg-white/10" role="meter" aria-label={budget.ruleId}
              aria-valuemin={0} aria-valuemax={budget.limit} aria-valuenow={Math.min(budget.used, budget.limit)}
              aria-valuetext={`${amount(budget.used)} of ${amount(budget.limit)} used`}>
              <div className={`h-full rounded-full ${exceeded ? 'bg-rose-400' : 'bg-cyan-300'}`} style={{ width: `${percent}%` }} />
            </div>
            <p className={`mt-2 text-xs ${exceeded ? 'text-rose-200' : 'text-slate-300'}`}>
              Requested +{amount(budget.requested)} · {exceeded ? 'Would exceed the limit' : 'Within budget'}
            </p>
          </div>
        )
      })}
      {contract ? (
        <dl className="mt-5 space-y-2 border-t border-white/10 pt-4 text-xs text-slate-300">
          <div><dt className="inline text-slate-500">Activates: </dt><dd className="inline">{new Date(contract.temporalBounds.notBefore).toLocaleString()}</dd></div>
          <div><dt className="inline text-slate-500">Cutoff (exclusive): </dt><dd className="inline">{new Date(contract.temporalBounds.notAfter).toLocaleString()}</dd></div>
          <div><dt className="inline text-slate-500">Max duration: </dt><dd className="inline">{contract.temporalBounds.maxDurationMs}ms</dd></div>
          <div><dt className="inline text-slate-500">Required evidence: </dt><dd className="inline">{contract.preconditionHashes.length} SHA-256 hashes</dd></div>
        </dl>
      ) : null}
    </section>
  )
}
