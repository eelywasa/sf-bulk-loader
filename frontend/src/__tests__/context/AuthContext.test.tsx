import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthProvider, useAuth } from '../../context/AuthContext'
import * as client from '../../api/client'
import type { RuntimeConfig, UserResponse } from '../../api/types'

const MOCK_USER: UserResponse = {
  id: '1',
  username: 'alice',
  email: null,
  display_name: null,
  role: 'admin',
  is_active: true,
}

const MOCK_RUNTIME_LOCAL: RuntimeConfig = {
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

// Display component that exposes auth state for assertions
function AuthDisplay() {
  const { token, user, isBootstrapping, authRequired, login, logout } = useAuth()
  return (
    <div>
      <span data-testid="bootstrapping">{String(isBootstrapping)}</span>
      <span data-testid="token">{token ?? 'none'}</span>
      <span data-testid="username">{user?.username ?? 'none'}</span>
      <span data-testid="auth-required">{String(authRequired)}</span>
      <button onClick={() => login('test-token')}>Login</button>
      <button onClick={logout}>Logout</button>
    </div>
  )
}

function renderAuth() {
  return render(
    <AuthProvider>
      <AuthDisplay />
    </AuthProvider>,
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  describe('bootstrap with no stored token (hosted profile)', () => {
    it('sets isBootstrapping to false after runtime config fetch', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      // Only the /api/runtime call — no /api/auth/me when there is no token
      expect(client.apiFetch).toHaveBeenCalledTimes(1)
      expect(client.apiFetch).toHaveBeenCalledWith('/api/runtime')
    })

    it('leaves token and user as empty', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(screen.getByTestId('token').textContent).toBe('none')
      expect(screen.getByTestId('username').textContent).toBe('none')
    })

    it('sets authRequired to true', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(screen.getByTestId('auth-required').textContent).toBe('true')
    })
  })

  describe('bootstrap with a valid stored token', () => {
    it('restores user from /api/auth/me', async () => {
      localStorage.setItem('auth_token', 'stored-token')
      vi.mocked(client.apiFetch)
        .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
        .mockResolvedValueOnce(MOCK_USER)

      renderAuth()

      await waitFor(() => {
        expect(screen.getByTestId('username').textContent).toBe('alice')
      })
      expect(screen.getByTestId('token').textContent).toBe('stored-token')
      expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
    })
  })

  describe('bootstrap with an invalid stored token', () => {
    it('clears token state when /api/auth/me returns 401', async () => {
      localStorage.setItem('auth_token', 'expired-token')
      vi.mocked(client.apiFetch)
        .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
        .mockRejectedValueOnce(new Error('Unauthorized'))

      renderAuth()

      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(screen.getByTestId('token').textContent).toBe('none')
      expect(screen.getByTestId('username').textContent).toBe('none')
    })
  })

  describe('login()', () => {
    it('stores token and sets user', async () => {
      vi.mocked(client.apiFetch)
        .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL) // bootstrap
        .mockResolvedValueOnce(MOCK_USER)           // login → /api/auth/me

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })

      await act(async () => {
        await userEvent.click(screen.getByRole('button', { name: 'Login' }))
      })

      expect(localStorage.getItem('auth_token')).toBe('test-token')
      expect(screen.getByTestId('token').textContent).toBe('test-token')
      expect(screen.getByTestId('username').textContent).toBe('alice')
    })
  })

  describe('logout()', () => {
    it('does not redirect to /login in desktop profile', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)
      const mockLocation = { href: '', pathname: '/' }
      vi.stubGlobal('location', mockLocation)

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })

      await act(async () => {
        await userEvent.click(screen.getByRole('button', { name: 'Logout' }))
      })

      expect(mockLocation.href).toBe('')

      vi.unstubAllGlobals()
    })

    it('clears token, user, and localStorage', async () => {
      localStorage.setItem('auth_token', 'existing-token')
      vi.mocked(client.apiFetch)
        .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
        .mockResolvedValueOnce(MOCK_USER)
      const mockLocation = { href: '', pathname: '/' }
      vi.stubGlobal('location', mockLocation)

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('username').textContent).toBe('alice')
      })

      await act(async () => {
        await userEvent.click(screen.getByRole('button', { name: 'Logout' }))
      })

      expect(localStorage.getItem('auth_token')).toBeNull()
      expect(screen.getByTestId('token').textContent).toBe('none')
      expect(screen.getByTestId('username').textContent).toBe('none')
      expect(mockLocation.href).toBe('/login')

      vi.unstubAllGlobals()
    })
  })

  describe('desktop profile (auth_mode=none)', () => {
    it('sets authRequired to false', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(screen.getByTestId('auth-required').textContent).toBe('false')
    })

    it('completes bootstrap without calling /api/auth/me', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(client.apiFetch).toHaveBeenCalledTimes(1)
      expect(client.apiFetch).toHaveBeenCalledWith('/api/runtime')
    })

    it('does not require a stored token to complete bootstrap', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)
      // No localStorage token set

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(screen.getByTestId('token').textContent).toBe('none')
    })
  })

  describe('runtime config fetch failure', () => {
    it('falls back to requiring auth when /api/runtime is unreachable', async () => {
      vi.mocked(client.apiFetch).mockRejectedValueOnce(new Error('Network error'))

      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(screen.getByTestId('auth-required').textContent).toBe('true')
    })
  })

  describe('useAuth outside provider', () => {
    it('throws when used outside AuthProvider', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      expect(() => render(<AuthDisplay />)).toThrow('useAuth must be used within AuthProvider')
      consoleSpy.mockRestore()
    })
  })
})
