import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/ui/Toast'
import RunsPage from '../../pages/RunsPage'
import type { LoadRun, LoadPlan } from '../../api/types'

// ─── Mock endpoints ────────────────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  runsApi: {
    list: vi.fn(),
  },
  plansApi: {
    list: vi.fn(),
  },
}))

import { runsApi, plansApi } from '../../api/endpoints'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const plan1: LoadPlan = {
  id: 'plan-1',
  name: 'Q1 Migration',
  description: null,
  connection_id: 'conn-1',
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
}

const run1: LoadRun = {
  id: 'aaaabbbb-1111-2222-3333-444455556666',
  load_plan_id: 'plan-1',
  status: 'completed',
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:05:00Z',
  total_records: 1000,
  total_success: 995,
  total_errors: 5,
  initiated_by: 'admin',
  error_summary: null,
  is_retry: false,
}

const run2: LoadRun = {
  id: 'ccccdddd-1111-2222-3333-444455556666',
  load_plan_id: 'plan-1',
  status: 'running',
  started_at: '2024-03-02T09:00:00Z',
  completed_at: null,
  total_records: 500,
  total_success: 200,
  total_errors: 0,
  initiated_by: 'scheduler',
  error_summary: null,
  is_retry: false,
}

const run3: LoadRun = {
  id: 'eeeeffff-1111-2222-3333-444455556666',
  load_plan_id: 'plan-2',
  status: 'failed',
  started_at: '2024-02-28T08:00:00Z',
  completed_at: '2024-02-28T08:02:00Z',
  total_records: 300,
  total_success: 0,
  total_errors: 300,
  initiated_by: null,
  error_summary: { auth_error: 'Step 1 failed: CSV file not found' },
  is_retry: false,
}

// ─── Render helper ─────────────────────────────────────────────────────────────

function renderRunsPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/runs']}>
          <Routes>
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/runs/:id" element={<div data-testid="run-detail-page" />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('RunsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(plansApi.list).mockResolvedValue([plan1])
  })

  // ── Loading / empty / error states ──────────────────────────────────────────

  it('shows loading indicator while fetching', () => {
    vi.mocked(runsApi.list).mockReturnValue(new Promise(() => {}))
    renderRunsPage()
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('shows empty state when no runs exist', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([])
    renderRunsPage()
    await waitFor(() => {
      expect(screen.getByText('No runs found')).toBeInTheDocument()
    })
  })

  it('shows error message when the list request fails', async () => {
    vi.mocked(runsApi.list).mockRejectedValue(new Error('Network error'))
    renderRunsPage()
    await waitFor(() => {
      expect(screen.getByText(/Failed to load runs/)).toBeInTheDocument()
    })
  })

  // ── List rendering ───────────────────────────────────────────────────────────

  it('renders run IDs in the table', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([run1, run2])
    renderRunsPage()
    await waitFor(() => {
      // short ID slices
      expect(screen.getByText('aaaabbbb…')).toBeInTheDocument()
      expect(screen.getByText('ccccdddd…')).toBeInTheDocument()
    })
  })

  it('renders run status badges', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([run1, run2])
    renderRunsPage()
    await waitFor(() => {
      expect(screen.getByText('completed')).toBeInTheDocument()
      expect(screen.getByText('running')).toBeInTheDocument()
    })
  })

  it('renders record counts', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([run1])
    renderRunsPage()
    await waitFor(() => {
      expect(screen.getByText('1000')).toBeInTheDocument()
      expect(screen.getByText('995')).toBeInTheDocument()
      expect(screen.getByText('5')).toBeInTheDocument()
    })
  })

  it('shows plan name when plan is available', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([run1])
    renderRunsPage()
    await waitFor(() => {
      // Q1 Migration appears in both the plan filter dropdown and in the table row
      expect(screen.getAllByText('Q1 Migration').length).toBeGreaterThanOrEqual(1)
    })
  })

  it('renders a View button for each run', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([run1, run2])
    renderRunsPage()
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: 'View' })).toHaveLength(2)
    })
  })

  // ── Navigation ───────────────────────────────────────────────────────────────

  it('navigates to /runs/:id when a row is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.list).mockResolvedValue([run1])
    renderRunsPage()
    await waitFor(() => screen.getByText('aaaabbbb…'))

    await user.click(screen.getByText('aaaabbbb…'))

    expect(screen.getByTestId('run-detail-page')).toBeInTheDocument()
  })

  it('navigates to /runs/:id when View button is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.list).mockResolvedValue([run1])
    renderRunsPage()
    await waitFor(() => screen.getAllByRole('button', { name: 'View' }))

    await user.click(screen.getAllByRole('button', { name: 'View' })[0])

    expect(screen.getByTestId('run-detail-page')).toBeInTheDocument()
  })

  // ── Filters ──────────────────────────────────────────────────────────────────

  it('renders filter dropdowns', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([])
    renderRunsPage()
    await waitFor(() => screen.getByLabelText('Filter by plan'))

    expect(screen.getByLabelText('Filter by plan')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by status')).toBeInTheDocument()
    expect(screen.getByLabelText('Started after')).toBeInTheDocument()
    expect(screen.getByLabelText('Started before')).toBeInTheDocument()
  })

  it('populates plan filter with available plan names', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([])
    renderRunsPage()
    // Wait until the plan option appears inside the select
    await waitFor(() => {
      const planSelect = screen.getByLabelText('Filter by plan')
      expect(within(planSelect as HTMLElement).getByText('Q1 Migration')).toBeInTheDocument()
    })
  })

  it('shows status filter options for all run statuses', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([])
    renderRunsPage()
    await waitFor(() => screen.getByLabelText('Filter by status'))

    const statusSelect = screen.getByLabelText('Filter by status')
    expect(within(statusSelect as HTMLElement).getByText('pending')).toBeInTheDocument()
    expect(within(statusSelect as HTMLElement).getByText('running')).toBeInTheDocument()
    expect(within(statusSelect as HTMLElement).getByText('completed')).toBeInTheDocument()
    expect(within(statusSelect as HTMLElement).getByText('failed')).toBeInTheDocument()
    expect(within(statusSelect as HTMLElement).getByText('aborted')).toBeInTheDocument()
  })

  it('shows clear filters button when a filter is set', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.list).mockResolvedValue([])
    renderRunsPage()
    await waitFor(() => screen.getByLabelText('Filter by status'))

    // Initially no clear button
    expect(screen.queryByRole('button', { name: /clear filters/i })).not.toBeInTheDocument()

    // Select a status
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'running')

    expect(screen.getByRole('button', { name: /clear filters/i })).toBeInTheDocument()
  })

  it('hides clear filters button after clearing', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.list).mockResolvedValue([])
    renderRunsPage()
    await waitFor(() => screen.getByLabelText('Filter by status'))

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'running')
    await user.click(screen.getByRole('button', { name: /clear filters/i }))

    expect(screen.queryByRole('button', { name: /clear filters/i })).not.toBeInTheDocument()
  })

  it('re-queries runs when a status filter is applied', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.list).mockResolvedValue([run2])
    renderRunsPage()
    await waitFor(() => screen.getByLabelText('Filter by status'))

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'running')

    await waitFor(() => {
      expect(runsApi.list).toHaveBeenCalledWith(expect.objectContaining({ run_status: 'running' }))
    })
  })

  // ── Empty state with active filters ─────────────────────────────────────────

  it('shows filtered empty message when filters are active and no runs found', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.list).mockResolvedValue([])
    renderRunsPage()
    await waitFor(() => screen.getByLabelText('Filter by status'))

    await user.selectOptions(screen.getByLabelText('Filter by status'), 'running')

    await waitFor(() => {
      expect(screen.getByText(/No runs match the current filters/)).toBeInTheDocument()
    })
  })

  // ── Multiple runs rendering ──────────────────────────────────────────────────

  it('renders all three runs', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([run1, run2, run3])
    renderRunsPage()
    await waitFor(() => {
      expect(screen.getByText('aaaabbbb…')).toBeInTheDocument()
      expect(screen.getByText('ccccdddd…')).toBeInTheDocument()
      expect(screen.getByText('eeeeffff…')).toBeInTheDocument()
    })
  })

  it('renders failed status badge', async () => {
    vi.mocked(runsApi.list).mockResolvedValue([run3])
    renderRunsPage()
    await waitFor(() => {
      expect(screen.getByText('failed')).toBeInTheDocument()
    })
  })
})
