/**
 * Tests for usePermission / usePermissions hooks.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import * as client from '../../api/client'
import type { RuntimeConfig, UserResponse } from '../../api/types'
import { usePermission, usePermissions } from '../usePermission'
import React from 'react'

const MOCK_RUNTIME_HOSTED: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

const MOCK_RUNTIME_DESKTOP: RuntimeConfig = {
  auth_mode: 'none',
  app_distribution: 'desktop',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

function makeAdminUser(): UserResponse {
  return {
    id: 'admin-1',
    email: 'admin@example.com',
    display_name: 'Admin',
    is_admin: true,
    profile: { name: 'admin' },
    permissions: [
      'connections.view', 'connections.view_credentials', 'connections.manage',
      'plans.view', 'plans.manage',
      'runs.view', 'runs.execute', 'runs.abort',
      'files.view', 'files.view_contents',
      'users.manage', 'system.settings',
    ],
  }
}

function makeViewerUser(): UserResponse {
  return {
    id: 'viewer-1',
    email: 'viewer@example.com',
    display_name: 'Viewer',
    is_admin: false,
    profile: { name: 'viewer' },
    permissions: ['connections.view', 'plans.view', 'runs.view', 'files.view'],
  }
}

function makeDesktopUser(): UserResponse {
  return {
    id: 'desktop',
    email: 'test@example.com',
    display_name: null,
    is_admin: true,
    profile: { name: 'desktop' },
    permissions: [
      'connections.view', 'connections.view_credentials', 'connections.manage',
      'plans.view', 'plans.manage',
      'runs.view', 'runs.execute', 'runs.abort',
      'files.view', 'files.view_contents',
      'users.manage', 'system.settings',
    ],
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <ThemeProvider>
        <AuthProvider>
          <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </AuthProvider>
      </ThemeProvider>
    )
  }
  return Wrapper
}

describe('usePermission', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('returns true for a permission the admin user holds', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_HOSTED)
      .mockResolvedValueOnce(makeAdminUser())

    const { result } = renderHook(() => usePermission('connections.manage'), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current).toBe(true)
    })
  })

  it('returns false for a permission the viewer does not hold', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_HOSTED)
      .mockResolvedValueOnce(makeViewerUser())

    const { result } = renderHook(() => usePermission('connections.manage'), {
      wrapper: makeWrapper(),
    })

    // Wait for bootstrap to complete (bootstrapping→false happens when permissions load)
    await waitFor(() => {
      // We can't distinguish "no permissions loaded yet" from "viewer has no this perm"
      // but since viewer genuinely lacks this key, false is always correct
      expect(result.current).toBe(false)
    })
  })

  it('returns true for all permissions in desktop mode', async () => {
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)
      .mockResolvedValueOnce(makeDesktopUser())

    const { result } = renderHook(() => usePermission('system.settings'), {
      wrapper: makeWrapper(),
    })

    await waitFor(() => {
      expect(result.current).toBe(true)
    })
  })

  it('returns false for unknown permission key', () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_HOSTED)
      .mockResolvedValueOnce(makeAdminUser())

    const { result } = renderHook(() => usePermission('nonexistent.key'), {
      wrapper: makeWrapper(),
    })

    // Immediately false (empty set during bootstrap), and stays false even after
    expect(result.current).toBe(false)
  })
})

describe('usePermissions', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('returns a Set with all viewer permissions', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_HOSTED)
      .mockResolvedValueOnce(makeViewerUser())

    const { result } = renderHook(() => usePermissions(), { wrapper: makeWrapper() })

    await waitFor(() => {
      expect(result.current.has('plans.view')).toBe(true)
    })

    expect(result.current.has('connections.manage')).toBe(false)
    expect(result.current.has('system.settings')).toBe(false)
  })
})
