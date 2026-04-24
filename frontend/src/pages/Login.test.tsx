/**
 * Tests for Login (SFBL-251, SFBL-204).
 *
 * Covers:
 * - SFBL-251 routing: phase-1 response with `mfa_required` routes to the
 *   challenge or the forced-enrol view based on `must_enroll`.
 * - SFBL-204 DOM tab order: email → password → submit → forgot-password.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import Login from './Login'
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

function renderLogin() {
  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={['/login']}>
          <Login />
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('Login', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch').mockImplementation(async (path: string) => {
      if (path === '/api/runtime') return MOCK_RUNTIME as unknown as never
      if (path === '/api/auth/me') return MOCK_USER as unknown as never
      throw new Error(`Unexpected apiFetch: ${path}`)
    })
    vi.spyOn(client, 'apiPost')
    vi.spyOn(endpoints.loginMfaApi, 'enrollStart').mockResolvedValue({
      secret_base32: 'JBSWY3DPEHPK3PXP',
      otpauth_uri: 'otpauth://totp/test',
      qr_svg: '<svg></svg>',
    })
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('SFBL-204: DOM source order is email → password → submit → forgot-password', async () => {
    renderLogin()

    const focusables = Array.from(
      document.querySelectorAll<HTMLElement>(
        'input, button, [href]',
      ),
    )
    // Identify meaningful nodes (filter out any nested branding etc.).
    const labels = focusables
      .map((el) => el.getAttribute('id') ?? el.getAttribute('data-testid') ?? el.tagName)
      .filter(Boolean)

    const emailIdx = labels.indexOf('email')
    const passwordIdx = labels.indexOf('password')
    const submitIdx = labels.indexOf('login-submit')
    // Forgot-password link has no id — find by tagName A.
    const forgotIdx = focusables.findIndex(
      (el) =>
        el.tagName === 'A' &&
        /forgot password/i.test(el.textContent ?? ''),
    )

    expect(emailIdx).toBeGreaterThanOrEqual(0)
    expect(emailIdx).toBeLessThan(passwordIdx)
    expect(passwordIdx).toBeLessThan(submitIdx)
    expect(submitIdx).toBeLessThan(forgotIdx)
  })

  it('routes to the MFA challenge when mfa_required with must_enroll=false', async () => {
    const user = userEvent.setup()
    vi.mocked(client.apiPost).mockResolvedValueOnce({
      mfa_required: true,
      mfa_token: 'mfa-xyz',
      mfa_methods: ['totp'],
      must_enroll: false,
    })

    renderLogin()

    await user.type(screen.getByLabelText(/email/i), 'alice@example.com')
    await user.type(screen.getByLabelText(/password/i), 'hunter2')
    await user.click(screen.getByTestId('login-submit'))

    await waitFor(() => {
      expect(screen.getByText(/two-factor verification/i)).toBeInTheDocument()
    })
    expect(screen.getByTestId('mfa-challenge-input')).toBeInTheDocument()
  })

  it('routes to the forced-enrol view when mfa_required with must_enroll=true', async () => {
    const user = userEvent.setup()
    vi.mocked(client.apiPost).mockResolvedValueOnce({
      mfa_required: true,
      mfa_token: 'mfa-xyz',
      mfa_methods: ['totp'],
      must_enroll: true,
    })

    renderLogin()

    await user.type(screen.getByLabelText(/email/i), 'alice@example.com')
    await user.type(screen.getByLabelText(/password/i), 'hunter2')
    await user.click(screen.getByTestId('login-submit'))

    await waitFor(() => {
      expect(
        screen.getByText(/set up two-factor authentication/i),
      ).toBeInTheDocument()
    })
    expect(endpoints.loginMfaApi.enrollStart).toHaveBeenCalledWith('mfa-xyz')
  })
})
