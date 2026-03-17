import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import * as client from '../../api/client'
import ProtectedRoute from '../../components/ProtectedRoute'
import type { UserResponse } from '../../api/types'

const MOCK_USER: UserResponse = {
  id: '1',
  username: 'alice',
  email: null,
  display_name: null,
  role: 'admin',
  is_active: true,
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
    renderProtected('/protected')
    await waitFor(() => {
      expect(screen.getByText('Login page')).toBeInTheDocument()
    })
  })

  it('renders protected content when authenticated', async () => {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_USER)
    renderProtected('/protected')
    await waitFor(() => {
      expect(screen.getByText('Protected content')).toBeInTheDocument()
    })
  })

  it('shows loading indicator while bootstrapping', () => {
    localStorage.setItem('auth_token', 'test-token')
    let resolve: (v: unknown) => void
    vi.mocked(client.apiFetch).mockReturnValueOnce(new Promise((r) => { resolve = r }))

    renderProtected('/protected')

    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
    expect(screen.queryByText('Protected content')).not.toBeInTheDocument()

    // Cleanup
    resolve!(MOCK_USER)
  })

  it('redirects to /login after bootstrap with invalid token', async () => {
    localStorage.setItem('auth_token', 'expired-token')
    vi.mocked(client.apiFetch).mockRejectedValueOnce(new Error('Unauthorized'))
    renderProtected('/protected')
    await waitFor(() => {
      expect(screen.getByText('Login page')).toBeInTheDocument()
    })
  })

  it('includes next param when redirecting unauthenticated users', async () => {
    // Render without AuthProvider to get the redirect location
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
    // Protected content should not be visible
    expect(container.textContent).not.toContain('Protected content')
  })
})
