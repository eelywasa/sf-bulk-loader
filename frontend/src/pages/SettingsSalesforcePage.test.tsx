/**
 * Tests for SettingsSalesforcePage (SFBL-157).
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
import SettingsSalesforcePage from './SettingsSalesforcePage'
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

const MOCK_SALESFORCE_CATEGORY: CategorySettings = {
  category: 'salesforce',
  settings: [
    {
      key: 'sf_poll_interval_initial',
      value: 5,
      type: 'int',
      is_secret: false,
      description: 'Initial polling interval in seconds (floor of exponential backoff).',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'sf_poll_interval_max',
      value: 30,
      type: 'int',
      is_secret: false,
      description: 'Maximum polling interval in seconds (ceiling of exponential backoff).',
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
            <MemoryRouter initialEntries={['/settings/salesforce']}>
              <SettingsSalesforcePage />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('SettingsSalesforcePage', () => {
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

  it('renders salesforce settings fields', async () => {
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.getSettingsCategory).mockResolvedValue({
      data: MOCK_SALESFORCE_CATEGORY,
      cacheTtl: 60,
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText('sf_poll_interval_initial')).toBeInTheDocument()
    })
    expect(screen.getByLabelText('sf_poll_interval_max')).toBeInTheDocument()
    expect(screen.getByText('Salesforce Settings')).toBeInTheDocument()
  })
})
