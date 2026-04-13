import type { EvidenceEvent, ReplayResult } from '../types'

interface ReasoningPanelProps {
  replay: ReplayResult
  selectedEvent: EvidenceEvent | null
}

function formatTimestamp(timestamp: number): string {
  return new Date(timestamp).toLocaleString()
}

function stringify(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

export function ReasoningPanel({
  replay,
  selectedEvent
}: ReasoningPanelProps) {
  const event = selectedEvent ?? replay.events[0] ?? null

  return (
    <aside className="grid gap-4">
      <section className="rounded-[24px] border border-white/10 bg-panel/80 p-5 shadow-glow backdrop-blur">
        <p className="text-xs uppercase tracking-[0.3em] text-accent/70">
          Reasoning Trail
        </p>
        <ul className="mt-4 space-y-3 text-sm text-slate-200">
          {replay.reasoningTrail.map((line, index) => (
            <li key={`${line}-${index}`} className="rounded-2xl border border-white/8 bg-white/5 px-4 py-3">
              {line}
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-[24px] border border-white/10 bg-panel/80 p-5 shadow-glow backdrop-blur">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-[0.3em] text-accent/70">
              Selected Event
            </p>
            <h2 className="mt-2 text-xl font-semibold text-ink">
              {event?.type ?? 'No event selected'}
            </h2>
          </div>
          {event ? (
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300">
              {formatTimestamp(event.timestamp)}
            </span>
          ) : null}
        </div>

        {event ? (
          <div className="mt-4 space-y-4">
            <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
              <div className="text-xs uppercase tracking-[0.24em] text-slate-400">
                Raw Payload
              </div>
              <pre className="mt-3 max-h-[24rem] overflow-auto rounded-2xl bg-[#07101c] p-4 text-xs text-slate-200">
                {stringify(event.payload)}
              </pre>
            </div>
            <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
              <div className="text-xs uppercase tracking-[0.24em] text-slate-400">
                Reasoning
              </div>
              <ul className="mt-3 space-y-2 text-sm text-slate-200">
                {(event.payload.reasoningTrail ?? []).map((line, index) => (
                  <li key={`${event.id}-${index}`}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
      </section>
    </aside>
  )
}
