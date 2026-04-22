import React from 'react'
import { render, type RenderOptions } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../components/ui/Toast'
import { AuthProvider } from '../context/AuthContext'
import { ThemeProvider } from '../context/ThemeContext'
import * as client from '../api/client'
import type { RuntimeConfig, UserResponse } from '../api/types'
import { vi } from 'vitest'

/** Full admin permission set — used as the default in renderWithProviders. */
export const ADMIN_PERMISSIONS: string[] = [
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

const DEFAULT_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

/** Build a UserResponse for the given set of permission keys. */
export function makeTestUser(permissions: string[] = ADMIN_PERMISSIONS): UserResponse {
  return {
    id: 'test-user',
    email: 'test@example.com',
    display_name: 'Test User',
    is_admin: permissions.includes('system.settings'),
    profile: { name: permissions.includes('system.settings') ? 'admin' : 'operator' },
    permissions,
  }
}

interface ProvidersOptions {
  /** Permission keys to seed the auth context with. Defaults to all admin permissions. */
  permissions?: string[]
  /** Override the runtime config returned from /api/runtime. */
  runtimeConfig?: RuntimeConfig
}

function AllProviders({
  children,
  permissions = ADMIN_PERMISSIONS,
  runtimeConfig = DEFAULT_RUNTIME,
}: { children: React.ReactNode } & ProvidersOptions) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  // Seed apiFetch mock so AuthProvider gets the right runtime config and user
  // (only if not already mocked by the test itself)
  React.useEffect(() => {
    // This is intentionally a no-op here — the mock is set up via beforeEach in AllProviders
  }, [])

  return (
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <MemoryRouter>{children}</MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>
  )
}

/**
 * Render a component with all app providers.
 *
 * By default, the AuthContext is seeded with full admin permissions so that
 * permission gates do not hide any UI elements in tests. Pass `permissions`
 * to override with a specific set.
 *
 * The function sets up vi.spyOn(client, 'apiFetch') before rendering so that
 * AuthProvider's bootstrap calls succeed. If your test already mocks apiFetch,
 * call vi.restoreAllMocks() / vi.clearAllMocks() in your afterEach.
 */
function renderWithProviders(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, 'wrapper'> & ProvidersOptions,
) {
  const { permissions = ADMIN_PERMISSIONS, runtimeConfig = DEFAULT_RUNTIME, ...renderOptions } = options ?? {}

  localStorage.setItem('auth_token', 'test-token')

  // Mock apiFetch to return the seeded runtime + user for auth bootstrap
  const mockUser = makeTestUser(permissions)
  vi.spyOn(client, 'apiFetch').mockImplementation((url: string) => {
    if (url === '/api/runtime') return Promise.resolve(runtimeConfig)
    return Promise.resolve(mockUser)
  })

  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <AllProviders permissions={permissions} runtimeConfig={runtimeConfig}>
      {children}
    </AllProviders>
  )

  return render(ui, { wrapper: Wrapper, ...renderOptions })
}

// Re-export everything from testing-library so tests can use one import
export * from '@testing-library/react'
export { renderWithProviders as render }
