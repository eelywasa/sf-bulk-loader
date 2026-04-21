/**
 * Tests for SettingsEmailPage (SFBL-157).
 *
 * Covers:
 *  - Renders fields from the category response
 *  - Happy-path save (PATCH called with only changed fields; toast shown)
 *  - 422 error path (field-level errors highlighted; error toast shown)
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
import SettingsEmailPage from './SettingsEmailPage'
import type { RuntimeConfig, UserResponse, CategorySettings } from '../api/types'

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_USER: UserResponse = {
  id: 'u1',
  username: 'admin',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'admin',
  is_active: true,
}

const MOCK_EMAIL_CATEGORY: CategorySettings = {
  category: 'email',
  settings: [
    {
      key: 'email_backend',
      value: 'noop',
      type: 'str',
      is_secret: false,
      description: 'Email delivery backend.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_from_address',
      value: 'noreply@example.com',
      type: 'str',
      is_secret: false,
      description: 'Sender address.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_from_name',
      value: '',
      type: 'str',
      is_secret: false,
      description: 'Display name.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_reply_to',
      value: '',
      type: 'str',
      is_secret: false,
      description: 'Reply-To address.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'frontend_base_url',
      value: '',
      type: 'str',
      is_secret: false,
      description: 'Base URL of the frontend.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_smtp_password',
      value: null,
      type: 'str',
      is_secret: true,
      description: 'SMTP authentication password.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_smtp_host',
      value: '',
      type: 'str',
      is_secret: false,
      description: 'SMTP server hostname.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_smtp_port',
      value: 587,
      type: 'int',
      is_secret: false,
      description: 'SMTP server port.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_smtp_username',
      value: '',
      type: 'str',
      is_secret: false,
      description: 'SMTP authentication username.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_smtp_starttls',
      value: true,
      type: 'bool',
      is_secret: false,
      description: 'Use STARTTLS.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_smtp_use_tls',
      value: false,
      type: 'bool',
      is_secret: false,
      description: 'Use implicit TLS.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_ses_region',
      value: '',
      type: 'str',
      is_secret: false,
      description: 'AWS region for SES.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_ses_configuration_set',
      value: '',
      type: 'str',
      is_secret: false,
      description: 'Optional SES configuration set name.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_max_retries',
      value: 3,
      type: 'int',
      is_secret: false,
      description: 'Maximum number of retry attempts.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_retry_backoff_seconds',
      value: 2.0,
      type: 'float',
      is_secret: false,
      description: 'Base delay in seconds.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_retry_backoff_max_seconds',
      value: 120.0,
      type: 'float',
      is_secret: false,
      description: 'Cap in seconds.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_timeout_seconds',
      value: 15.0,
      type: 'float',
      is_secret: false,
      description: 'Per-message send timeout.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_claim_lease_seconds',
      value: 60,
      type: 'int',
      is_secret: false,
      description: 'Duration in seconds for worker lease.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_pending_stale_minutes',
      value: 15,
      type: 'int',
      is_secret: false,
      description: 'Stale pending minutes.',
      restart_required: false,
      updated_at: null,
    },
    {
      key: 'email_log_recipients',
      value: false,
      type: 'bool',
      is_secret: false,
      description: 'Store recipient address.',
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
            <MemoryRouter initialEntries={['/settings/email']}>
              <SettingsEmailPage />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('SettingsEmailPage', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'test-token')
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints, 'getSettingsCategory')
    vi.spyOn(endpoints, 'updateSettingsCategory')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders core email fields from the category response', async () => {
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.getSettingsCategory).mockResolvedValue({
      data: MOCK_EMAIL_CATEGORY,
      cacheTtl: 60,
    })

    renderPage()

    // Should show the email_backend and email_from_address fields
    await waitFor(() => {
      expect(screen.getByLabelText('email_backend')).toBeInTheDocument()
    })
    expect(screen.getByLabelText('email_from_address')).toBeInTheDocument()
    expect(screen.getByText(/changes take up to 60s/i)).toBeInTheDocument()
  })

  it('shows noop callout when email_backend is noop', async () => {
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.getSettingsCategory).mockResolvedValue({
      data: MOCK_EMAIL_CATEGORY,
      cacheTtl: 60,
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText(/Email is disabled/i)).toBeInTheDocument()
    })
  })

  it('calls updateSettingsCategory with only changed fields on save', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.getSettingsCategory).mockResolvedValue({
      data: MOCK_EMAIL_CATEGORY,
      cacheTtl: 60,
    })
    vi.mocked(endpoints.updateSettingsCategory).mockResolvedValue({
      data: {
        ...MOCK_EMAIL_CATEGORY,
        settings: MOCK_EMAIL_CATEGORY.settings.map((s) =>
          s.key === 'email_from_address'
            ? { ...s, value: 'changed@example.com' }
            : s,
        ),
      },
      cacheTtl: 60,
    })

    renderPage()

    const fromField = await screen.findByLabelText('email_from_address')
    await userEv.clear(fromField)
    await userEv.type(fromField, 'changed@example.com')

    const saveBtn = screen.getByRole('button', { name: /save/i })
    await userEv.click(saveBtn)

    await waitFor(() => {
      expect(endpoints.updateSettingsCategory).toHaveBeenCalledWith(
        'email',
        expect.objectContaining({ email_from_address: 'changed@example.com' }),
      )
    })
    // Toast should show
    await waitFor(() => {
      expect(screen.getByText(/settings saved/i)).toBeInTheDocument()
    })
  })

  it('shows field-level errors on 422 response', async () => {
    const userEv = userEvent.setup()
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.getSettingsCategory).mockResolvedValue({
      data: MOCK_EMAIL_CATEGORY,
      cacheTtl: 60,
    })

    const apiErr = new client.ApiError({
      status: 422,
      message: 'Validation error',
      detail: [{ field: 'email_from_address', error: 'invalid email format' }] as never,
    })
    vi.mocked(endpoints.updateSettingsCategory).mockRejectedValue(apiErr)

    renderPage()

    const fromField = await screen.findByLabelText('email_from_address')
    await userEv.clear(fromField)
    await userEv.type(fromField, 'not-an-email')

    const saveBtn = screen.getByRole('button', { name: /save/i })
    await userEv.click(saveBtn)

    await waitFor(() => {
      expect(screen.getAllByText(/invalid email format/i).length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText(/some settings couldn't be saved/i).length).toBeGreaterThan(0)
  })
})
