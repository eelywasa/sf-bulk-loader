import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../components/ui/Toast'
import { ThemeProvider } from '../context/ThemeContext'
import { AuthProvider } from '../context/AuthContext'
import * as client from '../api/client'
import type { RuntimeConfig } from '../api/types'
import AppShell from '../layout/AppShell'
import Login from '../pages/Login'
import Dashboard from '../pages/Dashboard'
import Connections from '../pages/Connections'
import PlansPage from '../pages/PlansPage'
import PlanEditor from '../pages/PlanEditor'
import RunsPage from '../pages/RunsPage'
import RunDetail from '../pages/RunDetail'
import JobDetail from '../pages/JobDetail'
import FilesPage from '../pages/FilesPage'
import type { UserResponse } from '../api/types'

const MOCK_USER: UserResponse = {
  id: '1',
  username: 'testuser',
  email: null,
  display_name: null,
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

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})

function renderRoute(path: string) {
  const router = createMemoryRouter(
    [
      { path: '/login', element: <Login /> },
      {
        element: <AppShell />,
        children: [
          { path: '/', element: <Dashboard /> },
          { path: '/connections', element: <Connections /> },
          { path: '/plans', element: <PlansPage /> },
          { path: '/plans/:id', element: <PlanEditor /> },
          { path: '/runs', element: <RunsPage /> },
          { path: '/runs/:id', element: <RunDetail /> },
          { path: '/runs/:runId/jobs/:jobId', element: <JobDetail /> },
          { path: '/files', element: <FilesPage /> },
        ],
      },
    ],
    { initialEntries: [path] },
  )

  return render(
    <ThemeProvider>
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <RouterProvider router={router} />
          </ToastProvider>
        </QueryClientProvider>
      </AuthProvider>
    </ThemeProvider>,
  )
}

const MOCK_RUNTIME: RuntimeConfig = {
  auth_mode: 'local',
  app_distribution: 'self_hosted',
  transport_mode: 'http',
  input_storage_mode: 'local',
}

describe('Routing', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(client, 'apiFetch').mockImplementation((url: string) => {
      if (url === '/api/runtime') return Promise.resolve(MOCK_RUNTIME)
      return Promise.resolve(MOCK_USER)
    })
    localStorage.setItem('auth_token', 'test-token')
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders Dashboard at /', async () => {
    renderRoute('/')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    })
  })

  it('renders Connections at /connections', async () => {
    renderRoute('/connections')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Connections' })).toBeInTheDocument()
    })
  })

  it('renders PlansPage at /plans', async () => {
    renderRoute('/plans')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Load Plans' })).toBeInTheDocument()
    })
  })

  it('renders PlanEditor at /plans/new', async () => {
    renderRoute('/plans/new')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Load Plan' })).toBeInTheDocument()
    })
  })

  it('renders PlanEditor in edit mode at /plans/some-id', async () => {
    renderRoute('/plans/abc-123')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Edit Load Plan' })).toBeInTheDocument()
    })
  })

  it('renders RunsPage at /runs', async () => {
    renderRoute('/runs')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Runs' })).toBeInTheDocument()
    })
  })

  it('renders RunDetail at /runs/:id', async () => {
    renderRoute('/runs/run-456')
    // RunDetail fetches asynchronously; on initial render it shows a loading indicator
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('renders JobDetail at /runs/:runId/jobs/:jobId', async () => {
    renderRoute('/runs/run-456/jobs/job-789')
    // JobDetail fetches asynchronously; on initial render it shows a loading indicator
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('renders FilesPage at /files', async () => {
    renderRoute('/files')
    // FilesPage fetches asynchronously; on initial render it shows a loading indicator
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('AppShell nav is present on all routes', async () => {
    renderRoute('/connections')
    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
    })
  })

  it('renders login page at /login', () => {
    localStorage.clear() // no auth token for login page test
    vi.restoreAllMocks()
    renderRoute('/login')
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
  })
})
