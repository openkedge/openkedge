import { startTransition, useEffect, useState } from 'react'

import { ReasoningPanel } from './components/ReasoningPanel'
import { SummaryCard } from './components/SummaryCard'
import { Timeline } from './components/Timeline'
import { useReplay } from './hooks/useReplay'
import { mockReplays } from './mockData'
import type { EvidenceEvent } from './types'

const demoIds = Object.keys(mockReplays)

function useIntentId(): [string, (nextId: string) => void] {
  const initialIntentId =
    new URLSearchParams(window.location.search).get('intentId') ?? demoIds[0]
  const [intentId, setIntentId] = useState(initialIntentId)

  const updateIntentId = (nextId: string) => {
    startTransition(() => {
      setIntentId(nextId)
      const url = new URL(window.location.href)
      url.searchParams.set('intentId', nextId)
      window.history.replaceState({}, '', url)
    })
  }

  return [intentId, updateIntentId]
}

export default function App() {
  const [intentId, setIntentId] = useIntentId()
  const { data, error, loading } = useReplay(intentId)
  const [selectedEvent, setSelectedEvent] = useState<EvidenceEvent | null>(null)
  const [playbackActive, setPlaybackActive] = useState(false)
  const [visibleCount, setVisibleCount] = useState(0)
  const displayReplay = data ?? mockReplays[intentId] ?? mockReplays['allowed-demo']

  useEffect(() => {
    if (!displayReplay) {
      return
    }

    setSelectedEvent(displayReplay.events[0] ?? null)
    setVisibleCount(displayReplay.events.length)
    setPlaybackActive(false)
  }, [displayReplay])

  useEffect(() => {
    if (!playbackActive || !displayReplay) {
      return
    }

    setVisibleCount(1)
    setSelectedEvent(displayReplay.events[0] ?? null)

    const interval = window.setInterval(() => {
      setVisibleCount((current) => {
        const next = current + 1
        const nextEvent =
          displayReplay.events[
            Math.min(next - 1, displayReplay.events.length - 1)
          ] ?? null
        setSelectedEvent(nextEvent)

        if (next >= displayReplay.events.length) {
          window.clearInterval(interval)
          setPlaybackActive(false)
        }

        return Math.min(next, displayReplay.events.length)
      })
    }, 650)

    return () => {
      window.clearInterval(interval)
    }
  }, [displayReplay, playbackActive])

  let selectedIntentLabel = intentId

  switch (intentId) {
    case 'allowed-demo':
      selectedIntentLabel = 'Allowed Case'
      break
    case 'blocked-demo':
      selectedIntentLabel = 'Blocked Case'
      break
    case 'critical-blast-demo':
      selectedIntentLabel = 'High-Risk Case'
      break
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(112,226,255,0.16),_transparent_30%),linear-gradient(140deg,_#040912_0%,_#08111f_45%,_#10233a_100%)] text-ink">
      <div className="mx-auto flex min-h-screen max-w-[1600px] flex-col gap-6 px-4 py-6 md:px-8 lg:px-10">
        <SummaryCard replay={displayReplay} />


        <section className="rounded-[28px] border border-white/10 bg-panel/60 p-5 backdrop-blur">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="text-xs uppercase tracking-[0.3em] text-accent/70">
                Scenario Selector
              </p>
              <h2 className="mt-2 text-xl font-semibold text-ink">
                {selectedIntentLabel}
              </h2>
            </div>
            <div className="flex flex-wrap gap-3">
              {demoIds.map((demoId) => (
                <button
                  key={demoId}
                  type="button"
                  onClick={() => setIntentId(demoId)}
                  className={`rounded-full px-4 py-2 text-sm font-medium transition ${
                    intentId === demoId
                      ? 'bg-accent text-slate-950'
                      : 'border border-white/10 bg-white/5 text-slate-200 hover:border-white/20'
                  }`}
                >
                  {demoId.replace(/-/g, ' ')}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setPlaybackActive((current) => !current)}
                className="rounded-full border border-amber-300/30 bg-amber-300/10 px-4 py-2 text-sm font-medium text-amber-100 transition hover:border-amber-200/60 hover:bg-amber-200/20"
              >
                {playbackActive ? 'Stop Playback' : 'Replay Animation'}
              </button>
              
              <button
                type="button"
                onClick={async () => {
                  try {
                    const id = crypto.randomUUID()
                    await fetch('http://localhost:3001/intent', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        id,
                        type: 'ec2:TerminateInstances',
                        payload: Array.from({ length: 25 }).map((_, i) => `i-${String(i + 6).padStart(7, '0')}`),
                        metadata: { actor: 'demo-ui', timestamp: Date.now() }
                      })
                    })
                    setIntentId(id)
                    setPlaybackActive(true)
                  } catch (e) {
                    console.error('Simulation run failed', e)
                  }
                }}
                className="rounded-full border border-rose-500/30 bg-rose-500/80 px-4 py-2 text-sm font-medium text-white transition hover:bg-rose-500"
              >
                Run Outage Simulation
              </button>
            </div>
          </div>
          {loading ? (
            <div className="mt-4 h-2 rounded-full bg-[length:200%_100%] bg-gradient-to-r from-transparent via-accent/70 to-transparent animate-shimmer" />
          ) : null}
          {error ? (
            <p className="mt-4 text-sm text-amber-200">
              Live replay fetch failed. Showing mock data when available. {error}
            </p>
          ) : null}
        </section>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_420px]">
          <Timeline
            events={displayReplay.events}
            selectedEventId={selectedEvent?.id ?? null}
            visibleCount={visibleCount}
            onSelect={(event) => {
              setPlaybackActive(false)
              setVisibleCount(displayReplay.events.length)
              setSelectedEvent(event)
            }}
          />
          <ReasoningPanel
            replay={displayReplay}
            selectedEvent={selectedEvent}
          />
        </div>
      </div>
    </main>
  )
}
