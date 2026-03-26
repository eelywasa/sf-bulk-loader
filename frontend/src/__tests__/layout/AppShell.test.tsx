import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import AppShell from '../../layout/AppShell'
import { ThemeProvider } from '../../context/ThemeContext'
import { AuthProvider } from '../../context/AuthContext'
import * as client from '../../api/client'
import type { RuntimeConfig, UserResponse } from '../../api/types'

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

const MOCK_USER: UserResponse = {
  id: '1',
  username: 'alice',
  email: null,
  display_name: null,
  role: 'admin',
  is_active: true,
}

const MOCK_USER_DISPLAY: UserResponse = {
  ...MOCK_USER,
  display_name: 'Alice Admin',
}

function renderAppShell(initialPath = '/', mockUser: UserResponse | null = null) {
  if (mockUser) {
    localStorage.setItem('auth_token', 'test-token')
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
    vi.mocked(client.apiFetch).mockResolvedValueOnce(mockUser)
  }
  const router = createMemoryRouter(
    [
      {
        element: <AppShell />,
        children: [
          { path: '/', element: <div>Dashboard page</div> },
          { path: '/connections', element: <div>Connections page</div> },
          { path: '/plans', element: <div>Plans page</div> },
          { path: '/runs', element: <div>Runs page</div> },
          { path: '/files', element: <div>Files page</div> },
        ],
      },
    ],
    { initialEntries: [initialPath] },
  )
  return render(
    <ThemeProvider>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </ThemeProvider>,
  )
}

