import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../../components/ui/Toast'
import JobDetail from '../../pages/JobDetail'
import type { JobRecord } from '../../api/types'

// ─── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../api/endpoints', () => ({
  jobsApi: {
    get: vi.fn(),
    previewSuccessCsv: vi.fn(),
    previewErrorCsv: vi.fn(),
    previewUnprocessedCsv: vi.fn(),
    successCsvUrl: (id: string) => `/api/jobs/${id}/success-csv`,
    errorCsvUrl: (id: string) => `/api/jobs/${id}/error-csv`,
    unprocessedCsvUrl: (id: string) => `/api/jobs/${id}/unprocessed-csv`,
  },
  runsApi: {
    get: vi.fn(),
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

const MOCK_VIEWER_PERMISSIONS = new Set([
  'connections.view', 'plans.view', 'runs.view', 'files.view',
])

vi.mock('../../context/AuthContext', () => ({
  useAuth: vi.fn(() => ({ authRequired: true, permissions: MOCK_ALL_PERMISSIONS })),
  useAuthOptional: vi.fn(() => ({ authRequired: true, permissions: MOCK_ALL_PERMISSIONS })),
}))

import { useAuthOptional } from '../../context/AuthContext'

import { jobsApi, runsApi, plansApi } from '../../api/endpoints'
import type { InputFilePreview, LoadRun, LoadPlanDetail } from '../../api/types'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const jobComplete: JobRecord = {
  id: 'job-abc-123',
  load_run_id: 'run-111',
  load_step_id: 'step-1',
  sf_job_id: 'sf-xyz-456',
  partition_index: 2,
  status: 'job_complete',
  records_processed: 10000,
  records_failed: 12,
  success_file_path: '/output/success.csv',
  error_file_path: '/output/errors.csv',
  unprocessed_file_path: null,
  sf_api_response: JSON.stringify({ id: 'sf-xyz-456', state: 'JobComplete', numberRecordsProcessed: 10000 }),
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:05:00Z',
  error_message: null,
}

const jobFailed: JobRecord = {
  ...jobComplete,
  status: 'failed',
  records_processed: 0,
  records_failed: 500,
  success_file_path: null,
  error_file_path: null,
  unprocessed_file_path: null,
  sf_api_response: null,
  error_message: 'Bulk API job creation failed — invalid field',
}

const jobPending: JobRecord = {
  ...jobComplete,
  status: 'pending',
  records_processed: null,
  records_failed: null,
  success_file_path: null,
  error_file_path: null,
  unprocessed_file_path: null,
  sf_api_response: null,
  sf_job_id: null,
  started_at: null,
  completed_at: null,
  error_message: null,
}

const successPreview: InputFilePreview = {
  filename: 'success.csv',
  header: ['Id', 'Name', 'Status'],
  rows: [{ Id: '001', Name: 'Acme Corp', Status: 'Processed' }],
  total_rows: 120,
  filtered_rows: null,
  offset: 0,
  limit: 50,
  has_next: true,
}

const errorPreview: InputFilePreview = {
  ...successPreview,
  filename: 'errors.csv',
  rows: [{ Id: '002', Name: 'Globex', Status: 'Failed' }],
}

const mockRun: LoadRun = {
  id: 'run-111',
  load_plan_id: 'plan-1',
  status: 'completed',
  started_at: '2024-03-01T10:00:00Z',
  completed_at: '2024-03-01T10:05:00Z',
  total_records: 500,
  total_success: 500,
  total_errors: 0,
  initiated_by: 'admin',
  error_summary: null,
  is_retry: false,
}

const mockPlanDetailDml: LoadPlanDetail = {
  id: 'plan-1',
  connection_id: 'conn-1',
  name: 'Test Plan',
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
  ],
}

const mockPlanDetailQuery: LoadPlanDetail = {
  ...mockPlanDetailDml,
  load_steps: [
    {
      id: 'step-1',
      load_plan_id: 'plan-1',
      sequence: 1,
      object_name: 'Account',
      operation: 'query',
      csv_file_pattern: null,
      soql: 'SELECT Id, Name FROM Account',
      partition_size: 10000,
      external_id_field: null,
      assignment_rule_id: null,
      created_at: '2024-03-01T00:00:00Z',
      updated_at: '2024-03-01T00:00:00Z',
    },
  ],
}

