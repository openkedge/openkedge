import type { EvidenceEvent, ReplayStatusTheme } from '../types'

interface EventNodeProps {
  event: EvidenceEvent
  isActive: boolean
  isVisible: boolean
  onSelect: (event: EvidenceEvent) => void
  theme: ReplayStatusTheme
}

function formatTimestamp(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

function summarize(event: EvidenceEvent): string {
  if (event.payload.evaluationResult) {
    return event.payload.evaluationResult.allowed ? 'Allowed' : 'Blocked'
  }

  if (event.payload.executionResult) {
    return event.payload.executionResult.success ? 'Execution succeeded' : 'Execution failed'
  }

  if (event.payload.blastRadius) {
    return `${event.payload.blastRadius.riskLevel} risk`
  }

  return 'Evidence captured'
}

function stringify(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

export function EventNode({
  event,
  isActive,
  isVisible,
  onSelect,
  theme
}: EventNodeProps) {
  return (
    <button
      type="button"
      onClick={() => onSelect(event)}
      className={`group relative ml-6 w-full rounded-[24px] border p-5 text-left transition duration-300 ${
        isActive
          ? 'border-accent/60 bg-white/8 shadow-glow'
          : 'border-white/10 bg-panel/65 hover:border-white/20 hover:bg-white/8'
      } ${isVisible ? 'opacity-100 translate-y-0' : 'pointer-events-none opacity-35 translate-y-2'}`}
      style={{
        transitionProperty: 'opacity, transform, border-color, background-color, box-shadow'
      }}
    >
      <span className={`absolute -left-[2.2rem] top-8 h-4 w-4 rounded-full border-4 border-canvas ${theme.dot}`} />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <span className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.25em] ${theme.badge}`}>
              {event.type}
            </span>
            <span className="text-xs uppercase tracking-[0.22em] text-slate-400">
              Step {event.sequence}
            </span>
          </div>
          <p className="mt-3 text-lg font-semibold text-ink">{summarize(event)}</p>
          <p className="mt-2 text-sm text-slate-300">
            {event.payload.reasoningTrail?.[0] ?? 'No reasoning attached to this event.'}
          </p>
        </div>
        <div className="text-right text-sm text-slate-400">
          <div>{formatTimestamp(event.timestamp)}</div>
          <div className="mt-2 text-xs uppercase tracking-[0.22em] text-slate-500">
            Hover for raw payload
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className={`rounded-2xl border border-white/8 ${theme.panel} p-4`}>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-400">
            Blast Radius
          </div>
          <div className="mt-2 text-sm text-slate-200">
            {event.payload.blastRadius
              ? `${event.payload.blastRadius.riskLevel} on ${event.payload.blastRadius.resourceCount} resources`
              : 'Not evaluated on this step'}
          </div>
        </div>
        <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
          <div className="text-xs uppercase tracking-[0.22em] text-slate-400">
            Evaluation
          </div>
          <div className="mt-2 text-sm text-slate-200">
            {event.payload.evaluationResult
              ? event.payload.evaluationResult.allowed
                ? 'Allowed'
                : 'Blocked'
              : 'No decision on this step'}
          </div>
        </div>
        <div className="rounded-2xl border border-white/8 bg-white/5 p-4">
          <div className="text-xs uppercase tracking-[0.22em] text-slate-400">
            Execution
          </div>
          <div className="mt-2 text-sm text-slate-200">
            {event.payload.executionResult
              ? event.payload.executionResult.success
                ? 'Executed'
                : event.payload.executionResult.error ?? 'Failed'
              : 'Not executed on this step'}
          </div>
        </div>
      </div>

      <div className="mt-4 max-h-0 overflow-hidden rounded-2xl border border-transparent bg-[#07101c] transition-all duration-300 group-hover:max-h-[18rem] group-hover:border-white/10 group-hover:p-4">
        <pre className="overflow-auto text-xs text-slate-200">
          {stringify(event.payload)}
        </pre>
      </div>
    </button>
  )
}
