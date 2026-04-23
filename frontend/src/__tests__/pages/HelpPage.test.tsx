import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider } from '../../context/AuthContext'
import { ThemeProvider } from '../../context/ThemeContext'
import * as client from '../../api/client'
import type { RuntimeConfig, UserResponse } from '../../api/types'
import HelpPage from '../../pages/HelpPage'
import AppShell from '../../layout/AppShell'

// ─── Mock virtual:help-content ────────────────────────────────────────────────

vi.mock('virtual:help-content', () => ({
  default: {
    topics: [
      {
        slug: 'usage-index',
        title: 'Using the Bulk Loader',
        nav_order: 0,
        tags: ['index'],
        summary: 'Overview of all topics.',
        html: '<h1 id="overview">Overview</h1><p>Welcome to help.</p>',
        headings: [{ id: 'overview', text: 'Overview', level: 1 }],
        bodyText: 'Overview Welcome to help.',
      },
      {
        slug: 'getting-started',
        title: 'Getting started',
        nav_order: 10,
        tags: [],
        summary: 'First steps.',
        html: '<h2 id="intro">Intro</h2><p>Start here.</p>',
        headings: [{ id: 'intro', text: 'Intro', level: 2 }],
        bodyText: 'Intro Start here.',
      },
      {
        slug: 'user-management',
        title: 'User management',
        nav_order: 100,
        tags: [],
        summary: 'Manage users.',
        required_permission: 'users.manage',
        html: '<h2 id="users">Users</h2><p>Admin only.</p>',
        headings: [{ id: 'users', text: 'Users', level: 2 }],
        bodyText: 'Users Admin only.',
      },
    ],
  },
}))

// ─── Runtime / user fixtures ──────────────────────────────────────────────────

// Use auth_mode: 'none' so AuthProvider auto-fetches /me without needing a stored token.
// This lets us control permissions via the mocked /me response.
const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'none',
  app_distribution: 'desktop',
  transport_mode: 'local',
  input_storage_mode: 'local',
}

function makeUser(permissions: string[]): UserResponse {
  return {
    id: 'test-user',
    email: 'test@example.com',
    display_name: 'Test User',
    is_admin: false,
    profile: { name: 'viewer' },
    permissions,
  }
}

// ─── Render helpers ───────────────────────────────────────────────────────────

function renderHelpPage(
  initialPath: string = '/help',
  permissions: string[] = ['runs.view', 'plans.view'],
) {
  vi.spyOn(client, 'apiFetch').mockImplementation((url: string) => {
    if (url === '/api/runtime') return Promise.resolve(MOCK_RUNTIME)
    return Promise.resolve(makeUser(permissions))
  })

  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/help" element={<HelpPage />} />
            <Route path="/403" element={<div data-testid="forbidden-page">Forbidden</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

function renderAppShell(permissions: string[] = ['runs.view', 'plans.view']) {
  vi.spyOn(client, 'apiFetch').mockImplementation((url: string) => {
    if (url === '/api/runtime') return Promise.resolve(MOCK_RUNTIME)
    return Promise.resolve(makeUser(permissions))
  })

  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<div>Dashboard</div>} />
              <Route path="/help" element={<HelpPage />} />
            </Route>
            <Route path="/403" element={<div data-testid="forbidden-page">Forbidden</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('HelpPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── Nav rendering ─────────────────────────────────────────────────────────

  it('renders nav topics from the content index (excluding usage-index)', async () => {
    renderHelpPage()
    // "Getting started" should appear in the nav
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Getting started' })).toBeInTheDocument()
    })
    // "Using the Bulk Loader" (usage-index) should NOT appear in the nav
    expect(screen.queryByRole('button', { name: 'Using the Bulk Loader' })).not.toBeInTheDocument()
  })

  it('shows usage-index content as the default landing page', async () => {
    renderHelpPage('/help')
    await waitFor(() => {
      expect(screen.getByText('Welcome to help.')).toBeInTheDocument()
    })
  })

  it('renders help nav label', async () => {
    renderHelpPage()
    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Help topics' })).toBeInTheDocument()
    })
  })

  // ── Permission gating ──────────────────────────────────────────────────────

  it('hides admin topics from users without the required permission', async () => {
    // User does NOT have users.manage
    renderHelpPage('/help', ['runs.view', 'plans.view'])
    // Wait for auth bootstrap then check nav
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Getting started' })).toBeInTheDocument()
    })
    expect(screen.queryByRole('button', { name: 'User management' })).not.toBeInTheDocument()
  })

  it('shows admin topics to users who have the required permission', async () => {
    renderHelpPage('/help', ['runs.view', 'plans.view', 'users.manage'])
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'User management' })).toBeInTheDocument()
    })
  })

  // ── Deep-link navigation ──────────────────────────────────────────────────

  it('selects a topic when its nav button is clicked', async () => {
    const user = userEvent.setup()
    renderHelpPage('/help')
    await waitFor(() => screen.getByRole('button', { name: 'Getting started' }))

    await user.click(screen.getByRole('button', { name: 'Getting started' }))

    await waitFor(() => {
      expect(screen.getByText('Start here.')).toBeInTheDocument()
    })
  })

  it('loads the correct topic when a hash is present in the URL', async () => {
    renderHelpPage('/help#getting-started')
    await waitFor(() => {
      expect(screen.getByText('Start here.')).toBeInTheDocument()
    })
  })

  // ── Admin topic redirect ──────────────────────────────────────────────────

  it('redirects to /403 when an admin topic is accessed directly via hash by an unpermitted user', async () => {
    renderHelpPage('/help#user-management', ['runs.view'])
    await waitFor(() => {
      expect(screen.getByTestId('forbidden-page')).toBeInTheDocument()
    })
  })

  it('does NOT redirect to /403 for an admin topic when user has the permission', async () => {
    renderHelpPage('/help#user-management', ['runs.view', 'users.manage'])
    await waitFor(() => {
      expect(screen.getByText('Admin only.')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('forbidden-page')).not.toBeInTheDocument()
  })
})

// ─── AppShell Help link ───────────────────────────────────────────────────────

describe('AppShell Help link', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a Help link in the top bar', async () => {
    renderAppShell()
    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Help' })).toBeInTheDocument()
    })
  })

  it('Help link points to /help', async () => {
    renderAppShell()
    await waitFor(() => {
      const link = screen.getByRole('link', { name: 'Help' })
      expect(link).toHaveAttribute('href', '/help')
    })
  })
})