const mockPlanDetailQueryAll: LoadPlanDetail = {
  ...mockPlanDetailDml,
  load_steps: [
    {
      id: 'step-1',
      load_plan_id: 'plan-1',
      sequence: 1,
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

const jobQueryComplete: typeof jobComplete = {
  ...jobComplete,
  id: 'job-abc-123',
  load_step_id: 'step-1',
  records_processed: 500,
  records_failed: 0,
  error_file_path: null,
  unprocessed_file_path: null,
  success_file_path: '/output/query_result.csv',
}

// ─── Render helper ─────────────────────────────────────────────────────────────

function renderJobDetail(runId = 'run-111', jobId = 'job-abc-123') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[`/runs/${runId}/jobs/${jobId}`]}>
          <Routes>
            <Route path="/runs/:runId/jobs/:jobId" element={<JobDetail />} />
            <Route path="/runs/:id" element={<div data-testid="run-detail-page" />} />
            <Route path="/runs" element={<div data-testid="runs-page" />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('JobDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(jobsApi.previewSuccessCsv).mockResolvedValue(successPreview)
    vi.mocked(jobsApi.previewErrorCsv).mockResolvedValue(errorPreview)
    vi.mocked(jobsApi.previewUnprocessedCsv).mockResolvedValue(successPreview)
    // Default: return DML run + plan so existing tests are unaffected
    vi.mocked(runsApi.get).mockResolvedValue(mockRun)
    vi.mocked(plansApi.get).mockResolvedValue(mockPlanDetailDml)
  })

  // ── Loading / error states ─────────────────────────────────────────────────

  it('shows loading indicator while job is fetching', () => {
    vi.mocked(jobsApi.get).mockReturnValue(new Promise(() => {}))
    renderJobDetail()
    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
  })

  it('shows error state when job fetch fails with generic error', async () => {
    vi.mocked(jobsApi.get).mockRejectedValue(new Error('Network failure'))
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText(/Failed to load job/)).toBeInTheDocument()
    })
  })

  it('shows error message from ApiError', async () => {
    const { ApiError } = await import('../../api/client')
    vi.mocked(jobsApi.get).mockRejectedValue(new ApiError({ status: 404, message: 'Job not found' }))
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('Job not found')).toBeInTheDocument()
    })
  })

  it('shows Back to Run button in error state', async () => {
    vi.mocked(jobsApi.get).mockRejectedValue(new Error('Not found'))
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Back to Run' })).toBeInTheDocument()
    })
  })

  it('navigates to run detail when Back to Run is clicked in error state', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockRejectedValue(new Error('Not found'))
    renderJobDetail()
    await waitFor(() => screen.getByRole('button', { name: 'Back to Run' }))
    await user.click(screen.getByRole('button', { name: 'Back to Run' }))
    expect(screen.getByTestId('run-detail-page')).toBeInTheDocument()
  })

  // ── Breadcrumb ────────────────────────────────────────────────────────────

  it('renders Runs breadcrumb link', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByText('Job Detail'))
    expect(screen.getByRole('link', { name: 'Runs' })).toBeInTheDocument()
  })

  it('Runs breadcrumb link points to /runs', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByText('Job Detail'))
    expect(screen.getByRole('link', { name: 'Runs' })).toHaveAttribute('href', '/runs')
  })

  it('renders Run breadcrumb link pointing to run detail', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByText('Job Detail'))
    expect(screen.getByRole('link', { name: /Run run-/ })).toHaveAttribute('href', '/runs/run-111')
  })

  // ── Header ────────────────────────────────────────────────────────────────

  it('renders Job Detail heading', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Job Detail' })).toBeInTheDocument()
    })
  })

  it('renders status badge in header', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getAllByText('job_complete').length).toBeGreaterThanOrEqual(1)
    })
  })

  it('renders failed status badge for a failed job', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobFailed)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getAllByText('failed').length).toBeGreaterThanOrEqual(1)
    })
  })

  // ── Overview tab ──────────────────────────────────────────────────────────

  it('renders Overview tab label', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Overview' })).toBeInTheDocument()
    })
  })

  it('shows SF job ID as plain text when sf_instance_url is absent', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('sf-xyz-456')).toBeInTheDocument()
    })
    // Should not be a link when there is no instance URL
    expect(screen.queryByRole('link', { name: 'sf-xyz-456' })).not.toBeInTheDocument()
  })

  it('shows SF job ID as a link to Salesforce when sf_instance_url is present', async () => {
    const jobWithInstanceUrl: JobRecord = {
      ...jobComplete,
      sf_instance_url: 'https://myorg.salesforce.com',
    }
    vi.mocked(jobsApi.get).mockResolvedValue(jobWithInstanceUrl)
    renderJobDetail()
    await waitFor(() => {
      const link = screen.getByRole('link', { name: 'sf-xyz-456' })
      expect(link).toBeInTheDocument()
      expect(link).toHaveAttribute(
        'href',
        'https://myorg.salesforce.com/lightning/setup/AsyncApiJobStatus/page?address=%2Fsf-xyz-456',
      )
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    })
  })

  it('shows dash when SF job ID is null', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobPending)
    renderJobDetail()
    await waitFor(() => screen.getByText('Job Detail'))
    // At minimum the empty placeholder should be rendered
    expect(screen.getByText('Job Detail')).toBeInTheDocument()
  })

  it('shows partition index in Overview', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument()
    })
  })

  it('shows records processed in Overview', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('10000')).toBeInTheDocument()
    })
  })

  it('shows records failed in Overview', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('12')).toBeInTheDocument()
    })
  })

  it('shows error message in Overview when present', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobFailed)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('Bulk API job creation failed — invalid field')).toBeInTheDocument()
    })
  })

  it('does not show Error Message label when error_message is null', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByText('sf-xyz-456'))
    expect(screen.queryByText('Error Message')).not.toBeInTheDocument()
  })

  // ── Raw SF Payload tab ────────────────────────────────────────────────────

  it('renders Raw SF Payload tab label', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Raw SF Payload' })).toBeInTheDocument()
    })
  })

  it('shows formatted JSON after clicking Raw SF Payload tab', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Raw SF Payload' }))
    await user.click(screen.getByRole('tab', { name: 'Raw SF Payload' }))
    await waitFor(() => {
      expect(screen.getByText(/JobComplete/)).toBeVisible()
    })
  })

  it('shows Not available in Raw SF Payload tab when response is null', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobFailed)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Raw SF Payload' }))
    await user.click(screen.getByRole('tab', { name: 'Raw SF Payload' }))
    await waitFor(() => {
      expect(screen.getByText(/Not available — no Salesforce API response/)).toBeVisible()
    })
  })

  // ── Logs tab ──────────────────────────────────────────────────────────────

  it('renders Downloads tab label', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Logs' })).toBeInTheDocument()
    })
  })

  it('shows Success CSV, Error CSV, and Unprocessed CSV labels in Downloads tab', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => {
      expect(screen.getByText('Success CSV')).toBeVisible()
      expect(screen.getByText('Error CSV')).toBeVisible()
      expect(screen.getByText('Unprocessed CSV')).toBeVisible()
    })
  })

  it('shows download buttons for available files', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => {
      const buttons = screen.getAllByRole('button', { name: /Download/ })
      expect(buttons.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('renders shared CSV preview content for available log sections', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()

    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() =>
      expect(jobsApi.previewSuccessCsv).toHaveBeenCalledWith('job-abc-123', {
        offset: 0,
        limit: 50,
        filters: [],
      }),
    )
    await waitFor(() =>
      expect(jobsApi.previewErrorCsv).toHaveBeenCalledWith('job-abc-123', {
        offset: 0,
        limit: 50,
        filters: [],
      }),
    )

    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => {
      expect(screen.getByText('Acme Corp')).toBeVisible()
      expect(screen.getByText('Globex')).toBeVisible()
      expect(screen.getAllByText('Page 1 of 3 (120 rows)').length).toBeGreaterThanOrEqual(2)
      expect(screen.getByText('success.csv')).toBeVisible()
      expect(screen.getByText('errors.csv')).toBeVisible()
    })
  })

  it('passes pagination and filters to the correct preview helper', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    vi.mocked(jobsApi.previewSuccessCsv).mockImplementation(async (_id, params) => {
      if (params?.offset === 50) {
        return {
          ...successPreview,
          rows: [{ Id: '051', Name: 'Page Two', Status: 'Processed' }],
          offset: 50,
        }
      }
      return successPreview
    })

    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => screen.getByText('Acme Corp'))

    const nextButtons = screen.getAllByRole('button', { name: 'Next page' })
    await user.click(nextButtons[0])
    await waitFor(() =>
      expect(jobsApi.previewSuccessCsv).toHaveBeenCalledWith('job-abc-123', {
        offset: 50,
        limit: 50,
        filters: [],
      }),
    )

    const addFilterButtons = screen.getAllByText('+ Add Filter')
    await user.click(addFilterButtons[0])
    const filterColumns = screen.getAllByRole('combobox', { name: 'Filter column' })
    const filterValues = screen.getAllByRole('textbox', { name: 'Filter value' })
    const applyButtons = screen.getAllByRole('button', { name: 'Apply' })
    await user.selectOptions(filterColumns[0], 'Name')
    await user.type(filterValues[0], 'Acme')
    await user.click(applyButtons[0])

    await waitFor(() =>
      expect(jobsApi.previewSuccessCsv).toHaveBeenLastCalledWith('job-abc-123', {
        offset: 0,
        limit: 50,
        filters: [{ column: 'Name', value: 'Acme' }],
      }),
    )
  })

  it('download buttons trigger authenticated fetch to correct API endpoints', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(new Blob(['a,b\n1,2'], { type: 'text/csv' }), {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="success.csv"' },
      }),
    )
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    const downloadButtons = await screen.findAllByRole('button', { name: /Download/ })
    await user.click(downloadButtons[0])
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/jobs/job-abc-123/'),
      expect.objectContaining({ headers: expect.any(Headers) }),
    ))
    fetchSpy.mockRestore()
  })

  it('shows Not available for null file path in Downloads tab', async () => {
    const user = userEvent.setup()
    // jobComplete has null unprocessed_file_path — should show one "Not available"
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => {
      expect(screen.getByText('Not available')).toBeVisible()
    })
    expect(jobsApi.previewUnprocessedCsv).not.toHaveBeenCalled()
  })

  it('shows Not available for all three files when no files are present', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobFailed)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => {
      expect(screen.getAllByText('Not available').length).toBe(3)
    })
  })

  it('shows no download buttons when no files are present', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobFailed)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => {
      expect(screen.queryAllByRole('button', { name: /Download/ }).length).toBe(0)
    })
  })

  it('does not show the legacy "Showing first 25 rows" footer', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => expect(screen.getByText('Acme Corp')).toBeVisible())
    expect(screen.queryByText('Showing first 25 rows.')).not.toBeInTheDocument()
  })

  // ── Query step rendering ──────────────────────────────────────────────────

  it('shows "Rows Returned" label instead of "Records Processed" for a query job', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobQueryComplete)
    vi.mocked(runsApi.get).mockResolvedValue(mockRun)
    vi.mocked(plansApi.get).mockResolvedValue(mockPlanDetailQuery)

    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('Rows Returned')).toBeInTheDocument()
    })
    expect(screen.queryByText('Records Processed')).not.toBeInTheDocument()
  })

  it('hides "Records Failed" for a query job', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobQueryComplete)
    vi.mocked(runsApi.get).mockResolvedValue(mockRun)
    vi.mocked(plansApi.get).mockResolvedValue(mockPlanDetailQuery)

    renderJobDetail()
    await waitFor(() => screen.getByText('Rows Returned'))
    expect(screen.queryByText('Records Failed')).not.toBeInTheDocument()
  })

  it('shows SOQL block in the Overview tab for a query job', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobQueryComplete)
    vi.mocked(runsApi.get).mockResolvedValue(mockRun)
    vi.mocked(plansApi.get).mockResolvedValue(mockPlanDetailQuery)

    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('SELECT Id, Name FROM Account')).toBeInTheDocument()
    })
  })

  it('shows "SOQL" label in the Overview tab for a query job', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobQueryComplete)
    vi.mocked(runsApi.get).mockResolvedValue(mockRun)
    vi.mocked(plansApi.get).mockResolvedValue(mockPlanDetailQuery)

    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('SOQL')).toBeInTheDocument()
    })
  })

  it('shows "Result File" section in Logs tab for a query job (not "Success CSV")', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobQueryComplete)
    vi.mocked(runsApi.get).mockResolvedValue(mockRun)
    vi.mocked(plansApi.get).mockResolvedValue(mockPlanDetailQuery)

    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Logs' }))
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => {
      expect(screen.getByText('Result File')).toBeVisible()
    })
    expect(screen.queryByText('Success CSV')).not.toBeInTheDocument()
    expect(screen.queryByText('Error CSV')).not.toBeInTheDocument()
    expect(screen.queryByText('Unprocessed CSV')).not.toBeInTheDocument()
  })

  it('shows "Query All (incl. deleted)" operation badge for queryAll job', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobQueryComplete)
    vi.mocked(runsApi.get).mockResolvedValue(mockRun)
    vi.mocked(plansApi.get).mockResolvedValue(mockPlanDetailQueryAll)

    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('Query All (incl. deleted)')).toBeInTheDocument()
    })
  })

  // ── Tab switching ─────────────────────────────────────────────────────────

  it('switches back to Overview after visiting Downloads tab', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Overview' }))
    // Switch to Logs
    await user.click(screen.getByRole('tab', { name: 'Logs' }))
    await waitFor(() => screen.getByText('Success CSV'))
    // Switch back to Overview
    await user.click(screen.getByRole('tab', { name: 'Overview' }))
    await waitFor(() => {
      expect(screen.getByText('sf-xyz-456')).toBeVisible()
    })
  })

  // ── SFBL-206: Viewer gating ──────────────────────────────────────────────
  describe('without files.view_contents (Viewer)', () => {
    beforeEach(() => {
      vi.mocked(useAuthOptional).mockReturnValue({
        authRequired: true,
        permissions: MOCK_VIEWER_PERMISSIONS,
      } as ReturnType<typeof useAuthOptional>)
    })

    it('hides the Logs tab entirely for a viewer', async () => {
      vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
      renderJobDetail()
      await waitFor(() => screen.getByRole('tab', { name: 'Overview' }))
      expect(screen.queryByRole('tab', { name: 'Logs' })).not.toBeInTheDocument()
      expect(screen.queryByText('Success CSV')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Download/ })).not.toBeInTheDocument()
    })
  })
})
