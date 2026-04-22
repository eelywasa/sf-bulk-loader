/**
 * Tests for SettingsSecurityPage (SFBL-157).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import { ToastProvider } from '../components/ui/Toast'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import SettingsSecurityPage from './SettingsSecurityPage'
import type { RuntimeConfig, UserResponse, CategorySettings } from '../api/types'

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_USER: UserResponse = {
  id: 'u1',
  email: 'admin@example.com',
  display_name: 'Admin',
  is_active: true,
}

const MOCK_SECURITY_CATEGORY: CategorySettings = {
  category: 'security',
  settings: [
    {
      key: 'login_rate_limit_attempts',
      value: 20,
      type: 'int',
      is_secret: false,
      description: 'Maximum login attempts allowed within the rate-limit window.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'login_rate_limit_window_seconds',
      value: 300,
      type: 'int',
      is_secret: false,
      description: 'Window size in seconds for the login rate-limit counter.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'login_tier1_threshold',
      value: 5,
      type: 'int',
      is_secret: false,
      description: 'Number of consecutive failures that trigger a Tier-1 temporary lockout.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'login_tier1_window_minutes',
      value: 15,
      type: 'int',
      is_secret: false,
      description: 'Rolling window in minutes over which Tier-1 failures are counted.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'login_tier1_lock_minutes',
      value: 15,
      type: 'int',
      is_secret: false,
      description: 'Duration in minutes for a Tier-1 automatic account lock.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'login_tier2_threshold',
      value: 15,
      type: 'int',
      is_secret: false,
      description: 'Total failures within tier2_window_hours that trigger a Tier-2 lock.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'login_tier2_tier1_count',
      value: 3,
      type: 'int',
      is_secret: false,
      description: 'Number of Tier-1 locks within tier2_window_hours that trigger a Tier-2 lock.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'login_tier2_window_hours',
      value: 24,
      type: 'int',
      is_secret: false,
      description: 'Rolling window in hours over which Tier-2 lock triggers are counted.',
      restart_required: false,
      updated_at: null,
    },
  ],
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderPage() {
  const qc = makeQueryClient()
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <MemoryRouter initialEntries={['/settings/security']}>
              <SettingsSecurityPage />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('SettingsSecurityPage', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'test-token')
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints, 'getSettingsCategory')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders security fields and rate-limit callout', async () => {
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.getSettingsCategory).mockResolvedValue({
      data: MOCK_SECURITY_CATEGORY,
      cacheTtl: 60,
    })

    renderPage()

    // Callout
    await waitFor(() => {
      expect(screen.getByText(/changes to rate-limit windows apply/i)).toBeInTheDocument()
    })

    // Fields
    expect(screen.getByLabelText('login_rate_limit_attempts')).toBeInTheDocument()
    expect(screen.getByLabelText('login_tier1_threshold')).toBeInTheDocument()
  })
})
