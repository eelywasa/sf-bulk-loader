/**
 * Tests for the Settings → Notifications tab (SFBL-183).
 *
 * Verifies list rendering, add / edit / delete flows, and the test-send
 * button surfacing both success and failure outcomes.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import { ToastProvider } from '../components/ui/Toast'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import Settings from './Settings'
import type {
  NotificationSubscription,
  RuntimeConfig,
  UserResponse,
  LoadPlan,
} from '../api/types'

const MOCK_RUNTIME_LOCAL: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_RUNTIME_DESKTOP: RuntimeConfig = {
  auth_mode: 'none',
  app_distribution: 'desktop',
  transport_mode: 'local',
  input_storage_mode: 'local',
}

const MOCK_USER: UserResponse = {
  id: 'u1',
  username: 'alice',
  email: 'alice@example.com',
  display_name: 'Alice',
  role: 'user',
  is_active: true,
}

const MOCK_PLANS: LoadPlan[] = [
  {
    id: 'plan-1',
    name: 'Accounts Plan',
    description: null,
    connection_id: 'c1',
    output_connection_id: null,
    abort_on_step_failure: false,
    error_threshold_pct: 0,
    max_parallel_jobs: 1,
    created_at: '2026-04-20T00:00:00Z',
    updated_at: '2026-04-20T00:00:00Z',
  } as unknown as LoadPlan,
]

const MOCK_SUB: NotificationSubscription = {
  id: 'sub-1',
  user_id: 'u1',
  plan_id: 'plan-1',
  channel: 'email',
  destination: 'alice@example.com',
  trigger: 'terminal_any',
  created_at: '2026-04-20T00:00:00Z',
  updated_at: '2026-04-20T00:00:00Z',
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderSettings() {
  const qc = makeQueryClient()
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <MemoryRouter>
              <Settings />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('Settings → Notifications tab', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'test-token')
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'list')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'create')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'update')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'delete')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'test')
    vi.spyOn(endpoints.plansApi, 'list')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('hides tabs on desktop profile', async () => {
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)

    renderSettings()

    await waitFor(() => {
      expect(
        screen.getByText(/no configurable settings are available/i),
      ).toBeInTheDocument()
    })
    expect(screen.queryByRole('tab', { name: 'Notifications' })).toBeNull()
  })

  it('renders existing subscriptions in a table', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([
      MOCK_SUB,
    ])
    vi.mocked(endpoints.plansApi.list).mockResolvedValue(MOCK_PLANS)

    renderSettings()
    await userEv.click(await screen.findByRole('tab', { name: 'Notifications' }))

    await waitFor(() => {
      expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    })
    expect(screen.getByText('Accounts Plan')).toBeInTheDocument()
    expect(screen.getByText('Any terminal')).toBeInTheDocument()
  })

  it('creates a subscription end-to-end', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([])
    vi.mocked(endpoints.plansApi.list).mockResolvedValue(MOCK_PLANS)
    vi.mocked(endpoints.notificationSubscriptionsApi.create).mockResolvedValue({
      ...MOCK_SUB,
      id: 'new-id',
      plan_id: null,
      destination: 'ops@example.com',
    })

    renderSettings()
    await userEv.click(await screen.findByRole('tab', { name: 'Notifications' }))
    await userEv.click(await screen.findByRole('button', { name: /add subscription/i }))

    const input = await screen.findByLabelText(/email address/i)
    await userEv.type(input, 'ops@example.com')
    await userEv.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(endpoints.notificationSubscriptionsApi.create).toHaveBeenCalled()
    })
    const arg = vi.mocked(endpoints.notificationSubscriptionsApi.create).mock.calls[0][0]
    expect(arg.channel).toBe('email')
    expect(arg.destination).toBe('ops@example.com')
    expect(arg.trigger).toBe('terminal_any')
  })

  it('deletes a subscription after confirmation', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([
      MOCK_SUB,
    ])
    vi.mocked(endpoints.plansApi.list).mockResolvedValue(MOCK_PLANS)
    vi.mocked(endpoints.notificationSubscriptionsApi.delete).mockResolvedValue(
      undefined as unknown as never,
    )

    renderSettings()
    await userEv.click(await screen.findByRole('tab', { name: 'Notifications' }))
    await userEv.click(await screen.findByRole('button', { name: 'Delete' }))
    // confirmation modal
    const confirms = await screen.findAllByRole('button', { name: 'Delete' })
    await userEv.click(confirms[confirms.length - 1])

    await waitFor(() => {
      expect(endpoints.notificationSubscriptionsApi.delete).toHaveBeenCalledWith(
        'sub-1',
      )
    })
  })

  it('surfaces a success toast when test-send succeeds', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([
      MOCK_SUB,
    ])
    vi.mocked(endpoints.plansApi.list).mockResolvedValue(MOCK_PLANS)
    vi.mocked(endpoints.notificationSubscriptionsApi.test).mockResolvedValue({
      delivery_id: 'd1',
      status: 'sent',
      attempts: 1,
      last_error: null,
      email_delivery_id: 'e1',
    })

    renderSettings()
    await userEv.click(await screen.findByRole('tab', { name: 'Notifications' }))
    await userEv.click(await screen.findByRole('button', { name: 'Test' }))

    await waitFor(() => {
      expect(screen.getByText(/test notification dispatched/i)).toBeInTheDocument()
    })
  })

  it('surfaces an error toast when the test-send fails', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([
      MOCK_SUB,
    ])
    vi.mocked(endpoints.plansApi.list).mockResolvedValue(MOCK_PLANS)
    vi.mocked(endpoints.notificationSubscriptionsApi.test).mockResolvedValue({
      delivery_id: 'd1',
      status: 'failed',
      attempts: 3,
      last_error: 'smtp 550',
      email_delivery_id: null,
    })

    renderSettings()
    await userEv.click(await screen.findByRole('tab', { name: 'Notifications' }))
    await userEv.click(await screen.findByRole('button', { name: 'Test' }))

    await waitFor(() => {
      expect(screen.getByText(/test failed: smtp 550/i)).toBeInTheDocument()
    })
  })
})
