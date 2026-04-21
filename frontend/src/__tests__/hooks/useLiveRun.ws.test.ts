/**
 * WebSocket lifecycle tests for useLiveRun hook.
 *
 * Strategy:
 *  - Install a MockWebSocket via vi.stubGlobal so we can inspect/drive WS events.
 *  - Mock getStoredToken via vi.mock so the hook always has a token.
 *  - Mock endpoints via vi.mock so API calls resolve synchronously.
 *  - Create a shared queryClient per test so we can spy on invalidateQueries.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useLiveRun, POLL_INTERVAL_MS } from '../../hooks/useLiveRun'
import type { LoadRun, JobRecord, LoadPlanDetail } from '../../api/types'

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  runsApi: { get: vi.fn(), jobs: vi.fn() },
  plansApi: { get: vi.fn() },
}))

vi.mock('../../api/client', () => ({
  getStoredToken: vi.fn(() => 'test-token'),
}))

vi.mock('../../context/AuthContext', () => ({
  useAuth: vi.fn(() => ({ authRequired: true, permissions: new Set() })),
  useAuthOptional: vi.fn(() => ({ authRequired: true, permissions: new Set() })),
}))

import { runsApi, plansApi } from '../../api/endpoints'
import { getStoredToken } from '../../api/client'

// ─── MockWebSocket ────────────────────────────────────────────────────────────

class MockWebSocket {
  static lastInstance: MockWebSocket | null = null
  static callCount = 0

  url: string
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  send = vi.fn()
  close = vi.fn()

  constructor(url: string) {
    this.url = url
    MockWebSocket.lastInstance = this
    MockWebSocket.callCount++
  }

  static reset() {
    MockWebSocket.lastInstance = null
    MockWebSocket.callCount = 0
  }
}

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

const runRunning: LoadRun = {
  ...runPending,
  status: 'running',
  started_at: '2024-03-01T10:00:00Z',
}

const runCompleted: LoadRun = {
  ...runPending,
  status: 'completed',
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:05:00Z',
  total_records: 100,
  total_success: 100,
  total_errors: 0,
}

const emptyJobs: JobRecord[] = []

const planDetail: LoadPlanDetail = {
  id: 'plan-1',
  connection_id: 'conn-1',
  name: 'Test Plan',
  description: null,
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  output_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
  load_steps: [],
}

// ─── Wrapper factory ──────────────────────────────────────────────────────────

let testQueryClient: QueryClient

function makeWrapper() {
  testQueryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  })
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: testQueryClient }, children)
}

// ─── Setup / teardown ─────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  MockWebSocket.reset()
  vi.mocked(getStoredToken).mockReturnValue('test-token')
  vi.mocked(runsApi.jobs).mockResolvedValue(emptyJobs)
  vi.mocked(plansApi.get).mockResolvedValue(planDetail)
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

// ─── Helper: wait for WS to be constructed ───────────────────────────────────

async function waitForWs(): Promise<MockWebSocket> {
  await waitFor(() => expect(MockWebSocket.lastInstance).toBeTruthy())
  return MockWebSocket.lastInstance!
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('useLiveRun WebSocket lifecycle', () => {
  it('opens WS for a pending run with correct URL', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runPending)

    renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    expect(ws.url).toContain('/ws/runs/run-1')
    expect(ws.url).toContain('token=test-token')
    expect(ws.url).toMatch(/^ws:\/\//)
  })

  it('opens WS for a running run', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    await waitForWs()

    expect(MockWebSocket.callCount).toBe(1)
  })

  it('does not open WS for a terminal run', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.run).toBeDefined())

    // isLive is false for terminal runs → effect returns early → no WS
    expect(MockWebSocket.callCount).toBe(0)
  })

  it('sets isWsConnected=true after onopen fires', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    await act(async () => {
      ws.onopen?.(new Event('open'))
    })

    expect(result.current.isWsConnected).toBe(true)
  })

  it('sets isWsConnected=false after onclose fires', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    await act(async () => {
      ws.onopen?.(new Event('open'))
    })
    expect(result.current.isWsConnected).toBe(true)

    await act(async () => {
      ws.onclose?.(new CloseEvent('close'))
    })

    expect(result.current.isWsConnected).toBe(false)
  })

  it('sets isWsConnected=false on onerror and does not throw', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    await act(async () => {
      ws.onopen?.(new Event('open'))
    })

    await act(async () => {
      ws.onerror?.(new Event('error'))
    })

    expect(result.current.isWsConnected).toBe(false)
  })

  it('invalidates run and jobs queries on non-keepalive message', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    const invalidateSpy = vi.spyOn(testQueryClient, 'invalidateQueries')

    await act(async () => {
      ws.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({ event: 'job_status_change' }),
        })
      )
    })

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['runs', 'run-1'] })
    )
    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['runs', 'run-1', 'jobs'] })
    )
  })

  it('responds to ping with pong and skips cache invalidation', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    const invalidateSpy = vi.spyOn(testQueryClient, 'invalidateQueries')

    await act(async () => {
      ws.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({ type: 'ping' }),
        })
      )
    })

    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'pong' }))
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('skips cache invalidation for pong messages', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    const invalidateSpy = vi.spyOn(testQueryClient, 'invalidateQueries')

    await act(async () => {
      ws.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({ type: 'pong' }),
        })
      )
    })

    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('does not throw on malformed JSON messages', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    await act(async () => {
      ws.onmessage?.(new MessageEvent('message', { data: 'not-valid-json' }))
    })

    // Hook should still be rendering without error
    expect(result.current.isWsConnected).toBe(false)
  })

  it('closes WS when run transitions from running to terminal', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    await act(async () => {
      ws.onopen?.(new Event('open'))
    })
    expect(result.current.isWsConnected).toBe(true)

    // Simulate run completing via cache update
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    await act(async () => {
      await testQueryClient.invalidateQueries({ queryKey: ['runs', 'run-1'] })
    })

    await waitFor(() => expect(result.current.isLive).toBe(false))

    // Effect cleanup should have called ws.close()
    expect(ws.close).toHaveBeenCalled()
    expect(result.current.isWsConnected).toBe(false)
  })

  it('uses wss:// for https pages', async () => {
    // Override window.location.protocol to https:
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, protocol: 'https:', host: 'localhost' },
    })

    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    const ws = await waitForWs()

    expect(ws.url).toMatch(/^wss:\/\//)

    // Restore
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    })
  })

  it('does not open WS when getStoredToken returns null', async () => {
    vi.mocked(getStoredToken).mockReturnValue(null)
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.run).toBeDefined())

    // Effect returns early when token is null
    expect(MockWebSocket.callCount).toBe(0)
  })

  it('uses 3s polling without WS and 15s polling with WS', async () => {
    // Set up fake timers from the start so React Query's poll timers are fake-controlled.
    vi.useFakeTimers()

    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue(emptyJobs)
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    const { result } = renderHook(() => useLiveRun('run-1'), { wrapper: makeWrapper() })

    // React Query may schedule its initial fetch via a 0ms timer; advance a little
    // and flush promises so the initial data lands.
    await act(() => vi.advanceTimersByTimeAsync(1))
    await act(async () => {})
    expect(result.current.run?.status).toBe('running')

    // React Query schedules the next poll at 3000ms (fake timer now).
    const callsAfterInit = vi.mocked(runsApi.get).mock.calls.length

    // Part A — no WS: advance 3 s → poll fires
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    await act(async () => {})  // flush the re-fetch promise
    expect(vi.mocked(runsApi.get).mock.calls.length).toBeGreaterThan(callsAfterInit)

    // WS should now exist (created when isLive became true after initial fetch)
    expect(MockWebSocket.lastInstance).toBeTruthy()
    const ws = MockWebSocket.lastInstance!

    // Part B — connect WS: React Query switches to 15 s interval
    await act(async () => { ws.onopen?.(new Event('open')) })
    expect(result.current.isWsConnected).toBe(true)

    const callsAfterWsOpen = vi.mocked(runsApi.get).mock.calls.length

    // 3 s with WS connected — should NOT re-fetch (interval is 15 s)
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    await act(async () => {})
    expect(vi.mocked(runsApi.get).mock.calls.length).toBe(callsAfterWsOpen)

    // Advance to 15 s total → re-fetch fires
    await act(() => vi.advanceTimersByTimeAsync(12_000))
    await act(async () => {})
    expect(vi.mocked(runsApi.get).mock.calls.length).toBeGreaterThan(callsAfterWsOpen)
  })
})
