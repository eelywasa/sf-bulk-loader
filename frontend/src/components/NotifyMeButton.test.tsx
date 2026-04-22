/**
 * Tests for NotifyMeButton (SFBL-183).
 *
 * Verifies the split-button correctly reflects existing-subscription state
 * and that the quick-subscribe and unsubscribe actions call the right
 * endpoints.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import { ToastProvider } from './ui/Toast'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import { NotifyMeButton } from './NotifyMeButton'
import type {
  NotificationSubscription,
  RuntimeConfig,
  UserResponse,
} from '../api/types'

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_USER: UserResponse = {
  id: 'u1',
  email: 'alice@example.com',
  display_name: 'Alice',
  profile: { name: 'operator' },
  permissions: ['plans.view', 'runs.view', 'files.view', 'connections.view'],
}

const MOCK_EXISTING: NotificationSubscription = {
  id: 'sub-1',
  user_id: 'u1',
  plan_id: 'plan-1',
  channel: 'email',
  destination: 'alice@example.com',
  trigger: 'terminal_any',
  created_at: '2026-04-20T00:00:00Z',
  updated_at: '2026-04-20T00:00:00Z',
}

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderButton(planId: string) {
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={makeClient()}>
          <ToastProvider>
            <NotifyMeButton planId={planId} />
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('NotifyMeButton', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'test-token')
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'list')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'create')
    vi.spyOn(endpoints.notificationSubscriptionsApi, 'delete')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('shows "Notify me" when no subscription exists and quick-subscribes on click', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([])
    vi.mocked(endpoints.notificationSubscriptionsApi.create).mockResolvedValue({
      ...MOCK_EXISTING,
      id: 'new',
    })

    renderButton('plan-1')

    const btn = await screen.findByRole('button', { name: /notify me/i })
    await userEv.click(btn)

    await waitFor(() => {
      expect(endpoints.notificationSubscriptionsApi.create).toHaveBeenCalledWith({
        plan_id: 'plan-1',
        channel: 'email',
        destination: 'alice@example.com',
        trigger: 'terminal_any',
      })
    })
  })

  it('flips to "Notifications on" when a matching subscription exists', async () => {
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([
      MOCK_EXISTING,
    ])

    renderButton('plan-1')

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /notifications on/i }),
      ).toBeInTheDocument()
    })
    expect(screen.queryByRole('button', { name: /^notify me$/i })).toBeNull()
  })

  it('unsubscribes via the menu when a subscription exists', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.notificationSubscriptionsApi.list).mockResolvedValue([
      MOCK_EXISTING,
    ])
    vi.mocked(endpoints.notificationSubscriptionsApi.delete).mockResolvedValue(
      undefined as unknown as never,
    )

    renderButton('plan-1')

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /notifications on/i }),
      ).toBeInTheDocument()
    })
    await userEv.click(screen.getByRole('button', { name: /notification options/i }))
    await userEv.click(await screen.findByRole('menuitem', { name: /unsubscribe/i }))

    await waitFor(() => {
      expect(endpoints.notificationSubscriptionsApi.delete).toHaveBeenCalledWith(
        'sub-1',
      )
    })
  })
})
