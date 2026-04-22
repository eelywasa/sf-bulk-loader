/**
 * Permission matrix integration tests (SFBL-197).
 *
 * For each profile (admin, operator, viewer, desktop) × key pages and affordances,
 * mount the relevant component with AuthContext seeded with that profile's
 * permission set and assert which UI elements are visible / hidden.
 *
 * Scope:
 * - Connections page: "Create Connection" button gated by connections.manage
 * - Plans page: "Create Plan" button gated by plans.manage
 * - ProtectedRoute redirects: system.settings routes redirect to /403 for non-admin
 * - Runs abort: PermissionGate on abort button (runs.abort)
 * - Files preview: files.view_contents gate on preview link
 *
 * API calls are mocked via vitest mocks and apiFetch spies (same pattern as
 * existing page tests). Auth is seeded by mocking /api/runtime + /api/auth/me.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import { ToastProvider } from '../components/ui/Toast'
import ProtectedRoute from '../components/ProtectedRoute'
import PermissionGate from '../components/PermissionGate'
import * as client from '../api/client'
import type { RuntimeConfig, UserResponse } from '../api/types'

// ──────────────────────────────────────────────────────────────────────────────
// Profile permission sets (mirrors spec §5.2 and migration 0021)
// ──────────────────────────────────────────────────────────────────────────────

const ADMIN_PERMISSIONS = [
  'connections.view',
  'connections.view_credentials',
  'connections.manage',
  'plans.view',
  'plans.manage',
  'runs.view',
  'runs.execute',
  'runs.abort',
  'files.view',
  'files.view_contents',
  'users.manage',
  'system.settings',
]

const OPERATOR_PERMISSIONS = [
  'connections.view',
  'plans.view',
  'plans.manage',
  'runs.view',
  'runs.execute',
  'runs.abort',
  'files.view',
  'files.view_contents',
]

const VIEWER_PERMISSIONS = [
  'connections.view',
  'plans.view',
  'runs.view',
  'files.view',
]

// Desktop: all permissions (virtual user, auth_mode=none)
const DESKTOP_PERMISSIONS = [...ADMIN_PERMISSIONS]

const PROFILE_PERMISSIONS: Record<string, string[]> = {
  admin: ADMIN_PERMISSIONS,
  operator: OPERATOR_PERMISSIONS,
  viewer: VIEWER_PERMISSIONS,
  desktop: DESKTOP_PERMISSIONS,
}

// ──────────────────────────────────────────────────────────────────────────────
// Mock helpers
// ──────────────────────────────────────────────────────────────────────────────

const MOCK_RUNTIME_HOSTED: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_RUNTIME_DESKTOP: RuntimeConfig = {
  auth_mode: 'none',
  app_distribution: 'desktop',
  transport_mode: 'local',
  input_storage_mode: 'local',
}

function makeUser(profileName: string): UserResponse {
  return {
    id: `user-${profileName}`,
    email: `${profileName}@example.com`,
    display_name: profileName,
    is_admin: profileName === 'admin' || profileName === 'desktop',
    profile: { name: profileName },
    permissions: PROFILE_PERMISSIONS[profileName] ?? [],
  }
}

function seedAuth(profileName: string) {
  const isDesktop = profileName === 'desktop'
  localStorage.setItem('auth_token', isDesktop ? '' : 'test-token')
  const runtime = isDesktop ? MOCK_RUNTIME_DESKTOP : MOCK_RUNTIME_HOSTED
  const user = makeUser(profileName)

  vi.spyOn(client, 'apiFetch').mockImplementation((url: string) => {
    if (url === '/api/runtime') return Promise.resolve(runtime)
    return Promise.resolve(user) // /api/auth/me
  })
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

// ──────────────────────────────────────────────────────────────────────────────
// Render helpers
// ──────────────────────────────────────────────────────────────────────────────

/** Render a PermissionGate component with the given profile's AuthContext. */
function renderGate(
  profileName: string,
  gateProps: React.ComponentProps<typeof PermissionGate>,
) {
  seedAuth(profileName)
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={makeQueryClient()}>
          <PermissionGate {...gateProps} />
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

/** Render a ProtectedRoute for a given route, seeded with the profile's permissions. */
function renderProtectedRoute(profileName: string, permission: string) {
  seedAuth(profileName)
  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={makeQueryClient()}>
          <MemoryRouter initialEntries={['/target']}>
            <Routes>
              <Route
                path="/target"
                element={
                  <ProtectedRoute permission={permission}>
                    <div data-testid="protected-content">Protected</div>
                  </ProtectedRoute>
                }
              />
              <Route path="/403" element={<div data-testid="forbidden-page">403</div>} />
              <Route path="/login" element={<div data-testid="login-page">Login</div>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// connections.manage — "Create Connection" button
// ──────────────────────────────────────────────────────────────────────────────

describe('connections.manage — Create Connection button', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — button is visible', async () => {
    renderGate('admin', {
      permission: 'connections.manage',
      children: <button>Create Connection</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Connection' })).toBeInTheDocument()
    })
  })

  it('operator — button is hidden', async () => {
    renderGate('operator', {
      permission: 'connections.manage',
      children: <button>Create Connection</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Create Connection' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('viewer — button is hidden', async () => {
    renderGate('viewer', {
      permission: 'connections.manage',
      children: <button>Create Connection</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Create Connection' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('desktop — button is visible (all permissions)', async () => {
    renderGate('desktop', {
      permission: 'connections.manage',
      children: <button>Create Connection</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Connection' })).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// plans.manage — "Create Plan" button
// ──────────────────────────────────────────────────────────────────────────────

describe('plans.manage — Create Plan button', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — button is visible', async () => {
    renderGate('admin', {
      permission: 'plans.manage',
      children: <button>Create Plan</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Plan' })).toBeInTheDocument()
    })
  })

  it('operator — button is visible', async () => {
    renderGate('operator', {
      permission: 'plans.manage',
      children: <button>Create Plan</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Plan' })).toBeInTheDocument()
    })
  })

  it('viewer — button is hidden', async () => {
    renderGate('viewer', {
      permission: 'plans.manage',
      children: <button>Create Plan</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Create Plan' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('desktop — button is visible', async () => {
    renderGate('desktop', {
      permission: 'plans.manage',
      children: <button>Create Plan</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Plan' })).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// runs.abort — "Abort" button
// ──────────────────────────────────────────────────────────────────────────────

describe('runs.abort — Abort button', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — button is visible', async () => {
    renderGate('admin', {
      permission: 'runs.abort',
      children: <button>Abort Run</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Abort Run' })).toBeInTheDocument()
    })
  })

  it('operator — button is visible', async () => {
    renderGate('operator', {
      permission: 'runs.abort',
      children: <button>Abort Run</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Abort Run' })).toBeInTheDocument()
    })
  })

  it('viewer — button is hidden', async () => {
    renderGate('viewer', {
      permission: 'runs.abort',
      children: <button>Abort Run</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Abort Run' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('desktop — button is visible', async () => {
    renderGate('desktop', {
      permission: 'runs.abort',
      children: <button>Abort Run</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Abort Run' })).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// runs.execute — "Trigger Run" button
// ──────────────────────────────────────────────────────────────────────────────

describe('runs.execute — Trigger Run button', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — button is visible', async () => {
    renderGate('admin', {
      permission: 'runs.execute',
      children: <button>Trigger Run</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Trigger Run' })).toBeInTheDocument()
    })
  })

  it('operator — button is visible', async () => {
    renderGate('operator', {
      permission: 'runs.execute',
      children: <button>Trigger Run</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Trigger Run' })).toBeInTheDocument()
    })
  })

  it('viewer — button is hidden', async () => {
    renderGate('viewer', {
      permission: 'runs.execute',
      children: <button>Trigger Run</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Trigger Run' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('desktop — button is visible', async () => {
    renderGate('desktop', {
      permission: 'runs.execute',
      children: <button>Trigger Run</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Trigger Run' })).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// files.view_contents — Preview / Download affordances
// ──────────────────────────────────────────────────────────────────────────────

describe('files.view_contents — Preview button', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — preview button is visible', async () => {
    renderGate('admin', {
      permission: 'files.view_contents',
      children: <button>Preview File</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview File' })).toBeInTheDocument()
    })
  })

  it('operator — preview button is visible', async () => {
    renderGate('operator', {
      permission: 'files.view_contents',
      children: <button>Preview File</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview File' })).toBeInTheDocument()
    })
  })

  it('viewer — preview button is hidden', async () => {
    renderGate('viewer', {
      permission: 'files.view_contents',
      children: <button>Preview File</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Preview File' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('desktop — preview button is visible', async () => {
    renderGate('desktop', {
      permission: 'files.view_contents',
      children: <button>Preview File</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview File' })).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// system.settings — ProtectedRoute redirects non-admin to /403
// ──────────────────────────────────────────────────────────────────────────────

describe('system.settings — ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — renders protected content', async () => {
    renderProtectedRoute('admin', 'system.settings')
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })

  it('operator — redirected to /403', async () => {
    renderProtectedRoute('operator', 'system.settings')
    await waitFor(() => {
      expect(screen.getByTestId('forbidden-page')).toBeInTheDocument()
      expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    })
  })

  it('viewer — redirected to /403', async () => {
    renderProtectedRoute('viewer', 'system.settings')
    await waitFor(() => {
      expect(screen.getByTestId('forbidden-page')).toBeInTheDocument()
      expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    })
  })

  it('desktop — renders protected content (all permissions)', async () => {
    renderProtectedRoute('desktop', 'system.settings')
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// connections.view_credentials — NOT exposed to operator/viewer
// ──────────────────────────────────────────────────────────────────────────────

describe('connections.view_credentials — credentials visibility affordance', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — credential section is visible', async () => {
    renderGate('admin', {
      permission: 'connections.view_credentials',
      children: <div data-testid="credentials-section">Credentials</div>,
    })
    await waitFor(() => {
      expect(screen.getByTestId('credentials-section')).toBeInTheDocument()
    })
  })

  it('operator — credential section is hidden', async () => {
    renderGate('operator', {
      permission: 'connections.view_credentials',
      children: <div data-testid="credentials-section">Credentials</div>,
    })
    await waitFor(() => {
      expect(screen.queryByTestId('credentials-section')).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('viewer — credential section is hidden', async () => {
    renderGate('viewer', {
      permission: 'connections.view_credentials',
      children: <div data-testid="credentials-section">Credentials</div>,
    })
    await waitFor(() => {
      expect(screen.queryByTestId('credentials-section')).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// users.manage — admin-only
// ──────────────────────────────────────────────────────────────────────────────

describe('users.manage — admin-only affordance', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('admin — user management visible', async () => {
    renderGate('admin', {
      permission: 'users.manage',
      children: <button>Manage Users</button>,
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Manage Users' })).toBeInTheDocument()
    })
  })

  it('operator — user management hidden', async () => {
    renderGate('operator', {
      permission: 'users.manage',
      children: <button>Manage Users</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Manage Users' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('viewer — user management hidden', async () => {
    renderGate('viewer', {
      permission: 'users.manage',
      children: <button>Manage Users</button>,
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Manage Users' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// ProtectedRoute — connections.view
// ──────────────────────────────────────────────────────────────────────────────

describe('connections.view — ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it.each(['admin', 'operator', 'viewer'])('%s can access connections route', async (profile) => {
    renderProtectedRoute(profile, 'connections.view')
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// ProtectedRoute — plans.view
// ──────────────────────────────────────────────────────────────────────────────

describe('plans.view — ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it.each(['admin', 'operator', 'viewer'])('%s can access plans route', async (profile) => {
    renderProtectedRoute(profile, 'plans.view')
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// ProtectedRoute — runs.view
// ──────────────────────────────────────────────────────────────────────────────

describe('runs.view — ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it.each(['admin', 'operator', 'viewer'])('%s can access runs route', async (profile) => {
    renderProtectedRoute(profile, 'runs.view')
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// ProtectedRoute — files.view
// ──────────────────────────────────────────────────────────────────────────────

describe('files.view — ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it.each(['admin', 'operator', 'viewer'])('%s can access files route', async (profile) => {
    renderProtectedRoute(profile, 'files.view')
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })
})

// ──────────────────────────────────────────────────────────────────────────────
// plans.manage ProtectedRoute — viewer blocked
// ──────────────────────────────────────────────────────────────────────────────

describe('plans.manage — ProtectedRoute (viewer blocked)', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('viewer — redirected to /403 for plans.manage route', async () => {
    renderProtectedRoute('viewer', 'plans.manage')
    await waitFor(() => {
      expect(screen.getByTestId('forbidden-page')).toBeInTheDocument()
    })
  })

  it('operator — allowed through plans.manage route', async () => {
    renderProtectedRoute('operator', 'plans.manage')
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })
})
