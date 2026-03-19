import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { runsApi, plansApi } from '../api/endpoints'
import { getStoredToken } from '../api/client'
import type { LoadRun, JobRecord, LoadPlanDetail } from '../api/types'

// ─── Constants ────────────────────────────────────────────────────────────────

export const TERMINAL_STATUSES: LoadRun['status'][] = [
  'completed',
  'completed_with_errors',
  'failed',
  'aborted',
]

/** Fallback poll interval when WS is healthy — catches any missed events */
export const POLL_INTERVAL_MS = 15_000
/** Poll interval when WS is unavailable — sole update mechanism */
const POLL_INTERVAL_NO_WS = 3_000

// ─── Types ────────────────────────────────────────────────────────────────────

export interface UseLiveRunResult {
  run: LoadRun | undefined
  jobs: JobRecord[]
  planDetail: LoadPlanDetail | undefined
  isLoading: boolean
  isError: boolean
  error: Error | null
  /** True while the run is in a non-terminal state */
  isLive: boolean
  /** True while the WebSocket connection is open */
  isWsConnected: boolean
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * Provides live run data via a WebSocket + REST hybrid strategy.
 *
 * - Opens a WS connection on mount for non-terminal runs; WS events invalidate
 *   React Query caches so REST fetches immediately pick up changes.
 * - Falls back to polling every 3 s if WS is unavailable; slows to 15 s when WS
 *   is connected (catches any missed events).
 * - Closes the WS and stops polling once the run reaches a terminal state.
 */
export function useLiveRun(runId: string): UseLiveRunResult {
  const queryClient = useQueryClient()
  const [isWsConnected, setIsWsConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const runQuery = useQuery({
    queryKey: ['runs', runId],
    queryFn: () => runsApi.get(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status || TERMINAL_STATUSES.includes(status)) return false
      return isWsConnected ? POLL_INTERVAL_MS : POLL_INTERVAL_NO_WS
    },
  })

  const run = runQuery.data
  const isLive = !!run && !TERMINAL_STATUSES.includes(run.status)

  const jobsQuery = useQuery({
    queryKey: ['runs', runId, 'jobs'],
    queryFn: () => runsApi.jobs(runId),
    enabled: !!run,
    refetchInterval: isLive
      ? isWsConnected
        ? POLL_INTERVAL_MS
        : POLL_INTERVAL_NO_WS
      : false,
  })

  const planDetailQuery = useQuery({
    queryKey: ['plans', run?.load_plan_id],
    queryFn: () => plansApi.get(run!.load_plan_id),
    enabled: !!run?.load_plan_id,
  })

  // ── WebSocket connection ────────────────────────────────────────────────────
  useEffect(() => {
    if (!runId || !isLive) return

    const token = getStoredToken()
    if (!token) return // no auth token — polling-only mode

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const wsUrl = `${protocol}://${window.location.host}/ws/runs/${runId}?token=${encodeURIComponent(token)}`

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setIsWsConnected(true)
    }

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>
        // Respond to server keepalive pings
        if (data.type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }))
          return
        }
        // Any non-keepalive event → invalidate caches for immediate re-fetch
        if (data.type !== 'pong') {
          void queryClient.invalidateQueries({ queryKey: ['runs', runId] })
          void queryClient.invalidateQueries({ queryKey: ['runs', runId, 'jobs'] })
        }
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      setIsWsConnected(false)
      wsRef.current = null
    }

    ws.onerror = () => {
      setIsWsConnected(false)
    }

    return () => {
      ws.close()
      wsRef.current = null
      setIsWsConnected(false)
    }
  }, [runId, isLive, queryClient])

  return {
    run,
    jobs: jobsQuery.data ?? [],
    planDetail: planDetailQuery.data,
    isLoading: runQuery.isPending,
    isError: runQuery.isError,
    error: runQuery.error as Error | null,
    isLive,
    isWsConnected,
  }
}
