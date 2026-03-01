import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../components/ui/Toast'
import AppShell from '../layout/AppShell'
import Dashboard from '../pages/Dashboard'
import Connections from '../pages/Connections'
import PlansPage from '../pages/PlansPage'
import PlanEditor from '../pages/PlanEditor'
import RunsPage from '../pages/RunsPage'
import RunDetail from '../pages/RunDetail'
import JobDetail from '../pages/JobDetail'
import FilesPage from '../pages/FilesPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})

function renderRoute(path: string) {
  const router = createMemoryRouter(
    [
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
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

describe('Routing', () => {
  it('renders Dashboard at /', () => {
    renderRoute('/')
    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('renders Connections at /connections', () => {
    renderRoute('/connections')
    expect(screen.getByRole('heading', { name: 'Connections' })).toBeInTheDocument()
  })

  it('renders PlansPage at /plans', () => {
    renderRoute('/plans')
    expect(screen.getByRole('heading', { name: 'Load Plans' })).toBeInTheDocument()
  })

  it('renders PlanEditor at /plans/new', () => {
    renderRoute('/plans/new')
    expect(screen.getByRole('heading', { name: 'New Load Plan' })).toBeInTheDocument()
  })

  it('renders PlanEditor in edit mode at /plans/some-id', () => {
    renderRoute('/plans/abc-123')
    expect(screen.getByRole('heading', { name: 'Edit Load Plan' })).toBeInTheDocument()
  })

  it('renders RunsPage at /runs', () => {
    renderRoute('/runs')
    expect(screen.getByRole('heading', { name: 'Runs' })).toBeInTheDocument()
  })

  it('renders RunDetail at /runs/:id', () => {
    renderRoute('/runs/run-456')
    expect(screen.getByRole('heading', { name: 'Run Detail' })).toBeInTheDocument()
  })

  it('renders JobDetail at /runs/:runId/jobs/:jobId', () => {
    renderRoute('/runs/run-456/jobs/job-789')
    expect(screen.getByRole('heading', { name: 'Job Detail' })).toBeInTheDocument()
  })

  it('renders FilesPage at /files', () => {
    renderRoute('/files')
    expect(screen.getByRole('heading', { name: 'Input Files' })).toBeInTheDocument()
  })

  it('AppShell nav is present on all routes', () => {
    renderRoute('/connections')
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })
})
