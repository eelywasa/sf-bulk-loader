import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/ui/Toast'
import PlansPage from '../../pages/PlansPage'
import type { LoadPlan, Connection } from '../../api/types'

// ─── Mock the endpoints module ─────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  plansApi: {
    list: vi.fn(),
    delete: vi.fn(),
  },
  connectionsApi: {
    list: vi.fn(),
  },
}))

import { plansApi, connectionsApi } from '../../api/endpoints'

// ─── Test fixtures ─────────────────────────────────────────────────────────────

const conn1: Connection = {
  id: 'conn-1',
  name: 'Production Org',
  instance_url: 'https://prod.my.salesforce.com',
  login_url: 'https://login.salesforce.com',
  client_id: 'abc',
  username: 'admin@prod.com',
  is_sandbox: false,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const plan1: LoadPlan = {
  id: 'plan-1',
  name: 'Q1 Migration',
  description: 'First quarter data migration',
  connection_id: 'conn-1',
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  output_connection_id: null,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const plan2: LoadPlan = {
  id: 'plan-2',
  name: 'Q2 Migration',
  description: null,
  connection_id: 'conn-2',
  abort_on_step_failure: false,
  error_threshold_pct: 5,
  max_parallel_jobs: 3,
  output_connection_id: null,
  created_at: '2024-04-01T00:00:00Z',
  updated_at: '2024-04-01T00:00:00Z',
}

// ─── Render helper ─────────────────────────────────────────────────────────────

function renderPlansPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/plans']}>
          <Routes>
            <Route path="/plans" element={<PlansPage />} />
            <Route path="/plans/new" element={<div data-testid="new-plan-page" />} />
            <Route path="/plans/:id" element={<div data-testid="plan-editor-page" />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function mockList(data: LoadPlan[]) {
  vi.mocked(plansApi.list).mockResolvedValue(data)
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

describe('PlansPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(connectionsApi.list).mockResolvedValue([conn1])
  })

  // ── Loading / empty / error states ─────────────────────────────────────────

  it('shows a loading spinner while fetching', () => {
    vi.mocked(plansApi.list).mockReturnValue(new Promise(() => {}))
    renderPlansPage()
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('shows empty state when no plans exist', async () => {
    mockList([])
    renderPlansPage()
    await waitFor(() => {
      expect(screen.getByText('No load plans yet')).toBeInTheDocument()
    })
  })

  it('shows an error message when the list request fails', async () => {
    vi.mocked(plansApi.list).mockRejectedValue(new Error('Network error'))
    renderPlansPage()
    await waitFor(() => {
      expect(screen.getByText(/Failed to load plans/)).toBeInTheDocument()
      expect(screen.getByText(/Network error/)).toBeInTheDocument()
    })
  })

  // ── List rendering ─────────────────────────────────────────────────────────

  it('renders plan names in the table', async () => {
    mockList([plan1, plan2])
    renderPlansPage()
    await waitFor(() => {
      expect(screen.getByText('Q1 Migration')).toBeInTheDocument()
      expect(screen.getByText('Q2 Migration')).toBeInTheDocument()
    })
  })

  it('renders plan description', async () => {
    mockList([plan1])
    renderPlansPage()
    await waitFor(() => {
      expect(screen.getByText('First quarter data migration')).toBeInTheDocument()
    })
  })

  it('shows — when description is null', async () => {
    mockList([plan2])
    renderPlansPage()
    await waitFor(() => {
      expect(screen.getByText('—')).toBeInTheDocument()
    })
  })

  it('shows connection name when connection is loaded', async () => {
    mockList([plan1])
    renderPlansPage()
    await waitFor(() => {
      expect(screen.getByText('Production Org')).toBeInTheDocument()
    })
  })

  it('renders Edit and Delete buttons for each plan', async () => {
    mockList([plan1, plan2])
    renderPlansPage()
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(2)
      expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(2)
    })
  })

  // ── Navigation ──────────────────────────────────────────────────────────────

  it('navigates to /plans/new when "New Plan" header button is clicked', async () => {
    const user = userEvent.setup()
    mockList([])
    renderPlansPage()
    await waitFor(() => screen.getByText('No load plans yet'))

    await user.click(screen.getByRole('button', { name: 'New Plan' }))

    expect(screen.getByTestId('new-plan-page')).toBeInTheDocument()
  })

  it('navigates to /plans/new from empty-state "Create Plan" button', async () => {
    const user = userEvent.setup()
    mockList([])
    renderPlansPage()
    await waitFor(() => screen.getByText('No load plans yet'))

    await user.click(screen.getByRole('button', { name: 'Create Plan' }))

    expect(screen.getByTestId('new-plan-page')).toBeInTheDocument()
  })

  it('navigates to /plans/:id when Edit button is clicked', async () => {
    const user = userEvent.setup()
    mockList([plan1])
    renderPlansPage()
    await waitFor(() => screen.getByText('Q1 Migration'))

    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0])

    expect(screen.getByTestId('plan-editor-page')).toBeInTheDocument()
  })

  it('navigates to /plans/:id when a row is clicked', async () => {
    const user = userEvent.setup()
    mockList([plan1])
    renderPlansPage()
    await waitFor(() => screen.getByText('Q1 Migration'))

    await user.click(screen.getByText('Q1 Migration'))

    expect(screen.getByTestId('plan-editor-page')).toBeInTheDocument()
  })

  // ── Delete flow ─────────────────────────────────────────────────────────────

  it('opens delete confirmation modal with plan name', async () => {
    const user = userEvent.setup()
    mockList([plan1])
    renderPlansPage()
    await waitFor(() => screen.getByText('Q1 Migration'))

    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Delete Plan')).toBeInTheDocument()
    expect(within(dialog).getByText('Q1 Migration')).toBeInTheDocument()
  })

  it('calls plansApi.delete with the correct plan id when confirmed', async () => {
    const user = userEvent.setup()
    mockList([plan1])
    vi.mocked(plansApi.delete).mockResolvedValue(undefined)

    renderPlansPage()
    await waitFor(() => screen.getByText('Q1 Migration'))

    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])

    const dialog = await screen.findByRole('dialog')
    const deleteButtons = within(dialog).getAllByRole('button', { name: 'Delete' })
    await user.click(deleteButtons[deleteButtons.length - 1])

    await waitFor(() => {
      expect(plansApi.delete).toHaveBeenCalledWith('plan-1')
    })
  })

  it('closes delete modal without deleting when Cancel is clicked', async () => {
    const user = userEvent.setup()
    mockList([plan1])
    renderPlansPage()
    await waitFor(() => screen.getByText('Q1 Migration'))

    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])

    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(plansApi.delete).not.toHaveBeenCalled()
  })
})
