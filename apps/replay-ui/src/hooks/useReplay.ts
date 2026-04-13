import { startTransition, useEffect, useState } from 'react'

import { mockReplays } from '../mockData'
import type { ReplayResult } from '../types'

interface UseReplayState {
  data: ReplayResult | null
  error: string | null
  loading: boolean
}

const DEFAULT_API_BASE = import.meta.env.VITE_REPLAY_API_BASE_URL ?? ''

export function useReplay(intentId: string): UseReplayState {
  const [state, setState] = useState<UseReplayState>({
    data: null,
    error: null,
    loading: true
  })

  useEffect(() => {
    let cancelled = false

    async function loadReplay(): Promise<void> {
      setState((current) => ({
        ...current,
        loading: true,
        error: null
      }))

      try {
        const response = await fetch(`${DEFAULT_API_BASE}/replay/${intentId}`)

        if (!response.ok) {
          throw new Error(`Replay endpoint returned ${response.status}`)
        }

        const payload = (await response.json()) as ReplayResult

        if (cancelled) {
          return
        }

        startTransition(() => {
          setState({
            data: payload,
            error: null,
            loading: false
          })
        })
      } catch (error) {
        const fallback = mockReplays[intentId]

        if (cancelled) {
          return
        }

        startTransition(() => {
          setState({
            data: fallback ?? null,
            error:
              fallback === undefined
                ? error instanceof Error
                  ? error.message
                  : 'Unable to load replay'
                : null,
            loading: false
          })
        })
      }
    }

    void loadReplay()

    return () => {
      cancelled = true
    }
  }, [intentId])

  return state
}
