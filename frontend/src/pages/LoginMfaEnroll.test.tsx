/**
 * Tests for LoginMfaEnroll (SFBL-251) — forced-enrolment flow used when a
 * tenant has `require_2fa` on and the user has no factor configured.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import LoginMfaEnroll from './LoginMfaEnroll'
import type { RuntimeConfig, UserResponse } from '../api/types'

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
                <LoginMfaEnroll
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

describe('LoginMfaEnroll', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch').mockImplementation(async (path: string) => {
      if (path === '/api/runtime') return MOCK_RUNTIME as unknown as never
      if (path === '/api/auth/me') return MOCK_USER as unknown as never
      throw new Error(`Unexpected apiFetch: ${path}`)
    })
    vi.spyOn(endpoints.loginMfaApi, 'enrollStart').mockResolvedValue({
      secret_base32: 'JBSWY3DPEHPK3PXP',
      otpauth_uri: 'otpauth://totp/test',
      qr_svg: '<svg data-testid="qr-svg"></svg>',
    })
    vi.spyOn(endpoints.loginMfaApi, 'enrollAndVerify')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('happy path: scans, confirms, shows backup-codes modal, navigates on close', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.loginMfaApi.enrollAndVerify).mockResolvedValueOnce({
      access_token: 'full-token',
      token_type: 'bearer',
      expires_in: 3600,
      backup_codes: ['aaaaa-11111', 'bbbbb-22222', 'ccccc-33333'],
    })

    renderView()

    // Step 1: scan — QR + secret displayed
    await waitFor(() => {
      expect(screen.getByTestId('mfa-qr')).toBeInTheDocument()
    })
    expect(screen.getByTestId('mfa-secret')).toHaveTextContent('JBSWY3DPEHPK3PXP')

    // Advance to confirm
    await user.click(screen.getByRole('button', { name: /next/i }))

    // Step 2: confirm — enter 6-digit code
    await user.type(screen.getByTestId('mfa-enroll-code'), '654321')
    await user.click(screen.getByRole('button', { name: /verify and continue/i }))

    await waitFor(() => {
      expect(endpoints.loginMfaApi.enrollAndVerify).toHaveBeenCalledWith('mfa-abc', {
        secret_base32: 'JBSWY3DPEHPK3PXP',
        code: '654321',
      })
    })

    // Step 3: backup-codes modal is shown with the returned codes
    await waitFor(() => {
      expect(screen.getByTestId('backup-codes-list')).toBeInTheDocument()
    })
    expect(screen.getByText('aaaaa-11111')).toBeInTheDocument()
    expect(screen.getByText('bbbbb-22222')).toBeInTheDocument()
    expect(screen.getByText('ccccc-33333')).toBeInTheDocument()

    // Acknowledge and close — navigates to nextPath
    await user.click(screen.getByTestId('backup-codes-ack'))
    await user.click(screen.getByTestId('backup-codes-close'))

    expect(await screen.findByText('Dashboard')).toBeInTheDocument()
  })

  it('shows an inline error when confirm returns 400', async () => {
    const user = userEvent.setup()
    const { ApiError } = await import('../api/client')
    vi.mocked(endpoints.loginMfaApi.enrollAndVerify).mockRejectedValueOnce(
      new ApiError({ status: 400, message: 'invalid_code' }),
    )

    renderView()

    await waitFor(() => {
      expect(screen.getByTestId('mfa-qr')).toBeInTheDocument()
    })
    await user.click(screen.getByRole('button', { name: /next/i }))
    await user.type(screen.getByTestId('mfa-enroll-code'), '000000')
    await user.click(screen.getByRole('button', { name: /verify and continue/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/incorrect code/i)
    })
  })
})
