import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthProvider, useAuth } from '../../context/AuthContext'
import * as client from '../../api/client'
import type { UserResponse } from '../../api/types'

const MOCK_USER: UserResponse = {
  id: '1',
  username: 'alice',
  email: null,
  display_name: null,
  role: 'admin',
  is_active: true,
}

// Display component that exposes auth state for assertions
function AuthDisplay() {
  const { token, user, isBootstrapping, login, logout } = useAuth()
  return (
    <div>
      <span data-testid="bootstrapping">{String(isBootstrapping)}</span>
      <span data-testid="token">{token ?? 'none'}</span>
      <span data-testid="username">{user?.username ?? 'none'}</span>
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

  describe('bootstrap with no stored token', () => {
    it('sets isBootstrapping to false without fetching', async () => {
      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(client.apiFetch).not.toHaveBeenCalled()
    })

    it('leaves token and user as empty', async () => {
      renderAuth()
      await waitFor(() => {
        expect(screen.getByTestId('bootstrapping').textContent).toBe('false')
      })
      expect(screen.getByTestId('token').textContent).toBe('none')
      expect(screen.getByTestId('username').textContent).toBe('none')
    })
  })

  describe('bootstrap with a valid stored token', () => {
    it('restores user from /api/auth/me', async () => {
      localStorage.setItem('auth_token', 'stored-token')
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_USER)

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
      vi.mocked(client.apiFetch).mockRejectedValueOnce(new Error('Unauthorized'))

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
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_USER)
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
    it('clears token, user, and localStorage', async () => {
      localStorage.setItem('auth_token', 'existing-token')
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_USER)
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

  describe('useAuth outside provider', () => {
    it('throws when used outside AuthProvider', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      expect(() => render(<AuthDisplay />)).toThrow('useAuth must be used within AuthProvider')
      consoleSpy.mockRestore()
    })
  })
})
