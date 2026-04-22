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

const MOCK_ALL_PERMISSIONS = new Set([
  'connections.view', 'connections.view_credentials', 'connections.manage',
  'plans.view', 'plans.manage',
  'runs.view', 'runs.execute', 'runs.abort',
  'files.view', 'files.view_contents',
  'users.manage', 'system.settings',
])

vi.mock('../../context/AuthContext', () => ({
  useAuth: vi.fn(() => ({ authRequired: true, permissions: MOCK_ALL_PERMISSIONS })),
  useAuthOptional: vi.fn(() => ({ authRequired: true, permissions: MOCK_ALL_PERMISSIONS })),
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
  output_connection_id: null,
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

const planDetailWithQuery: LoadPlanDetail = {
  ...planDetail,
  load_steps: [
    {
      id: 'step-q1',
      load_plan_id: 'plan-1',
      sequence: 1,
      object_name: 'Account',
      operation: 'query',
      csv_file_pattern: null,
      soql: 'SELECT Id, Name FROM Account WHERE CreatedDate = TODAY',
      partition_size: 10000,
      external_id_field: null,
      assignment_rule_id: null,
      created_at: '2024-03-01T00:00:00Z',
      updated_at: '2024-03-01T00:00:00Z',
    },
    {
      id: 'step-qa1',
      load_plan_id: 'plan-1',
      sequence: 2,
      object_name: 'Contact',
      operation: 'queryAll',
      csv_file_pattern: null,
      soql: 'SELECT Id, Name, IsDeleted FROM Contact',
      partition_size: 10000,
      external_id_field: null,
      assignment_rule_id: null,
      created_at: '2024-03-01T00:00:00Z',
      updated_at: '2024-03-01T00:00:00Z',
    },
  ],
}

const jobQueryComplete: JobRecord = {
  id: 'job-q1',
  load_run_id: 'run-111',
  load_step_id: 'step-q1',
  sf_job_id: 'sf-query-abc',
  partition_index: 0,
  status: 'job_complete',
  records_processed: 500,
  records_failed: 0,
  success_file_path: '/output/query_result.csv',
  error_file_path: null,
  unprocessed_file_path: null,
  sf_api_response: null,
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:03:00Z',
  error_message: null,
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

  it('renders storage_error from error_summary', async () => {
    const runStorageFailed: LoadRun = {
      ...runFailed,
      error_summary: { storage_error: 'Input bucket unreachable during step 2' },
    }
    vi.mocked(runsApi.get).mockResolvedValue(runStorageFailed)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('Input bucket unreachable during step 2')).toBeInTheDocument()
    })
  })

  it('renders generic failure message for failed run with no recognized reason', async () => {
    // Defensive: a future error_summary key (not yet mapped on the frontend)
    // must not cause the failure banner to silently disappear.
    const runUnknownReason: LoadRun = {
      ...runFailed,
      error_summary: { preflight_warnings: null } as LoadRun['error_summary'],
    }
    vi.mocked(runsApi.get).mockResolvedValue(runUnknownReason)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText(/Run failed\. See logs for details\./)).toBeInTheDocument()
    })
  })

  it('renders preflight warning banner when preflight_warnings are present', async () => {
    const runWithPreflight: LoadRun = {
      ...runCompleted,
      error_summary: {
        preflight_warnings: [
          {
            step_id: 'step-1',
            outcome_code: 'storage_error',
            error: 'S3 bucket temporarily unavailable',
          },
        ],
      },
    }
    vi.mocked(runsApi.get).mockResolvedValue(runWithPreflight)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    const banner = await screen.findByRole('status', { name: /preflight warnings/i })
    expect(within(banner).getByText(/total records is approximate/i)).toBeInTheDocument()
    expect(within(banner).getByText(/S3 bucket temporarily unavailable/)).toBeInTheDocument()
    expect(within(banner).getByText(/storage_error/)).toBeInTheDocument()
  })

  it('does not render the preflight warning banner when preflight_warnings is empty', async () => {
    const runNoWarnings: LoadRun = {
      ...runCompleted,
      error_summary: { preflight_warnings: [] },
    }
    vi.mocked(runsApi.get).mockResolvedValue(runNoWarnings)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByText('completed'))
    expect(screen.queryByRole('status', { name: /preflight warnings/i })).not.toBeInTheDocument()
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

  // ── Progress bars ─────────────────────────────────────────────────────────────

  it('renders run-level progress bar when total_records is set', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByRole('progressbar')).toBeInTheDocument()
    })
  })

  it('renders step-level progress bar when step has jobs with total_records', async () => {
    const jobWithTotals: JobRecord = {
      ...jobComplete,
      total_records: 1000,
      records_processed: 800,
    }
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobWithTotals])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => {
      // Run-level + step-level progress bars
      expect(screen.getAllByRole('progressbar').length).toBeGreaterThanOrEqual(2)
    })
  })

  it('does not render step-level progress bar when step has no jobs', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetail)

    renderRunDetail()
    await waitFor(() => screen.getByText('Account'))

    // Only the run-level progress bar should be present
    expect(screen.getAllByRole('progressbar')).toHaveLength(1)
  })

  // ── Query step rendering ──────────────────────────────────────────────────────

  it('renders "Query" operation badge for a query step', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetailWithQuery)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('Query')).toBeInTheDocument()
    })
  })

  it('renders "Query All (incl. deleted)" operation badge for a queryAll step', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([])
    vi.mocked(plansApi.get).mockResolvedValue(planDetailWithQuery)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText('Query All (incl. deleted)')).toBeInTheDocument()
    })
  })

  it('shows SOQL block in expanded query step panel', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobQueryComplete])
    vi.mocked(plansApi.get).mockResolvedValue(planDetailWithQuery)

    renderRunDetail()
    await waitFor(() => screen.getByLabelText('Step 1: Account'))

    // SOQL appears before expanding (it's in the header section)
    await waitFor(() => {
      expect(
        screen.getByText('SELECT Id, Name FROM Account WHERE CreatedDate = TODAY'),
      ).toBeInTheDocument()
    })
  })

  it('shows "rows returned" label in query step progress row', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobQueryComplete])
    vi.mocked(plansApi.get).mockResolvedValue(planDetailWithQuery)

    renderRunDetail()
    await waitFor(() => {
      expect(screen.getByText(/500 rows returned/)).toBeInTheDocument()
    })
  })

  it('does not show "Retry Failed Records" button for a query step with failed records', async () => {
    // Query ops don't support retry
    const jobQueryWithErrors: JobRecord = {
      ...jobQueryComplete,
      records_failed: 10,
    }
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobQueryWithErrors])
    vi.mocked(plansApi.get).mockResolvedValue(planDetailWithQuery)

    renderRunDetail()
    await waitFor(() => screen.getByText('Account'))

    expect(screen.queryByRole('button', { name: 'Retry Failed Records' })).not.toBeInTheDocument()
  })

  it('shows "rows returned" in expanded query job row', async () => {
    const user = userEvent.setup()
    vi.mocked(runsApi.get).mockResolvedValue(runCompleted)
    vi.mocked(runsApi.jobs).mockResolvedValue([jobQueryComplete])
    vi.mocked(plansApi.get).mockResolvedValue(planDetailWithQuery)

    renderRunDetail()
    await waitFor(() => screen.getByLabelText('Step 1: Account'))
    await user.click(screen.getByLabelText('Step 1: Account'))

    await waitFor(() => {
      // Multiple instances of "rows returned" are expected (step header + job row)
      expect(screen.getAllByText(/500 rows returned/).length).toBeGreaterThanOrEqual(1)
    })
  })
})
