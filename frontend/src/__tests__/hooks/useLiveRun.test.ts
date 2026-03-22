import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useLiveRun, TERMINAL_STATUSES, POLL_INTERVAL_MS } from '../../hooks/useLiveRun'
import type { LoadRun, JobRecord, LoadPlanDetail } from '../../api/types'

// ─── Mock endpoints ────────────────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  runsApi: {
    get: vi.fn(),
    jobs: vi.fn(),
  },
  plansApi: {
    get: vi.fn(),
  },
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: vi.fn(() => ({ authRequired: true })),
}))

import { runsApi, plansApi } from '../../api/endpoints'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const runPending: LoadRun = {
  id: 'run-1',
  load_plan_id: 'plan-1',
  status: 'pending',
  started_at: null,
  completed_at: null,
  total_records: null,
  total_success: null,
  total_errors: null,
  initiated_by: null,
  error_summary: null,
  is_retry: false,
}

const runRunning: LoadRun = { ...runPending, status: 'running', started_at: '2024-03-01T10:00:00Z' }

const runCompleted: LoadRun = {
  ...runPending,
  status: 'completed',
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:05:00Z',
  total_records: 1000,
  total_success: 1000,
  total_errors: 0,
}

const runFailed: LoadRun = { ...runPending, status: 'failed' }
const runAborted: LoadRun = { ...runPending, status: 'aborted' }

const job1: JobRecord = {
  id: 'job-1',
  load_run_id: 'run-1',
  load_step_id: 'step-1',
  sf_job_id: 'sf-123',
  partition_index: 0,
  status: 'job_complete',
  records_processed: 500,
  records_failed: 0,
  success_file_path: null,
  error_file_path: null,
  unprocessed_file_path: null,
  sf_api_response: null,
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:05:00Z',
  error_message: null,
}

const planDetail: LoadPlanDetail = {
  id: 'plan-1',
  connection_id: 'conn-1',
  name: 'Test Plan',
  description: null,
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
  load_steps: [],
}

// ─── Wrapper ──────────────────────────────────────────────────────────────────

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  })
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children)
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('useLiveRun', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('constants', () => {
    it('TERMINAL_STATUSES includes completed, completed_with_errors, failed, aborted', () => {
      expect(TERMINAL_STATUSES).toContain('completed')
      expect(TERMINAL_STATUSES).toContain('completed_with_errors')
      expect(TERMINAL_STATUSES).toContain('failed')
      expect(TERMINAL_STATUSES).toContain('aborted')
    })

    it('TERMINAL_STATUSES does not include pending or running', () => {
      expect(TERMINAL_STATUSES).not.toContain('pending')
      expect(TERMINAL_STATUSES).not.toContain('running')
    })

    it('POLL_INTERVAL_MS is 15000 (WS fallback interval)', () => {
      expect(POLL_INTERVAL_MS).toBe(15_000)
    })
  })

  describe('loading state', () => {
    it('returns isLoading=true while run query is pending', () => {
      vi.mocked(runsApi.get).mockReturnValue(new Promise(() => {}))
      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      expect(result.current.isLoading).toBe(true)
      expect(result.current.run).toBeUndefined()
      expect(result.current.jobs).toEqual([])
    })
  })

  describe('error state', () => {
    it('returns isError=true when run query fails', async () => {
      vi.mocked(runsApi.get).mockRejectedValue(new Error('Not found'))
      const { result } = renderHook(() => useLiveRun('run-99'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.isError).toBe(true))
      expect(result.current.run).toBeUndefined()
    })
  })

  describe('successful load', () => {
    it('returns the run data after fetch', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
      vi.mocked(runsApi.jobs).mockResolvedValue([job1])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.run).toBeDefined())

      expect(result.current.run?.id).toBe('run-1')
      expect(result.current.run?.status).toBe('completed')
    })

    it('returns jobs after they are fetched', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
      vi.mocked(runsApi.jobs).mockResolvedValue([job1])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.jobs.length).toBeGreaterThan(0))

      expect(result.current.jobs[0].id).toBe('job-1')
    })

    it('returns planDetail after it is fetched', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
      vi.mocked(runsApi.jobs).mockResolvedValue([job1])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.planDetail).toBeDefined())

      expect(result.current.planDetail?.name).toBe('Test Plan')
    })
  })

  describe('isLive flag', () => {
    it('is true when run status is pending', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runPending)
      vi.mocked(runsApi.jobs).mockResolvedValue([])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.run).toBeDefined())

      expect(result.current.isLive).toBe(true)
    })

    it('is true when run status is running', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runRunning)
      vi.mocked(runsApi.jobs).mockResolvedValue([])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.run).toBeDefined())

      expect(result.current.isLive).toBe(true)
    })

    it('is false when run status is completed', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
      vi.mocked(runsApi.jobs).mockResolvedValue([job1])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.run).toBeDefined())

      expect(result.current.isLive).toBe(false)
    })

    it('is false when run status is failed', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runFailed)
      vi.mocked(runsApi.jobs).mockResolvedValue([])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.run).toBeDefined())

      expect(result.current.isLive).toBe(false)
    })

    it('is false when run status is aborted', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runAborted)
      vi.mocked(runsApi.jobs).mockResolvedValue([])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
      await waitFor(() => expect(result.current.run).toBeDefined())

      expect(result.current.isLive).toBe(false)
    })
  })

  describe('jobs query dependency', () => {
    it('does not call runsApi.jobs until run data is available', () => {
      vi.mocked(runsApi.get).mockReturnValue(new Promise(() => {}))

      renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })

      expect(runsApi.jobs).not.toHaveBeenCalled()
    })

    it('calls runsApi.jobs once run data arrives', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
      vi.mocked(runsApi.jobs).mockResolvedValue([])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })

      await waitFor(() => expect(runsApi.jobs).toHaveBeenCalledWith('run-1'))
    })
  })

  describe('planDetail query dependency', () => {
    it('calls plansApi.get with the load_plan_id from the run', async () => {
      vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
      vi.mocked(runsApi.jobs).mockResolvedValue([])
      vi.mocked(plansApi.get).mockResolvedValue(planDetail)

      renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })

      await waitFor(() => expect(plansApi.get).toHaveBeenCalledWith('plan-1'))
    })

    it('does not call plansApi.get until run data arrives', () => {
      vi.mocked(runsApi.get).mockReturnValue(new Promise(() => {}))

      renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })

      expect(plansApi.get).not.toHaveBeenCalled()
    })
  })
})
