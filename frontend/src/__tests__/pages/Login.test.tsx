import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import * as client from '../../api/client'
import Login from '../../pages/Login'
import type { UserResponse } from '../../api/types'

const MOCK_USER: UserResponse = {
  id: '1',
  username: 'alice',
  email: null,
  display_name: null,
  role: 'admin',
  is_active: true,
}

function renderLogin(initialPath = '/login') {
  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<div>Dashboard</div>} />
            <Route path="/plans" element={<div>Plans</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('Login page', () => {
  beforeEach(() => {
    localStorage.clear()
    // Spy on apiPost (called directly by Login.tsx) and apiFetch
    // (called by AuthContext.login → GET /api/auth/me)
    vi.spyOn(client, 'apiPost')
    vi.spyOn(client, 'apiFetch')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders username and password fields', () => {
    renderLogin()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
  })

  it('renders sign in button', () => {
    renderLogin()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows the Bulk Loader brand', () => {
    renderLogin()
    expect(screen.getByText('Bulk Loader')).toBeInTheDocument()
  })

  it('calls login API and navigates to / on success', async () => {
    const user = userEvent.setup()
    vi.mocked(client.apiPost).mockResolvedValueOnce({
      access_token: 'new-token',
      token_type: 'bearer',
      expires_in: 3600,
    })
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_USER)

    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'secret')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
    })
    expect(localStorage.getItem('auth_token')).toBe('new-token')
  })

  it('redirects to ?next param after login', async () => {
    const user = userEvent.setup()
    vi.mocked(client.apiPost).mockResolvedValueOnce({
      access_token: 'tok',
      token_type: 'bearer',
      expires_in: 3600,
    })
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_USER)

    renderLogin('/login?next=%2Fplans')

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'pw')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByText('Plans')).toBeInTheDocument()
    })
  })

  it('shows error message on 401', async () => {
    const user = userEvent.setup()
    vi.mocked(client.apiPost).mockRejectedValueOnce(
      new client.ApiError({ status: 401, message: 'Unauthorized' }),
    )

    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'wrongpass')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Invalid username or password')
    })
  })

  it('shows generic error on non-401 failures', async () => {
    const user = userEvent.setup()
    vi.mocked(client.apiPost).mockRejectedValueOnce(
      new client.ApiError({ status: 500, message: 'Server error' }),
    )

    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'pw')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Sign in failed')
    })
  })

  it('disables submit button while loading', async () => {
    const user = userEvent.setup()
    let resolve: (v: unknown) => void
    vi.mocked(client.apiPost).mockReturnValueOnce(
      new Promise((r) => {
        resolve = r
      }),
    )

    renderLogin()

    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'pw')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled()

    // Cleanup
    resolve!({ access_token: 'tok', token_type: 'bearer', expires_in: 3600 })
  })
})
