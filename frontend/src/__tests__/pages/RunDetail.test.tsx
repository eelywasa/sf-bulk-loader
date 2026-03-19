import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/ui/Toast'
import RunDetail from '../../pages/RunDetail'
import type { LoadRun, JobRecord, LoadPlanDetail } from '../../api/types'

// ─── Mock endpoints + hook ────────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  runsApi: {
    get: vi.fn(),
    jobs: vi.fn(),
    abort: vi.fn(),
    retryStep: vi.fn(),
    logsZipUrl: vi.fn(() => '/fake-zip-url'),
  },
  plansApi: {
    get: vi.fn(),
  },
}))

import { runsApi, plansApi } from '../../api/endpoints'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const planDetail: LoadPlanDetail = {
  id: 'plan-1',
  connection_id: 'conn-1',
  name: 'Q1 Migration',
  description: null,
  abort_on_step_failure: true,
  error_threshold_pct: 10,
  max_parallel_jobs: 5,
  created_at: '2024-03-01T00:00:00Z',
  updated_at: '2024-03-01T00:00:00Z',
  load_steps: [
    {
      id: 'step-1',
      load_plan_id: 'plan-1',
      sequence: 1,
      object_name: 'Account',
      operation: 'insert',
      csv_file_pattern: 'accounts_*.csv',
      partition_size: 10000,
      external_id_field: null,
      assignment_rule_id: null,
      created_at: '2024-03-01T00:00:00Z',
      updated_at: '2024-03-01T00:00:00Z',
    },
    {
      id: 'step-2',
      load_plan_id: 'plan-1',
      sequence: 2,
      object_name: 'Contact',
      operation: 'upsert',
      csv_file_pattern: 'contacts_*.csv',
      partition_size: 5000,
      external_id_field: 'ExternalId__c',
      assignment_rule_id: null,
      created_at: '2024-03-01T00:00:00Z',
      updated_at: '2024-03-01T00:00:00Z',
    },
  ],
}

const runCompleted: LoadRun = {
  id: 'run-111',
  load_plan_id: 'plan-1',
  status: 'completed',
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:05:00Z',
  total_records: 1500,
  total_success: 1500,
  total_errors: 0,
  initiated_by: 'admin',
  error_summary: null,
  is_retry: false,
}

const runRunning: LoadRun = {
  ...runCompleted,
  status: 'running',
  completed_at: null,
  total_success: 500,
  total_errors: 0,
}

const runFailed: LoadRun = {
  ...runCompleted,
  status: 'failed',
  total_success: 0,
  total_errors: 1500,
  error_summary: { auth_error: 'Step 1 exceeded error threshold' },
}

const jobComplete: JobRecord = {
  id: 'job-1',
  load_run_id: 'run-111',
  load_step_id: 'step-1',
  sf_job_id: 'sf-abc',
  partition_index: 0,
  status: 'job_complete',
  records_processed: 1000,
  records_failed: 0,
  success_file_path: '/output/success.csv',
  error_file_path: null,
  unprocessed_file_path: null,
  sf_api_response: null,
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:03:00Z',
  error_message: null,
}

const jobFailed: JobRecord = {
  id: 'job-2',
  load_run_id: 'run-111',
  load_step_id: 'step-1',
  sf_job_id: null,
  partition_index: 1,
  status: 'failed',
  records_processed: 0,
  records_failed: 500,
  success_file_path: null,
  error_file_path: '/output/errors.csv',
  unprocessed_file_path: null,
  sf_api_response: null,
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:01:00Z',
  error_message: 'Bulk API job creation failed',
}

const jobAborted: JobRecord = {
  ...jobComplete,
  id: 'job-3',
  status: 'aborted',
  records_processed: 0,
  records_failed: 0,
}

const jobCompleteWithErrors: JobRecord = {
  ...jobComplete,
  id: 'job-4',
  records_processed: 800,
  records_failed: 200,
  error_file_path: '/output/errors.csv',
}

const runAborted: LoadRun = {
  ...runCompleted,
  status: 'aborted',
  total_success: 0,
  total_errors: 0,
  error_summary: null,
}

