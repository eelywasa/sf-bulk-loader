/**
 * Tests for VerifyEmail.tsx — SFBL-149
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import * as client from '../api/client'
import * as endpoints from '../api/endpoints'
import VerifyEmail from './VerifyEmail'
import type { RuntimeConfig } from '../api/types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const MOCK_RUNTIME_NONE: RuntimeConfig = {
  auth_mode: 'none',
  app_distribution: 'desktop',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_RUNTIME_LOCAL: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

function renderVerifyEmail(token = 'test-token', runtime = MOCK_RUNTIME_NONE) {
  // Stub the runtime fetch so AuthProvider doesn't try to reach the network
  vi.mocked(client.apiFetch).mockResolvedValue(runtime)

  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={[`/verify-email/${token}`]}>
          <Routes>
            <Route path="/verify-email/:token" element={<VerifyEmail />} />
            <Route path="/profile" element={<div>Profile page</div>} />
            <Route path="/login" element={<div>Login page</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('VerifyEmail page', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.meApi, 'confirmEmailChange')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('shows loading state initially', () => {
    // Never resolves so we stay in loading state
    vi.mocked(endpoints.meApi.confirmEmailChange).mockReturnValue(new Promise(() => {}))

    renderVerifyEmail()

    expect(screen.getByText(/Verifying/)).toBeInTheDocument()
  })

  it('shows success message when confirm resolves', async () => {
    vi.mocked(endpoints.meApi.confirmEmailChange).mockResolvedValueOnce(undefined)

    renderVerifyEmail()

    await waitFor(() => {
      expect(screen.getByText(/Your email address has been updated/)).toBeInTheDocument()
    })
  })

  it('shows failure message when confirm rejects with ApiError', async () => {
    vi.mocked(endpoints.meApi.confirmEmailChange).mockRejectedValueOnce(
      new client.ApiError({ status: 400, message: 'Token expired or already used' }),
    )

    renderVerifyEmail()

    await waitFor(() => {
      expect(screen.getByText('Token expired or already used')).toBeInTheDocument()
    })
  })

  it('shows fallback failure message for unknown errors', async () => {
    vi.mocked(endpoints.meApi.confirmEmailChange).mockRejectedValueOnce(
      new Error('Network failure'),
    )

    renderVerifyEmail()

    await waitFor(() => {
      expect(screen.getByText('Network failure')).toBeInTheDocument()
    })
  })

  it('shows sign-in link when unauthenticated (auth required profile)', async () => {
    vi.mocked(endpoints.meApi.confirmEmailChange).mockResolvedValueOnce(undefined)
    // Override the first call to return local runtime (requires auth, no token stored)
    vi.mocked(client.apiFetch).mockResolvedValue(MOCK_RUNTIME_LOCAL)

    renderVerifyEmail('my-token', MOCK_RUNTIME_LOCAL)

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /sign in/i })).toBeInTheDocument()
    })
  })

  it('calls confirmEmailChange with the token from the URL', async () => {
    vi.mocked(endpoints.meApi.confirmEmailChange).mockResolvedValueOnce(undefined)

    renderVerifyEmail('abc123token')

    await waitFor(() => {
      expect(endpoints.meApi.confirmEmailChange).toHaveBeenCalledWith({ token: 'abc123token' })
    })
  })
})
