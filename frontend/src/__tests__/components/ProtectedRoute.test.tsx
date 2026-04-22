import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import * as client from '../../api/client'
import ProtectedRoute from '../../components/ProtectedRoute'
import type { RuntimeConfig, UserResponse } from '../../api/types'

const MOCK_USER: UserResponse = {
  id: '1',
  email: 'test@example.com',
  display_name: null,
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

function renderProtected(initialPath: string) {
  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/login" element={<div>Login page</div>} />
            <Route
              path="/protected"
              element={
                <ProtectedRoute>
                  <div>Protected content</div>
                </ProtectedRoute>
              }
            />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('redirects to /login when not authenticated', async () => {
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)

    renderProtected('/protected')
    await waitFor(() => {
      expect(screen.getByText('Login page')).toBeInTheDocument()
    })
  })

  it('renders protected content when authenticated', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockResolvedValueOnce(MOCK_USER)

    renderProtected('/protected')
    await waitFor(() => {
      expect(screen.getByText('Protected content')).toBeInTheDocument()
    })
  })

  it('shows loading indicator while bootstrapping', () => {
    localStorage.setItem('auth_token', 'test-token')
    let resolveRuntime: (v: unknown) => void
    vi.mocked(client.apiFetch).mockReturnValueOnce(new Promise((r) => { resolveRuntime = r }))

    renderProtected('/protected')

    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
    expect(screen.queryByText('Protected content')).not.toBeInTheDocument()

    // Cleanup
    resolveRuntime!(MOCK_RUNTIME_LOCAL)
  })

  it('redirects to /login after bootstrap with invalid token', async () => {
    localStorage.setItem('auth_token', 'expired-token')
    vi.mocked(client.apiFetch)
      .mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
      .mockRejectedValueOnce(new Error('Unauthorized'))

    renderProtected('/protected')
    await waitFor(() => {
      expect(screen.getByText('Login page')).toBeInTheDocument()
    })
  })

  it('includes next param when redirecting unauthenticated users', async () => {
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)

    const { container } = render(
      <ThemeProvider>
        <AuthProvider>
          <MemoryRouter initialEntries={['/protected?foo=bar']}>
            <Routes>
              <Route
                path="/login"
                element={<div data-testid="login">Login page</div>}
              />
              <Route
                path="/protected"
                element={
                  <ProtectedRoute>
                    <div>Protected content</div>
                  </ProtectedRoute>
                }
              />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </ThemeProvider>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('login')).toBeInTheDocument()
    })
    expect(container.textContent).not.toContain('Protected content')
  })

  describe('desktop profile (auth_mode=none)', () => {
    it('renders protected content without a token', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)
      // No localStorage token set

      renderProtected('/protected')
      await waitFor(() => {
        expect(screen.getByText('Protected content')).toBeInTheDocument()
      })
    })

    it('does not redirect to /login', async () => {
      vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)

      renderProtected('/protected')
      await waitFor(() => {
        expect(screen.getByText('Protected content')).toBeInTheDocument()
      })
      expect(screen.queryByText('Login page')).not.toBeInTheDocument()
    })
  })
})
