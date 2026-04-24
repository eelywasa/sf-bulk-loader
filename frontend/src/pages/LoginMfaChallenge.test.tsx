/**
 * Tests for LoginMfaChallenge (SFBL-251).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import { ApiError } from '../api/client'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import LoginMfaChallenge from './LoginMfaChallenge'
import type { RuntimeConfig, UserResponse, TokenResponse } from '../api/types'

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
  is_active: true,
}

function renderView(onAbort = vi.fn()) {
  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={['/login']}>
          <Routes>
            <Route
              path="/login"
              element={
                <LoginMfaChallenge
                  mfaToken="mfa-abc"
                  nextPath="/dashboard"
                  onAbort={onAbort}
                />
              }
            />
            <Route path="/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('LoginMfaChallenge', () => {
  beforeEach(() => {
    localStorage.clear()
    // Runtime fetch (AuthProvider bootstrap): hosted, no stored token.
    vi.spyOn(client, 'apiFetch').mockImplementation(async (path: string) => {
      if (path === '/api/runtime') return MOCK_RUNTIME as unknown as never
      if (path === '/api/auth/me') return MOCK_USER as unknown as never
      throw new Error(`Unexpected apiFetch: ${path}`)
    })
    vi.spyOn(endpoints.loginMfaApi, 'verify')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders the TOTP input and toggle to backup code', async () => {
    renderView()
    expect(await screen.findByLabelText('Authenticator code')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /verify and continue/i })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /use a backup code instead/i }),
    ).toBeInTheDocument()
  })

  it('happy path: TOTP success calls login() and navigates to nextPath', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.loginMfaApi.verify).mockResolvedValueOnce({
      access_token: 'full-token',
      token_type: 'bearer',
      expires_in: 3600,
    } as TokenResponse)

    renderView()

    await user.type(await screen.findByTestId('mfa-challenge-input'), '123456')
    await user.click(screen.getByTestId('mfa-challenge-submit'))

    await waitFor(() => {
      expect(endpoints.loginMfaApi.verify).toHaveBeenCalledWith('mfa-abc', {
        method: 'totp',
        code: '123456',
      })
    })
    // login() stored the access token
    await waitFor(() => {
      expect(localStorage.getItem('auth_token')).toBe('full-token')
    })
    expect(await screen.findByText('Dashboard')).toBeInTheDocument()
  })

  it('shows "Incorrect code" on 401 without mfa_token code', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.loginMfaApi.verify).mockRejectedValueOnce(
      new ApiError({ status: 401, message: 'invalid_code', code: 'invalid_code' }),
    )

    renderView()

    await user.type(await screen.findByTestId('mfa-challenge-input'), '123456')
    await user.click(screen.getByTestId('mfa-challenge-submit'))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/incorrect code/i)
    })
  })

  it('calls onAbort when the mfa_token is expired (401 mfa_token_expired)', async () => {
    const user = userEvent.setup()
    const onAbort = vi.fn()
    vi.mocked(endpoints.loginMfaApi.verify).mockRejectedValueOnce(
      new ApiError({
        status: 401,
        message: 'mfa_token expired',
        code: 'mfa_token_expired',
      }),
    )

    renderView(onAbort)

    await user.type(await screen.findByTestId('mfa-challenge-input'), '123456')
    await user.click(screen.getByTestId('mfa-challenge-submit'))

    await waitFor(() => {
      expect(onAbort).toHaveBeenCalledWith('Session expired, please sign in again.')
    })
  })

  it('supports the backup-code path', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.loginMfaApi.verify).mockResolvedValueOnce({
      access_token: 'full-token',
      token_type: 'bearer',
      expires_in: 3600,
    } as TokenResponse)

    renderView()

    // Flip into backup-code mode
    await user.click(
      await screen.findByRole('button', { name: /use a backup code instead/i }),
    )
    expect(screen.getByLabelText('Backup code')).toBeInTheDocument()

    await user.type(screen.getByTestId('mfa-challenge-input'), 'abcde-12345')
    await user.click(screen.getByTestId('mfa-challenge-submit'))

    await waitFor(() => {
      expect(endpoints.loginMfaApi.verify).toHaveBeenCalledWith('mfa-abc', {
        method: 'backup_code',
        code: 'abcde-12345',
      })
    })
  })
})
