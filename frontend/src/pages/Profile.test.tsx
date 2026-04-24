/**
 * Tests for Profile.tsx — SFBL-149 + SFBL-192
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
import Profile from './Profile'
import type { RuntimeConfig, UserResponse, TokenResponse, LoginHistoryEntry, MfaStatus } from '../api/types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const MOCK_RUNTIME_LOCAL: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_USER: UserResponse = {
  id: '1',
  email: 'alice@example.com',
  display_name: 'Alice',
  is_admin: true,
  is_active: true,
  profile: { name: 'admin' },
}

const MOCK_TOKEN_RESPONSE: TokenResponse = {
  access_token: 'new-access-token',
  token_type: 'bearer',
  expires_in: 3600,
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderProfile() {
  const qc = makeQueryClient()
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <MemoryRouter>
              <Profile />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('Profile page', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.meApi, 'updateProfile')
    vi.spyOn(endpoints.meApi, 'changePassword')
    vi.spyOn(endpoints.meApi, 'requestEmailChange')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders the page headings', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL) // bootstrap: /api/runtime
      .mockResolvedValueOnce(MOCK_USER)          // bootstrap: /api/auth/me

    renderProfile()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Profile', level: 1 })).toBeInTheDocument()
    })
    expect(screen.getByRole('heading', { name: 'Account identity', level: 2 })).toBeInTheDocument()
    // "Display name" appears as both h2 and label — use getAllByText
    expect(screen.getAllByText('Display name').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('heading', { name: 'Email address', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Password', level: 2 })).toBeInTheDocument()
  })

  it('shows the user identity fields when authenticated', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)

    renderProfile()

    // Email is in the identity card; wait for AuthProvider to finish bootstrapping
    await waitFor(() => {
      expect(screen.getAllByText('alice@example.com').length).toBeGreaterThan(0)
    }, { timeout: 3000 })
    expect(screen.getByText('admin')).toBeInTheDocument()
    expect(screen.getByText('active')).toBeInTheDocument()
  })

  it('saves display name on submit and shows success', async () => {
    const user = userEvent.setup()
    localStorage.setItem('auth_token', 'test-token')

    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
      // login re-fetch after update
      .mockResolvedValueOnce(MOCK_USER)

    vi.mocked(endpoints.meApi.updateProfile).mockResolvedValueOnce({
      ...MOCK_USER,
      display_name: 'Alice Smith',
    })

    renderProfile()

    // Wait for component to mount with user
    await waitFor(() => {
      expect(screen.getByLabelText('Display name')).toBeInTheDocument()
    })

    const input = screen.getByLabelText('Display name')
    await user.clear(input)
    await user.type(input, 'Alice Smith')

    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(screen.getByText('Display name updated.')).toBeInTheDocument()
    })
    expect(endpoints.meApi.updateProfile).toHaveBeenCalledWith({ display_name: 'Alice Smith' })
  })

  it('shows error when display name update fails', async () => {
    const user = userEvent.setup()
    localStorage.setItem('auth_token', 'test-token')

    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)

    vi.mocked(endpoints.meApi.updateProfile).mockRejectedValueOnce(
      new client.ApiError({ status: 400, message: 'Display name is invalid' }),
    )

    renderProfile()

    await waitFor(() => {
      expect(screen.getByLabelText('Display name')).toBeInTheDocument()
    })

    const input = screen.getByLabelText('Display name')
    await user.clear(input)
    await user.type(input, 'Bad Name!!')

    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(screen.getByText('Display name is invalid')).toBeInTheDocument()
    })
  })

  it('password change calls changePassword and re-logs in on success', async () => {
    const user = userEvent.setup()
    localStorage.setItem('auth_token', 'test-token')

    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
      // login call after password change
      .mockResolvedValueOnce(MOCK_USER)

    vi.mocked(endpoints.meApi.changePassword).mockResolvedValueOnce(MOCK_TOKEN_RESPONSE)

    renderProfile()

    await waitFor(() => {
      expect(screen.getByLabelText('Current password')).toBeInTheDocument()
    })

    await user.type(screen.getByLabelText('Current password'), 'OldPassword1!')
    await user.type(screen.getByLabelText('New password'), 'NewPassword1!abcde')
    await user.type(screen.getByLabelText('Confirm new password'), 'NewPassword1!abcde')

    await user.click(screen.getByRole('button', { name: 'Change password' }))

    await waitFor(() => {
      expect(screen.getByText(/Password changed successfully/)).toBeInTheDocument()
    })

    expect(endpoints.meApi.changePassword).toHaveBeenCalledWith({
      current_password: 'OldPassword1!',
      new_password: 'NewPassword1!abcde',
    })
    // The new token should be stored
    expect(localStorage.getItem('auth_token')).toBe('new-access-token')
  })

  it('email change request transitions to confirmation state', async () => {
    const user = userEvent.setup()
    localStorage.setItem('auth_token', 'test-token')

    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)

    vi.mocked(endpoints.meApi.requestEmailChange).mockResolvedValueOnce(undefined)

    renderProfile()

    await waitFor(() => {
      expect(screen.getByLabelText('New email address')).toBeInTheDocument()
    })

    await user.type(screen.getByLabelText('New email address'), 'newalice@example.com')
    await user.click(screen.getByRole('button', { name: 'Request change' }))

    await waitFor(() => {
      expect(screen.getByText(/Check your inbox/)).toBeInTheDocument()
    })

    expect(endpoints.meApi.requestEmailChange).toHaveBeenCalledWith({
      new_email: 'newalice@example.com',
    })
  })

  it('shows warning on 429 from email change request', async () => {
    const user = userEvent.setup()
    localStorage.setItem('auth_token', 'test-token')

    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)

    vi.mocked(endpoints.meApi.requestEmailChange).mockRejectedValueOnce(
      new client.ApiError({ status: 429, message: 'Rate limit exceeded' }),
    )

    renderProfile()

    await waitFor(() => {
      expect(screen.getByLabelText('New email address')).toBeInTheDocument()
    })

    await user.type(screen.getByLabelText('New email address'), 'newalice@example.com')
    await user.click(screen.getByRole('button', { name: 'Request change' }))

    await waitFor(() => {
      expect(screen.getByText(/Too many requests/)).toBeInTheDocument()
    })
  })
})

// ─── SecurityCard tests (SFBL-250) ────────────────────────────────────────────

describe('Profile page — SecurityCard', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.meApi, 'getLoginHistory').mockResolvedValue([])
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  function withMfa(mfa: MfaStatus): UserResponse {
    return { ...MOCK_USER, mfa }
  }

  it('renders "Off" + Set up button when user is not enrolled', async () => {
    localStorage.setItem('auth_token', 'test-token')
    const user = withMfa({
      enrolled: false,
      enrolled_at: null,
      backup_codes_remaining: 0,
      tenant_required: false,
    })
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(user)

    renderProfile()

    await waitFor(() => {
      expect(screen.getByTestId('security-card')).toBeInTheDocument()
    })
    expect(screen.getByText('Off')).toBeInTheDocument()
    expect(screen.getByTestId('mfa-setup')).toBeInTheDocument()
    expect(screen.queryByTestId('mfa-regen')).not.toBeInTheDocument()
    expect(screen.queryByTestId('mfa-disable')).not.toBeInTheDocument()
  })

  it('renders "On", remaining count, regenerate + disable when enrolled and tenant does not require', async () => {
    localStorage.setItem('auth_token', 'test-token')
    const user = withMfa({
      enrolled: true,
      enrolled_at: '2026-04-01T10:00:00Z',
      backup_codes_remaining: 7,
      tenant_required: false,
    })
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(user)

    renderProfile()

    await waitFor(() => {
      expect(screen.getByTestId('security-card')).toBeInTheDocument()
    })
    expect(screen.getByText('On')).toBeInTheDocument()
    expect(screen.getByTestId('backup-codes-remaining')).toHaveTextContent('7 backup codes remaining')
    expect(screen.getByTestId('mfa-regen')).toBeInTheDocument()
    expect(screen.getByTestId('mfa-disable')).toBeInTheDocument()
    expect(screen.queryByTestId('mfa-setup')).not.toBeInTheDocument()
  })

  it('hides Disable when tenant_required is true', async () => {
    localStorage.setItem('auth_token', 'test-token')
    const user = withMfa({
      enrolled: true,
      enrolled_at: '2026-04-01T10:00:00Z',
      backup_codes_remaining: 7,
      tenant_required: true,
    })
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(user)

    renderProfile()

    await waitFor(() => {
      expect(screen.getByTestId('security-card')).toBeInTheDocument()
    })
    expect(screen.getByTestId('mfa-regen')).toBeInTheDocument()
    expect(screen.queryByTestId('mfa-disable')).not.toBeInTheDocument()
  })
})

// ─── LoginHistoryCard tests (SFBL-192) ────────────────────────────────────────

describe('Profile page — LoginHistoryCard', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.meApi, 'getLoginHistory')
    // Stub other meApi methods to prevent unhandled rejection noise
    vi.spyOn(endpoints.meApi, 'updateProfile')
    vi.spyOn(endpoints.meApi, 'changePassword')
    vi.spyOn(endpoints.meApi, 'requestEmailChange')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders empty state when history is empty', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.meApi.getLoginHistory).mockResolvedValueOnce([])

    renderProfile()

    await waitFor(() => {
      expect(screen.getByText('Recent sign-in activity')).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByTestId('login-history-empty')).toBeInTheDocument()
    })
    expect(screen.getByText(/No sign-in activity recorded yet/)).toBeInTheDocument()
  })

  it('renders the history table when data is present', async () => {
    localStorage.setItem('auth_token', 'test-token')

    const mockHistory: LoginHistoryEntry[] = [
      { attempted_at: '2024-06-01T10:00:00Z', ip: '192.168.1.1', outcome: 'Success' },
      { attempted_at: '2024-06-01T09:00:00Z', ip: '10.0.0.1', outcome: 'Failed' },
    ]

    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.meApi.getLoginHistory).mockResolvedValueOnce(mockHistory)

    renderProfile()

    await waitFor(() => {
      expect(screen.getByTestId('login-history-table')).toBeInTheDocument()
    })

    expect(screen.getByText('192.168.1.1')).toBeInTheDocument()
    expect(screen.getByText('10.0.0.1')).toBeInTheDocument()
    expect(screen.getByText('Success')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('shows the section heading', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)
    vi.mocked(endpoints.meApi.getLoginHistory).mockResolvedValueOnce([])

    renderProfile()

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'Recent sign-in activity', level: 2 }),
      ).toBeInTheDocument()
    })
  })
})
