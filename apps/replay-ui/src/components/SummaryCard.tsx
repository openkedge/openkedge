import type { ReplayResult } from '../types'

interface SummaryCardProps {
  replay: ReplayResult
}

function statusTone(finalOutcome: ReplayResult['reconstructed']['finalOutcome']): string {
  switch (finalOutcome) {
    case 'allowed':
      return 'text-emerald-300'
    case 'blocked':
      return 'text-rose-300'
    case 'failed':
      return 'text-amber-300'
    default:
      return 'text-slate-300'
  }
}

export function SummaryCard({ replay }: SummaryCardProps) {
  const blastRadius = replay.reconstructed.blastRadius

  return (
    <section className="overflow-hidden rounded-[28px] border border-white/10 bg-panel/80 p-6 shadow-glow backdrop-blur">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-3">
          <p className="text-xs uppercase tracking-[0.35em] text-accent/70">
            OpenKedge Replay
          </p>
          <div className="space-y-2">
            <h1 className="text-3xl font-semibold tracking-tight text-ink md:text-5xl">
              Intent {replay.intentId}
            </h1>
            <p className="max-w-2xl text-sm text-slate-300 md:text-base">
              A traceable explanation of how OpenKedge processed the intent, sized
              its blast radius, and either permitted or stopped execution.
            </p>
          </div>
        </div>

        <div className="grid gap-3 text-sm text-slate-200 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
            <div className="text-xs uppercase tracking-[0.24em] text-slate-400">
              Final Outcome
            </div>
            <div className={`mt-2 text-xl font-semibold uppercase ${statusTone(replay.reconstructed.finalOutcome)}`}>
              {replay.reconstructed.finalOutcome}
            </div>
          </div>
          <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
            <div className="text-xs uppercase tracking-[0.24em] text-slate-400">
              Risk Level
            </div>
            <div className="mt-2 text-xl font-semibold text-gold">
              {blastRadius?.riskLevel ?? 'UNKNOWN'}
            </div>
          </div>
          <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
            <div className="text-xs uppercase tracking-[0.24em] text-slate-400">
              Resource Count
            </div>
            <div className="mt-2 text-xl font-semibold text-ink">
              {blastRadius?.resourceCount ?? 0}
            </div>
          </div>
          <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
            <div className="text-xs uppercase tracking-[0.24em] text-slate-400">
              Integrity
            </div>
            <div className={`mt-2 text-xl font-semibold ${replay.integrity.valid ? 'text-emerald-300' : 'text-rose-300'}`}>
              {replay.integrity.valid ? 'VALID' : 'TAMPERED'}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