// ─── Render helper ─────────────────────────────────────────────────────────────

function renderRunDetail(runId = 'run-111') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[`/runs/${runId}`]}>
          <Routes>
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/runs" element={<div data-testid="runs-page" />} />
            <Route path="/runs/:runId/jobs/:jobId" element={<div data-testid="job-detail-page" />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('RunDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ── Loading / error states ───────────────────────────────────────────────────

  it('shows loading indicator while run is fetching', () => {
    vi.mocked(runsApi.get).mockReturnValue(new Promise(() => {}))
    renderRunDetail()
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('shows error state when run fetch fails', async () => {
    vi.mocked(runsApi.get).mockRejectedValue(new Error('Not found'))
    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText(/Failed to load run/)).toBeInTheDocument()
    })
  })

  it('shows Back to Runs button in error state', async () => {
    vi.mocked(runsApi.get).mockRejectedValue(new Error('Error'))
    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Back to Runs' })).toBeInTheDocument()
    })
  })

  it('navigates to /runs from Back to Runs button in error state', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockRejectedValue(new Error('Error'))
    renderRunDetail()
    await waitFor(() => screen.getByRole('button', { name: 'Back to Runs' }))

    await user.click(screen.getByRole('button', { name: 'Back to Runs' }))

    expect(screen.getByTestId('runs-page')).toBeInTheDocument()
  })

  // ── Summary header ───────────────────────────────────────────────────────────

  it('renders run ID in header', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      // Both breadcrumb and h1 contain the ID — check at least one appears
      expect(screen.getAllByText('run-111').length).toBeGreaterThanOrEqual(1)
    })
  })

  it('renders status badge', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('completed')).toBeInTheDocument()
    })
  })

  it('renders record counts in stats', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      // total_records and total_success are both 1500 — use getAllByText
      expect(screen.getAllByText('1500').length).toBeGreaterThanOrEqual(1)
      // errors should be 0
      expect(screen.getByText('0')).toBeInTheDocument()
    })
  })

  it('renders plan name link when plan is loaded', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('Plan: Q1 Migration')).toBeInTheDocument()
    })
  })

  it('shows Polling indicator when run is live', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('Polling…')).toBeInTheDocument()
    })
  })

  it('does not show Polling indicator for a completed run', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByText('completed'))

    expect(screen.queryByText('Polling…')).not.toBeInTheDocument()
  })

  it('renders error summary when present', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runFailed)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('Step 1 exceeded error threshold')).toBeInTheDocument()
    })
  })

  // ── Breadcrumb ───────────────────────────────────────────────────────────────

  it('renders breadcrumb Runs link', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    // wait for run to load (multiple elements with this text — use getAllByText)
    await waitFor(() => expect(screen.getAllByText('run-111').length).toBeGreaterThan(0))

    expect(screen.getByRole('link', { name: 'Runs' })).toBeInTheDocument()
  })

  // ── Abort button ─────────────────────────────────────────────────────────────

  it('shows Abort Run button when run is live', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Abort Run' })).toBeInTheDocument()
    })
  })

  it('does not show Abort Run button for a completed run', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByText('completed'))

    expect(screen.queryByRole('button', { name: 'Abort Run' })).not.toBeInTheDocument()
  })

  // ── Abort modal ───────────────────────────────────────────────────────────────

  it('opens abort confirmation modal when Abort Run is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByRole('button', { name: 'Abort Run' }))

    await user.click(screen.getByRole('button', { name: 'Abort Run' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Abort Run')).toBeInTheDocument()
  })

  it('calls runsApi.abort when modal Abort button is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)
    vi.mocked(runsApi.abort).mockResolvedValue(undefined)

    renderRunDetail()
    await waitFor(() => screen.getByRole('button', { name: 'Abort Run' }))

    await user.click(screen.getByRole('button', { name: 'Abort Run' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Abort' }))

    await waitFor(() => {
      expect(runsApi.abort).toHaveBeenCalledWith('run-111')
    })
  })

  it('closes abort modal when Cancel is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByRole('button', { name: 'Abort Run' }))

    await user.click(screen.getByRole('button', { name: 'Abort Run' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(runsApi.abort).not.toHaveBeenCalled()
  })

  it('shows friendly message on 409 abort response', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    const { ApiError } = await import('../../api/client')
    const err409 = new ApiError({ status: 409, message: 'Conflict' })
    vi.mocked(runsApi.abort).mockRejectedValue(err409)

    renderRunDetail()
    await waitFor(() => screen.getByRole('button', { name: 'Abort Run' }))

    await user.click(screen.getByRole('button', { name: 'Abort Run' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Abort' }))

    await waitFor(() => {
      expect(screen.getByText(/Run is not abortable/)).toBeInTheDocument()
    })
  })

  // ── Step accordion ────────────────────────────────────────────────────────────

  it('renders step panels for each step in the plan', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByLabelText('Step 1: Account')).toBeInTheDocument()
      expect(screen.getByLabelText('Step 2: Contact')).toBeInTheDocument()
    })
  })

  it('renders step object names in the accordion', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('Account')).toBeInTheDocument()
      expect(screen.getByText('Contact')).toBeInTheDocument()
    })
  })

  it('expands step accordion to show jobs when header is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobComplete])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByLabelText('Step 1: Account'))

    await user.click(screen.getByLabelText('Step 1: Account'))

    await waitFor(() => {
      expect(screen.getByText('Part 0')).toBeInTheDocument()
      expect(screen.getByText('job_complete')).toBeInTheDocument()
    })
  })

  it('shows "No jobs started yet" for a step with no jobs after expansion', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByLabelText('Step 1: Account'))

    await user.click(screen.getByLabelText('Step 1: Account'))

    expect(screen.getByText('No jobs started yet.')).toBeInTheDocument()
  })

  it('shows job error message in expanded step', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobFailed])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByLabelText('Step 1: Account'))

    await user.click(screen.getByLabelText('Step 1: Account'))

    await waitFor(() => {
      expect(screen.getByText('Bulk API job creation failed')).toBeInTheDocument()
    })
  })

  it('renders Details link for each job in expanded step', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobComplete])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByLabelText('Step 1: Account'))

    await user.click(screen.getByLabelText('Step 1: Account'))

    await waitFor(() => {
      const detailsLink = screen.getByRole('link', { name: 'Details' })
      expect(detailsLink).toHaveAttribute('href', '/runs/run-111/jobs/job-1')
    })
  })

  it('collapses the step panel when clicked again', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobComplete])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByLabelText('Step 1: Account'))

    // Expand
    await user.click(screen.getByLabelText('Step 1: Account'))
    await waitFor(() => screen.getByText('Part 0'))

    // Collapse
    await user.click(screen.getByLabelText('Step 1: Account'))
    expect(screen.queryByText('Part 0')).not.toBeInTheDocument()
  })

  // ── Retry Failed Records button ───────────────────────────────────────────────

  it('shows Retry Failed Records for a step whose job has status failed', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runFailed)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobFailed])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Retry Failed Records' })).toBeInTheDocument()
    })
  })

  it('shows Retry Failed Records for a complete step with failed records', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobCompleteWithErrors])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Retry Failed Records' })).toBeInTheDocument()
    })
  })

  it('does not show Retry Failed Records for a complete step with zero failed records', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobComplete])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByText('completed'))

    expect(screen.queryByRole('button', { name: 'Retry Failed Records' })).not.toBeInTheDocument()
  })

  it('does not show Retry Failed Records for an aborted step', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runAborted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobAborted])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByText('aborted'))

    expect(screen.queryByRole('button', { name: 'Retry Failed Records' })).not.toBeInTheDocument()
  })

  it('does not show Retry Failed Records while the run is live', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runRunning)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobFailed])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByText('Polling…'))

    expect(screen.queryByRole('button', { name: 'Retry Failed Records' })).not.toBeInTheDocument()
  })

  // ── Progress bar ──────────────────────────────────────────────────────────────

  it('renders progress bar when total_records is set', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByRole('progressbar')).toBeInTheDocument()
    })
  })
})
