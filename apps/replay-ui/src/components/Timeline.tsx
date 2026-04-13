import type { EvidenceEvent, ReplayStatusTheme } from '../types'
import { EventNode } from './EventNode'

interface TimelineProps {
  events: EvidenceEvent[]
  selectedEventId: string | null
  visibleCount: number
  onSelect: (event: EvidenceEvent) => void
}

function themeForEvent(event: EvidenceEvent): ReplayStatusTheme {
  if (event.payload.evaluationResult && !event.payload.evaluationResult.allowed) {
    return {
      dot: 'bg-rose-400 shadow-[0_0_24px_rgba(251,113,133,0.9)]',
      badge: 'bg-rose-500/12 text-rose-200',
      panel: 'bg-rose-500/8'
    }
  }

  if (event.payload.blastRadius?.riskLevel === 'HIGH') {
    return {
      dot: 'bg-amber-300 shadow-[0_0_24px_rgba(251,191,36,0.9)]',
      badge: 'bg-amber-400/12 text-amber-200',
      panel: 'bg-amber-400/8'
    }
  }

  if (event.type === 'ExecutionSkipped' || event.type === 'ProcessingFailed') {
    return {
      dot: 'bg-rose-400 shadow-[0_0_24px_rgba(251,113,133,0.9)]',
      badge: 'bg-rose-500/12 text-rose-200',
      panel: 'bg-rose-500/8'
    }
  }

  return {
    dot: 'bg-accent shadow-[0_0_24px_rgba(112,226,255,0.9)]',
    badge: 'bg-cyan-400/12 text-cyan-100',
    panel: 'bg-cyan-400/8'
  }
}

export function Timeline({
  events,
  selectedEventId,
  visibleCount,
  onSelect
}: TimelineProps) {
  return (
    <section className="rounded-[28px] border border-white/10 bg-panel/70 p-6 backdrop-blur">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-accent/70">
            Timeline
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-ink">
            Distributed trace for a single intent
          </h2>
        </div>
        <div className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs uppercase tracking-[0.22em] text-slate-300">
          {events.length} events
        </div>
      </div>

      <div className="relative mt-8">
        <div className="absolute left-[1.35rem] top-2 h-[calc(100%-1rem)] w-px bg-gradient-to-b from-accent/50 via-white/10 to-transparent" />
        <div className="space-y-5">
          {events.map((event, index) => (
            <div
              key={event.id}
              className="animate-rise-in"
              style={{ animationDelay: `${index * 110}ms` }}
            >
              <EventNode
                event={event}
                isActive={event.id === selectedEventId}
                isVisible={index < visibleCount}
                onSelect={onSelect}
                theme={themeForEvent(event)}
              />
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
