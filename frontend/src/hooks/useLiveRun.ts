import { useQuery } from '@tanstack/react-query'
import { runsApi, plansApi } from '../api/endpoints'
import type { LoadRun, JobRecord, LoadPlanDetail } from '../api/types'

// ─── Constants ────────────────────────────────────────────────────────────────

export const TERMINAL_STATUSES: LoadRun['status'][] = [
  'completed',
  'completed_with_errors',
  'failed',
  'aborted',
]

export const POLL_INTERVAL_MS = 3_000

// ─── Types ────────────────────────────────────────────────────────────────────

export interface UseLiveRunResult {
  run: LoadRun | undefined
  jobs: JobRecord[]
  planDetail: LoadPlanDetail | undefined
  isLoading: boolean
  isError: boolean
  error: Error | null
  /** True while the run is in a non-terminal state and actively polling */
  isLive: boolean
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * Polls run status and jobs every 3 s while the run is pending or running.
 * Stops polling once the run reaches a terminal status.
 */
export function useLiveRun(runId: string): UseLiveRunResult {
  const runQuery = useQuery({
    queryKey: ['runs', runId],
    queryFn: () => runsApi.get(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status || TERMINAL_STATUSES.includes(status)) return false
      return POLL_INTERVAL_MS
    },
  })

  const run = runQuery.data
  const isLive = !!run && !TERMINAL_STATUSES.includes(run.status)

  const jobsQuery = useQuery({
    queryKey: ['runs', runId, 'jobs'],
    queryFn: () => runsApi.jobs(runId),
    enabled: !!run,
    refetchInterval: isLive ? POLL_INTERVAL_MS : false,
  })

  const planDetailQuery = useQuery({
    queryKey: ['plans', run?.load_plan_id],
    queryFn: () => plansApi.get(run!.load_plan_id),
    enabled: !!run?.load_plan_id,
  })

  return {
    run,
    jobs: jobsQuery.data ?? [],
    planDetail: planDetailQuery.data,
    isLoading: runQuery.isPending,
    isError: runQuery.isError,
    error: runQuery.error as Error | null,
    isLive,
  }
}
