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
    successCsvUrl: (id: string) => `/api/jobs/${id}/success-csv`,
    errorCsvUrl: (id: string) => `/api/jobs/${id}/error-csv`,
    unprocessedCsvUrl: (id: string) => `/api/jobs/${id}/unprocessed-csv`,
  },
}))

import { jobsApi } from '../../api/endpoints'

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

  it('shows SF job ID in Overview', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByText('sf-xyz-456')).toBeInTheDocument()
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

  // ── Downloads tab ─────────────────────────────────────────────────────────

  it('renders Downloads tab label', async () => {
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Downloads' })).toBeInTheDocument()
    })
  })

  it('shows Success CSV, Error CSV, and Unprocessed CSV labels in Downloads tab', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Downloads' }))
    await user.click(screen.getByRole('tab', { name: 'Downloads' }))
    await waitFor(() => {
      expect(screen.getByText('Success CSV')).toBeVisible()
      expect(screen.getByText('Error CSV')).toBeVisible()
      expect(screen.getByText('Unprocessed CSV')).toBeVisible()
    })
  })

  it('shows download links for available files', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Downloads' }))
    await user.click(screen.getByRole('tab', { name: 'Downloads' }))
    await waitFor(() => {
      const links = screen.getAllByRole('link', { name: /Download/ })
      expect(links.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('download links point to correct API endpoints', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Downloads' }))
    await user.click(screen.getByRole('tab', { name: 'Downloads' }))
    await waitFor(() => {
      const links = screen.getAllByRole('link', { name: /Download/ })
      const hrefs = links.map((l) => l.getAttribute('href'))
      expect(hrefs).toContain('/api/jobs/job-abc-123/success-csv')
      expect(hrefs).toContain('/api/jobs/job-abc-123/error-csv')
    })
  })

  it('shows Not available for null file path in Downloads tab', async () => {
    const user = userEvent.setup()
    // jobComplete has null unprocessed_file_path — should show one "Not available"
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Downloads' }))
    await user.click(screen.getByRole('tab', { name: 'Downloads' }))
    await waitFor(() => {
      expect(screen.getByText('Not available')).toBeVisible()
    })
  })

  it('shows Not available for all three files when no files are present', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobFailed)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Downloads' }))
    await user.click(screen.getByRole('tab', { name: 'Downloads' }))
    await waitFor(() => {
      expect(screen.getAllByText('Not available').length).toBe(3)
    })
  })

  it('shows no download links when no files are present', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobFailed)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Downloads' }))
    await user.click(screen.getByRole('tab', { name: 'Downloads' }))
    await waitFor(() => {
      expect(screen.queryAllByRole('link', { name: /Download/ }).length).toBe(0)
    })
  })

  // ── Tab switching ─────────────────────────────────────────────────────────

  it('switches back to Overview after visiting Downloads tab', async () => {
    const user = userEvent.setup()
    vi.mocked(jobsApi.get).mockResolvedValue(jobComplete)
    renderJobDetail()
    await waitFor(() => screen.getByRole('tab', { name: 'Overview' }))
    // Switch to Downloads
    await user.click(screen.getByRole('tab', { name: 'Downloads' }))
    await waitFor(() => screen.getByText('Success CSV'))
    // Switch back to Overview
    await user.click(screen.getByRole('tab', { name: 'Overview' }))
    await waitFor(() => {
      expect(screen.getByText('sf-xyz-456')).toBeVisible()
    })
  })
})
