/**
 * Tests for MfaEnrollWizard (SFBL-250).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import { ToastProvider } from '../../components/ui/Toast'
import * as client from '../../api/client'
import * as endpoints from '../../api/endpoints'
import MfaEnrollWizard from '../MfaEnrollWizard'
import type { RuntimeConfig, UserResponse } from '../../api/types'

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_USER: UserResponse = {
  id: '1',
  email: 'alice@example.com',
  display_name: 'Alice',
  is_admin: false,
  is_active: true,
  profile: { name: 'operator' },
  permissions: [],
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
}

function renderWizard(props: { open: boolean; onClose: () => void; onEnrolled?: () => void }) {
  const qc = makeQueryClient()
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <MemoryRouter>
              <MfaEnrollWizard {...props} />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('MfaEnrollWizard', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.mfaApi, 'enrollStart')
    vi.spyOn(endpoints.mfaApi, 'enrollConfirm')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('happy path: intro → scan → confirm → backup codes', async () => {
    const user = userEvent.setup()
    localStorage.setItem('auth_token', 'old-token')

    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME) // /api/runtime
      .mockResolvedValueOnce(MOCK_USER) // /api/auth/me bootstrap
      .mockResolvedValueOnce(MOCK_USER) // /api/auth/me after login() swap

    vi.mocked(endpoints.mfaApi.enrollStart).mockResolvedValueOnce({
      secret_base32: 'JBSWY3DPEHPK3PXPABCD',
      otpauth_uri: 'otpauth://totp/SFBL:alice?secret=JBSWY3DPEHPK3PXPABCD&issuer=SFBL',
      qr_svg: '<svg data-testid="svg-content" xmlns="http://www.w3.org/2000/svg"><rect /></svg>',
    })

    vi.mocked(endpoints.mfaApi.enrollConfirm).mockResolvedValueOnce({
      access_token: 'new-access-token',
      token_type: 'bearer',
      expires_in: 3600,
      backup_codes: ['aaaa1-bbbb2', 'cccc3-dddd4'],
    })

    const onEnrolled = vi.fn()
    const onClose = vi.fn()
    renderWizard({ open: true, onClose, onEnrolled })

    // Step 1: intro
    expect(await screen.findByText(/Set up two-factor authentication/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Continue/i }))

    // Step 2: scan — QR rendered, secret visible
    await waitFor(() => {
      expect(screen.getByTestId('mfa-secret')).toHaveTextContent('JBSWY3DPEHPK3PXPABCD')
    })
    expect(screen.getByTestId('mfa-qr').querySelector('svg')).not.toBeNull()
    expect(endpoints.mfaApi.enrollStart).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /Next/i }))

    // Step 3: confirm code
    const codeInput = await screen.findByLabelText(/Authenticator code/i)
    await user.type(codeInput, '123456')
    await user.click(screen.getByRole('button', { name: /Verify and enable/i }))

    await waitFor(() => {
      expect(endpoints.mfaApi.enrollConfirm).toHaveBeenCalledWith({
        secret_base32: 'JBSWY3DPEHPK3PXPABCD',
        code: '123456',
      })
    })

    // Token swap happened
    await waitFor(() => {
      expect(localStorage.getItem('auth_token')).toBe('new-access-token')
    })

    // Step 4: backup codes visible
    await waitFor(() => {
      expect(screen.getByText('aaaa1-bbbb2')).toBeInTheDocument()
    })
    expect(screen.getByText('cccc3-dddd4')).toBeInTheDocument()

    // Acknowledge + close → onEnrolled fires
    await user.click(screen.getByTestId('backup-codes-ack'))
    await user.click(screen.getByTestId('backup-codes-close'))

    expect(onEnrolled).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalled()
  })
})
