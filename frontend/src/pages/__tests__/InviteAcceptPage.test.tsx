import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import { ApiError } from '../../api/client'
import * as endpoints from '../../api/endpoints'
import * as client from '../../api/client'
import InviteAcceptPage from '../InviteAcceptPage'

// ─── Helpers ─────────────────────────────────────────────────────────────────

const VALID_TOKEN = 'valid-invite-token-abc'
const VALID_PASSWORD = 'ValidP4ss!word123'

const MOCK_INVITE_INFO = {
  email: 'invited@example.com',
  display_name: 'Invited User',
  profile_name: 'operator',
}

function renderPage(token: string | null = VALID_TOKEN) {
  const qs = token ? `?token=${token}` : ''
  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={[`/invite/accept${qs}`]}>
          <Routes>
            <Route path="/invite/accept" element={<InviteAcceptPage />} />
            <Route path="/" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('InviteAcceptPage', () => {
  beforeEach(() => {
    vi.spyOn(endpoints.invitationsApi, 'getInfo')
    vi.spyOn(endpoints.invitationsApi, 'accept')
    // apiFetch is used by AuthProvider to call /api/runtime and /api/auth/me
    vi.spyOn(client, 'apiFetch').mockRejectedValue(new ApiError({ status: 503, message: 'unavailable' }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows loading state initially', () => {
    vi.mocked(endpoints.invitationsApi.getInfo).mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(screen.getByText(/validating your invitation/i)).toBeInTheDocument()
  })

  it('renders email and profile after valid token lookup', async () => {
    vi.mocked(endpoints.invitationsApi.getInfo).mockResolvedValueOnce(MOCK_INVITE_INFO)

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('invited@example.com')).toBeInTheDocument()
    })
    expect(screen.getByText(/operator/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByLabelText('Confirm password')).toBeInTheDocument()
  })

  it('shows invalid state when token is absent from URL', async () => {
    renderPage(null)

    await waitFor(() => {
      expect(screen.getByText(/invitation unavailable/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/no invitation token/i)).toBeInTheDocument()
  })

  it('shows invalid state when token lookup returns 404', async () => {
    vi.mocked(endpoints.invitationsApi.getInfo).mockRejectedValueOnce(
      new ApiError({ status: 404, message: 'Not found' }),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByText(/invitation unavailable/i)).toBeInTheDocument()
    })
  })

  it('shows already-accepted message when token returns 410', async () => {
    vi.mocked(endpoints.invitationsApi.getInfo).mockRejectedValueOnce(
      new ApiError({ status: 410, message: 'Gone' }),
    )

    renderPage()

    await waitFor(() => {
      expect(screen.getByText(/already been accepted/i)).toBeInTheDocument()
    })
  })

  it('submits password and navigates to / on success', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.invitationsApi.getInfo).mockResolvedValueOnce(MOCK_INVITE_INFO)
    vi.mocked(endpoints.invitationsApi.accept).mockResolvedValueOnce({
      access_token: 'jwt-token-abc',
      token_type: 'bearer',
    })
    // Mock /api/auth/me for the login flow
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce({ auth_mode: 'local', app_distribution: 'self_hosted', transport_mode: 'http', input_storage_mode: 'local' })
      .mockResolvedValueOnce({ id: 'user-1', email: 'invited@example.com', display_name: null, permissions: [] })

    renderPage()

    await waitFor(() => screen.getByLabelText('Password'))

    await user.type(screen.getByLabelText('Password'), VALID_PASSWORD)
    await user.type(screen.getByLabelText('Confirm password'), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: /set password and sign in/i }))

    await waitFor(() => {
      expect(endpoints.invitationsApi.accept).toHaveBeenCalledWith(VALID_TOKEN, { password: VALID_PASSWORD })
    })
  })

  it('shows error when accept returns 410', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.invitationsApi.getInfo).mockResolvedValueOnce(MOCK_INVITE_INFO)
    vi.mocked(endpoints.invitationsApi.accept).mockRejectedValueOnce(
      new ApiError({ status: 410, message: 'Gone' }),
    )

    renderPage()

    await waitFor(() => screen.getByLabelText('Password'))

    await user.type(screen.getByLabelText('Password'), VALID_PASSWORD)
    await user.type(screen.getByLabelText('Confirm password'), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: /set password and sign in/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/no longer valid|already been accepted/i)
    })
  })

  it('shows password mismatch error and disables submit', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.invitationsApi.getInfo).mockResolvedValueOnce(MOCK_INVITE_INFO)

    renderPage()

    await waitFor(() => screen.getByLabelText('Password'))

    await user.type(screen.getByLabelText('Password'), VALID_PASSWORD)
    await user.type(screen.getByLabelText('Confirm password'), 'different')

    expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /set password and sign in/i })).toBeDisabled()
  })

  it('shows strength meter hints while typing', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.invitationsApi.getInfo).mockResolvedValueOnce(MOCK_INVITE_INFO)

    renderPage()

    await waitFor(() => screen.getByLabelText('Password'))

    await user.type(screen.getByLabelText('Password'), 'short')

    expect(screen.getByText(/at least 12 characters/i)).toBeInTheDocument()
  })
})