describe('AppShell', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders the brand name', () => {
    renderAppShell()
    expect(screen.getByText('Bulk Loader')).toBeInTheDocument()
  })

  it('renders all navigation links', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Connections' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Load Plans' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Runs' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Input Files' })).toBeInTheDocument()
  })

  it('renders the outlet content for the current route', () => {
    renderAppShell('/')
    expect(screen.getByText('Dashboard page')).toBeInTheDocument()
  })

  it('renders connections page at /connections', () => {
    renderAppShell('/connections')
    expect(screen.getByText('Connections page')).toBeInTheDocument()
  })

  it('dashboard link has correct href', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/')
  })

  it('connections link has correct href', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Connections' })).toHaveAttribute(
      'href',
      '/connections',
    )
  })

  it('plans link has correct href', () => {
    renderAppShell()
    expect(screen.getByRole('link', { name: 'Load Plans' })).toHaveAttribute('href', '/plans')
  })

  it('has a main navigation landmark', () => {
    renderAppShell()
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })

  it('renders Settings button instead of version string', () => {
    renderAppShell()
    expect(screen.getByRole('button', { name: /settings/i })).toBeInTheDocument()
    expect(screen.queryByText('SF Bulk Loader v0.1')).not.toBeInTheDocument()
  })

  it('Settings menu is closed by default', () => {
    renderAppShell()
    expect(screen.queryByRole('button', { name: /theme/i })).not.toBeInTheDocument()
  })

  it('opens Settings menu on click', async () => {
    const user = userEvent.setup()
    renderAppShell()
    await user.click(screen.getByRole('button', { name: /settings/i }))
    expect(screen.getByRole('button', { name: /theme/i })).toBeInTheDocument()
  })

  it('opens Theme submenu on click', async () => {
    const user = userEvent.setup()
    renderAppShell()
    await user.click(screen.getByRole('button', { name: /settings/i }))
    await user.click(screen.getByRole('button', { name: /theme/i }))
    expect(screen.getByRole('menuitemradio', { name: /light/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitemradio', { name: /dark/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitemradio', { name: /system/i })).toBeInTheDocument()
  })

  it('closes menu after selecting a theme', async () => {
    const user = userEvent.setup()
    renderAppShell()
    await user.click(screen.getByRole('button', { name: /settings/i }))
    await user.click(screen.getByRole('button', { name: /theme/i }))
    await user.click(screen.getByRole('menuitemradio', { name: /light/i }))
    expect(screen.queryByRole('button', { name: /theme/i })).not.toBeInTheDocument()
  })

  it('renders an icon for each nav item', () => {
    renderAppShell()
    const nav = screen.getByRole('navigation', { name: 'Main navigation' })
    const links = within(nav).getAllByRole('link')
    links.forEach((link) => {
      expect(link.querySelector('svg')).toBeInTheDocument()
    })
  })

  it('renders the logo icon in the brand area', () => {
    const { container } = renderAppShell()
    const brand = container.querySelector('.px-3.py-4')
    expect(brand?.querySelector('svg')).toBeInTheDocument()
  })

  it('shows sign out button when auth is required', async () => {
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_LOCAL)
    renderAppShell()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
    })
  })

  it('hides sign out button in desktop profile', async () => {
    vi.mocked(client.apiFetch).mockResolvedValueOnce(MOCK_RUNTIME_DESKTOP)
    renderAppShell()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /sign out/i })).not.toBeInTheDocument()
    })
  })

  it('shows username when user is authenticated', async () => {
    renderAppShell('/', MOCK_USER)
    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })
  })

  it('shows display_name when present', async () => {
    renderAppShell('/', MOCK_USER_DISPLAY)
    await waitFor(() => {
      expect(screen.getByText('Alice Admin')).toBeInTheDocument()
    })
  })

  describe('collapsible sidebar', () => {
    it('renders the collapse toggle button', () => {
      renderAppShell()
      expect(screen.getByRole('button', { name: /collapse sidebar/i })).toBeInTheDocument()
    })

    it('hides nav labels and brand text when collapsed', async () => {
      const user = userEvent.setup()
      renderAppShell()
      await user.click(screen.getByRole('button', { name: /collapse sidebar/i }))
      expect(screen.queryByText('Bulk Loader')).not.toBeInTheDocument()
      expect(screen.queryByText('Dashboard')).not.toBeInTheDocument()
      expect(screen.queryByText('Connections')).not.toBeInTheDocument()
    })

    it('shows expand button after collapsing', async () => {
      const user = userEvent.setup()
      renderAppShell()
      await user.click(screen.getByRole('button', { name: /collapse sidebar/i }))
      expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument()
    })

    it('restores nav labels when expanded again', async () => {
      const user = userEvent.setup()
      renderAppShell()
      await user.click(screen.getByRole('button', { name: /collapse sidebar/i }))
      await user.click(screen.getByRole('button', { name: /expand sidebar/i }))
      expect(screen.getByText('Bulk Loader')).toBeInTheDocument()
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
    })

    it('persists collapsed state to localStorage', async () => {
      const user = userEvent.setup()
      renderAppShell()
      await user.click(screen.getByRole('button', { name: /collapse sidebar/i }))
      expect(localStorage.getItem('sidebarCollapsed')).toBe('true')
    })

    it('reads collapsed state from localStorage on mount', () => {
      localStorage.setItem('sidebarCollapsed', 'true')
      renderAppShell()
      expect(screen.queryByText('Bulk Loader')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument()
    })

    it('nav icons are still present when collapsed', async () => {
      const user = userEvent.setup()
      renderAppShell()
      await user.click(screen.getByRole('button', { name: /collapse sidebar/i }))
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      const links = within(nav).getAllByRole('link')
      links.forEach((link) => {
        expect(link.querySelector('svg')).toBeInTheDocument()
      })
    })
  })

  it('logout button triggers logout', async () => {
    const user = userEvent.setup()
    const mockLocation = { href: '', pathname: '/' }
    vi.stubGlobal('location', mockLocation)

    renderAppShell('/', MOCK_USER)
    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    await act(async () => {
      await user.click(screen.getByRole('button', { name: /sign out/i }))
    })

    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(mockLocation.href).toBe('/login')

    vi.unstubAllGlobals()
  })
})
