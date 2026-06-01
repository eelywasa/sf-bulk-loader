/**
 * Tests for SettingsTokensPage (SFBL-369).
 *
 * Verifies:
 * - The page renders only when the user holds tokens.manage permission.
 * - Without the permission, a "no permission" fallback is shown.
 * - The tokens table renders listed tokens.
 * - The create flow opens the form, submits, and shows the copy-once modal
 *   with the plaintext token and the "not shown again" warning.
 * - Revoking a token shows a confirmation dialog and calls the API on confirm.
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
import SettingsTokensPage from './SettingsTokensPage'
import type { RuntimeConfig, UserResponse } from '../api/types'
import type { PatMetadata, PatCreateResponse } from '../api/types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const MOCK_RUNTIME_LOCAL: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_USER_WITH_PERMISSION: UserResponse = {
  id: 'u1',
  email: 'alice@example.com',
  display_name: 'Alice',
  is_active: true,
  permissions: ['tokens.manage'],
}

const MOCK_USER_WITHOUT_PERMISSION: UserResponse = {
  id: 'u2',
  email: 'viewer@example.com',
  display_name: 'Viewer',
  is_active: true,
  permissions: [],
}

const MOCK_TOKEN: PatMetadata = {
  id: 'tok-1',
  name: 'CI pipeline',
  prefix: 'sfbl_',
  last4: 'abcd',
  created_at: '2026-01-01T00:00:00Z',
  last_used_at: '2026-05-01T00:00:00Z',
  expires_at: null,
  revoked_at: null,
}

const MOCK_CREATE_RESPONSE: PatCreateResponse = {
  ...MOCK_TOKEN,
  id: 'tok-new',
  name: 'My new token',
  prefix: 'sfbl_',
  last4: 'zzzz',
  token: 'sfbl_supersecretplaintexttoken1234567890',
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderPage(user = MOCK_USER_WITH_PERMISSION) {
  const qc = makeQueryClient()
  // Seed apiFetch mock for the AuthProvider bootstrap calls:
  //   call 1 → GET /api/runtime
  //   call 2 → GET /api/me
  vi.mocked(client.apiFetch)
    .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
    .mockResolvedValueOnce(user)

  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <MemoryRouter>
              <SettingsTokensPage />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('SettingsTokensPage', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('auth_token', 'test-token')
    vi.spyOn(client, 'apiFetch')
    vi.spyOn(endpoints.patApi, 'list')
    vi.spyOn(endpoints.patApi, 'create')
    vi.spyOn(endpoints.patApi, 'revoke')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  // ── Permission gating ─────────────────────────────────────────────────────

  it('renders the PAT section when the user holds tokens.manage', async () => {
    vi.mocked(endpoints.patApi.list).mockResolvedValue([])

    renderPage(MOCK_USER_WITH_PERMISSION)

    await waitFor(() => {
      expect(screen.getByText('Personal Access Tokens')).toBeInTheDocument()
    })
    // Tab content should be visible (the empty-state text)
    await waitFor(() => {
      expect(screen.getByText(/no tokens yet/i)).toBeInTheDocument()
    })
  })

  it('shows a permission fallback when the user lacks tokens.manage', async () => {
    renderPage(MOCK_USER_WITHOUT_PERMISSION)

    await waitFor(() => {
      expect(
        screen.getByText(/you do not have permission to manage personal access tokens/i),
      ).toBeInTheDocument()
    })
    // New token button must NOT appear
    expect(screen.queryByTestId('pat-new-button')).not.toBeInTheDocument()
  })

  // ── Token list ────────────────────────────────────────────────────────────

  it('renders listed tokens in a table', async () => {
    vi.mocked(endpoints.patApi.list).mockResolvedValue([MOCK_TOKEN])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('CI pipeline')).toBeInTheDocument()
    })
    // Prefix…last4 column
    expect(screen.getByText(/sfbl_…abcd/)).toBeInTheDocument()
    // Active badge
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  // ── Create flow + copy-once modal ─────────────────────────────────────────

  it('shows the copy-once modal with plaintext and warning after creating a token', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.patApi.list).mockResolvedValue([])
    vi.mocked(endpoints.patApi.create).mockResolvedValue(MOCK_CREATE_RESPONSE)

    renderPage()

    // Open create modal
    const newButton = await screen.findByTestId('pat-new-button')
    await user.click(newButton)

    // Fill name
    const nameInput = await screen.findByLabelText(/token name/i)
    await user.type(nameInput, 'My new token')

    // Submit
    await user.click(screen.getByRole('button', { name: /generate token/i }))

    // Copy-once modal should appear
    await waitFor(() => {
      expect(screen.getByTestId('pat-plaintext')).toBeInTheDocument()
    })

    // Plaintext is shown
    expect(screen.getByTestId('pat-plaintext')).toHaveTextContent(
      'sfbl_supersecretplaintexttoken1234567890',
    )

    // "Not shown again" warning is visible
    expect(
      screen.getByText(/this token will not be shown again/i),
    ).toBeInTheDocument()

    // Copy button is present
    expect(screen.getByTestId('pat-copy-button')).toBeInTheDocument()
  })

  it('discards the plaintext when the copy-once modal is closed', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.patApi.list).mockResolvedValue([])
    vi.mocked(endpoints.patApi.create).mockResolvedValue(MOCK_CREATE_RESPONSE)

    renderPage()

    await user.click(await screen.findByTestId('pat-new-button'))
    await user.type(await screen.findByLabelText(/token name/i), 'My new token')
    await user.click(screen.getByRole('button', { name: /generate token/i }))

    // Wait for copy-once modal
    await waitFor(() => {
      expect(screen.getByTestId('pat-plaintext')).toBeInTheDocument()
    })

    // Dismiss modal
    const doneButton = screen.getByTestId('pat-copy-once-done')
    await user.click(doneButton)

    // Modal is gone — plaintext is no longer in the DOM
    await waitFor(() => {
      expect(screen.queryByTestId('pat-plaintext')).not.toBeInTheDocument()
    })
  })

  // ── Revoke flow ───────────────────────────────────────────────────────────

  it('revokes a token after clicking Revoke and confirming', async () => {
    const user = userEvent.setup()
    vi.mocked(endpoints.patApi.list).mockResolvedValue([MOCK_TOKEN])
    vi.mocked(endpoints.patApi.revoke).mockResolvedValue(undefined)

    renderPage()

    // Click the revoke button in the table
    const revokeButton = await screen.findByTestId(`pat-revoke-${MOCK_TOKEN.id}`)
    await user.click(revokeButton)

    // Confirmation dialog should appear
    const confirmButton = await screen.findByTestId('pat-revoke-confirm')
    await user.click(confirmButton)

    await waitFor(() => {
      expect(endpoints.patApi.revoke).toHaveBeenCalledWith(MOCK_TOKEN.id)
    })
  })
})
