/**
 * Tests for AdminUsersPage (SFBL-201).
 *
 * Covers:
 *  - List renders users from API
 *  - Invite flow shows one-time token reveal modal on success
 *  - Delete calls correct endpoint
 *  - 409 error is surfaced in the confirm dialog
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import { ToastProvider } from '../../components/ui/Toast'
import * as client from '../../api/client'
import AdminUsersPage from '../AdminUsersPage'
import ForbiddenPage from '../ForbiddenPage'
import type {
  RuntimeConfig,
  UserResponse,
  AdminUser,
  AdminUserListResponse,
  ProfileListItem,
} from '../../api/types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_ME: UserResponse = {
  id: 'admin-1',
  email: 'admin@example.com',
  display_name: 'Admin',
  is_active: true,
  is_admin: true,
  permissions: ['users.manage', 'system.settings'],
}

const MOCK_USERS: AdminUser[] = [
  {
    id: 'u1',
    email: 'alice@example.com',
    display_name: 'Alice',
    status: 'active',
    is_admin: false,
    profile: { id: 'p1', name: 'operator' },
    permissions: [],
    invited_by: null,
    invited_at: null,
    last_login_at: '2026-04-01T10:00:00Z',
  },
  {
    id: 'u2',
    email: 'bob@example.com',
    display_name: null,
    status: 'invited',
    is_admin: false,
    profile: { id: 'p2', name: 'viewer' },
    permissions: [],
    invited_by: 'admin-1',
    invited_at: '2026-04-15T12:00:00Z',
    last_login_at: null,
  },
]

const MOCK_LIST_RESPONSE: AdminUserListResponse = {
  items: MOCK_USERS,
  total: 2,
  page: 1,
  page_size: 100,
}

const MOCK_PROFILES: ProfileListItem[] = [
  { id: 'p1', name: 'admin', description: 'Administrator' },
  { id: 'p2', name: 'operator', description: 'Operator' },
  { id: 'p3', name: 'viewer', description: 'Viewer' },
]

// ─── Module-level mock for adminUsersApi ─────────────────────────────────────

vi.mock('../../api/endpoints', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api/endpoints')>()
  return {
    ...mod,
    adminUsersApi: {
      list: vi.fn(),
      listProfiles: vi.fn(),
      invite: vi.fn(),
      update: vi.fn(),
      unlock: vi.fn(),
      deactivate: vi.fn(),
      reactivate: vi.fn(),
      resetPassword: vi.fn(),
      resendInvite: vi.fn(),
      delete: vi.fn(),
      get: vi.fn(),
    },
  }
})

// ─── Setup helpers ─────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

function renderPage() {
  const queryClient = makeQueryClient()
  return {
    queryClient,
    ...render(
      <MemoryRouter initialEntries={['/admin/users']}>
        <QueryClientProvider client={queryClient}>
          <ThemeProvider>
            <AuthProvider>
              <ToastProvider>
                <Routes>
                  <Route path="/403" element={<ForbiddenPage />} />
                  <Route path="/login" element={<div>Login page</div>} />
                  <Route path="/admin/users" element={<AdminUsersPage />} />
                </Routes>
              </ToastProvider>
            </AuthProvider>
          </ThemeProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    ),
  }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('AdminUsersPage', () => {
  let adminUsersApi: typeof import('../../api/endpoints')['adminUsersApi']

  beforeEach(async () => {
    vi.resetAllMocks()

    // Simulate authenticated session
    localStorage.setItem('auth_token', 'test-admin-token')

    // Mock the bootstrap apiFetch calls (runtime + /api/auth/me)
    vi.spyOn(client, 'apiFetch')
      .mockResolvedValueOnce(MOCK_RUNTIME)
      .mockResolvedValueOnce(MOCK_ME)

    // Resolve the mocked adminUsersApi
    const mod = await import('../../api/endpoints')
    adminUsersApi = mod.adminUsersApi

    // Default happy-path mocks
    vi.mocked(adminUsersApi.list).mockResolvedValue(MOCK_LIST_RESPONSE)
    vi.mocked(adminUsersApi.listProfiles).mockResolvedValue(MOCK_PROFILES)
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders the page heading and invite button', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'User Management' })).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Invite User' })).toBeInTheDocument()
  })

  it('renders list of users from API', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    })
    expect(screen.getByText('bob@example.com')).toBeInTheDocument()
    expect(screen.getByText('Alice')).toBeInTheDocument()
    // Status badges (there's also a filter chip named "Active", so use getAllByText)
    expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Invited').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('2 users')).toBeInTheDocument()
  })

  it('opens invite modal when Invite User is clicked', async () => {
    const user = userEvent.setup()
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Invite User' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Invite User' }))

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
    // Dialog title (h2) plus the page button both say "Invite User"; check for dialog heading specifically
    expect(screen.getByRole('heading', { name: 'Invite User' })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send Invitation' })).toBeInTheDocument()
  })

  it('shows invite token reveal modal on successful invite', async () => {
    vi.mocked(adminUsersApi.invite).mockResolvedValue({
      user: { ...MOCK_USERS[0], email: 'newuser@example.com', status: 'invited' },
      raw_token: 'abc123secrettoken',
      expires_at: '2026-04-29T12:00:00Z',
    })

    const user = userEvent.setup()
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Invite User' })).toBeInTheDocument()
    })

    // Open invite modal
    await user.click(screen.getByRole('button', { name: 'Invite User' }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    // Fill in form
    await user.type(screen.getByLabelText(/email/i), 'newuser@example.com')
    const profileSelect = screen.getByLabelText(/profile/i)
    await user.selectOptions(profileSelect, 'p2')

    // Submit
    await user.click(screen.getByRole('button', { name: 'Send Invitation' }))

    // Reveal modal should appear with the invite link + raw token
    await waitFor(() => {
      expect(screen.getByText('Invitation Link')).toBeInTheDocument()
    })
    expect(
      screen.getByText(/\/invite\/accept\?token=abc123secrettoken/),
    ).toBeInTheDocument()
    expect(screen.getByText('abc123secrettoken')).toBeInTheDocument()
    expect(screen.getByText(/shown once/i)).toBeInTheDocument()
    // Must confirm explicitly — no auto-dismiss
    expect(screen.getByRole('button', { name: /I've saved this/i })).toBeInTheDocument()
  })

  it('calls delete endpoint when Delete action is confirmed', async () => {
    vi.mocked(adminUsersApi.delete).mockResolvedValue(undefined)

    const user = userEvent.setup()
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    })

    // Click the inline Delete button on alice's row (first "Delete" on the page)
    const deleteButtonsRow = screen.getAllByRole('button', { name: 'Delete' })
    await user.click(deleteButtonsRow[0])

    // Confirm dialog appears
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      expect(screen.getByText('Delete User')).toBeInTheDocument()
    })

    // Click the "Delete" confirm button in the dialog footer
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' })
    await user.click(deleteButtons[deleteButtons.length - 1])

    await waitFor(() => {
      expect(adminUsersApi.delete).toHaveBeenCalledWith('u1')
    })
  })

  it('surfaces 409 error in the confirm dialog', async () => {
    const { ApiError } = await import('../../api/client')
    vi.mocked(adminUsersApi.deactivate).mockRejectedValue(
      new ApiError({
        status: 409,
        message: 'Cannot deactivate the last active administrator.',
      }),
    )

    const user = userEvent.setup()
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    })

    // Click the inline Deactivate button on alice's row
    const deactivateBtns = screen.getAllByRole('button', { name: 'Deactivate' })
    await user.click(deactivateBtns[0])

    await waitFor(() => {
      expect(screen.getByText('Deactivate User')).toBeInTheDocument()
    })

    // Confirm in the modal footer
    const confirmBtns = screen.getAllByRole('button', { name: 'Deactivate' })
    await user.click(confirmBtns[confirmBtns.length - 1])

    await waitFor(() => {
      expect(
        screen.getByText('Cannot deactivate the last active administrator.'),
      ).toBeInTheDocument()
    })
  })
})
