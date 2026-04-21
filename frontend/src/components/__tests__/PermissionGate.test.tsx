/**
 * Tests for <PermissionGate> component.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import * as client from '../../api/client'
import type { RuntimeConfig, UserResponse } from '../../api/types'
import PermissionGate from '../PermissionGate'

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

function makeUser(permissions: string[]): UserResponse {
  return {
    id: 'u1',
    username: 'testuser',
    email: 'test@example.com',
    display_name: 'Test',
    profile: { name: 'admin' },
    permissions,
  }
}

function renderGate(
  gateProps: React.ComponentProps<typeof PermissionGate>,
  permissions: string[],
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })

  vi.mocked(client.apiFetch)
    .mockResolvedValueOnce(MOCK_RUNTIME)
    .mockResolvedValueOnce(makeUser(permissions))

  localStorage.setItem('auth_token', 'test-token')

  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <PermissionGate {...gateProps} />
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('PermissionGate', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders children when user has the required permission', async () => {
    renderGate(
      { permission: 'connections.manage', children: <button>Create Connection</button> },
      ['connections.manage', 'connections.view'],
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Connection' })).toBeInTheDocument()
    })
  })

  it('renders fallback (null by default) when user lacks the permission', async () => {
    renderGate(
      { permission: 'connections.manage', children: <button>Create Connection</button> },
      ['connections.view'],
    )

    // Give auth context time to bootstrap
    await waitFor(() => {
      // The button should never appear
      expect(screen.queryByRole('button', { name: 'Create Connection' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('renders custom fallback when specified', async () => {
    renderGate(
      {
        permission: 'connections.manage',
        fallback: <p>No access</p>,
        children: <button>Create Connection</button>,
      },
      ['connections.view'],
    )

    await waitFor(() => {
      expect(screen.getByText('No access')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Create Connection' })).not.toBeInTheDocument()
    })
  })

  it('renders children when user holds at least one of "any" keys (OR logic)', async () => {
    renderGate(
      {
        any: ['runs.execute', 'runs.abort'],
        children: <button>Run Action</button>,
      },
      ['runs.abort'],
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Run Action' })).toBeInTheDocument()
    })
  })

  it('hides children when user holds none of the "any" keys', async () => {
    renderGate(
      {
        any: ['runs.execute', 'runs.abort'],
        children: <button>Run Action</button>,
      },
      ['runs.view'],
    )

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Run Action' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('renders children when user holds all of "all" keys (AND logic)', async () => {
    renderGate(
      {
        all: ['connections.view', 'connections.manage'],
        children: <button>Full Access</button>,
      },
      ['connections.view', 'connections.manage'],
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Full Access' })).toBeInTheDocument()
    })
  })

  it('hides children when user is missing one of the "all" keys', async () => {
    renderGate(
      {
        all: ['connections.view', 'connections.manage'],
        children: <button>Full Access</button>,
      },
      ['connections.view'],
    )

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Full Access' })).not.toBeInTheDocument()
    }, { timeout: 1000 })
  })

  it('renders children unconditionally when no permission props are specified', async () => {
    renderGate(
      { children: <button>Always Visible</button> },
      [],
    )

    // Since no props specified, should always render
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Always Visible' })).toBeInTheDocument()
    })
  })
})
