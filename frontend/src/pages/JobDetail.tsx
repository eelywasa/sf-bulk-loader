import { type ReactNode, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { jobsApi, runsApi, plansApi } from '../api/endpoints'
import { apiFetchBlob } from '../api/client'
import { formatApiErrorStrict } from '../api/errors'
import type { CsvFetchParams, JobStatus } from '../api/types'
import { isQueryOperation, operationLabel } from '../api/types'
import { Badge, Button, Card, CsvPreviewPanel, Spinner, Tabs } from '../components/ui'
import type { BadgeVariant } from '../components/ui/Badge'
import { ALERT_ERROR } from '../components/ui/formStyles'
import { usePermission } from '../hooks/usePermission'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function truncateId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function jobStatusVariant(status: JobStatus): BadgeVariant {
  const map: Record<JobStatus, BadgeVariant> = {
    pending: 'pending',
    uploading: 'uploading',
    upload_complete: 'upload_complete',
    in_progress: 'in_progress',
    job_complete: 'job_complete',
    failed: 'failed',
    aborted: 'aborted',
  }
  return map[status] ?? 'neutral'
}

function basename(path: string): string {
  const segments = path.split('/')
  return segments[segments.length - 1] || path
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function MetaField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-content-muted uppercase tracking-wide mb-1">{label}</dt>
      <dd className="text-sm text-content-primary">{children}</dd>
    </div>
  )
}

interface LogSectionProps {
  label: string
  description: string
  downloadHref: string
  available: boolean
  queryKey: unknown[]
  fetchPage: (params: CsvFetchParams) => ReturnType<typeof jobsApi.previewSuccessCsv>
  filename?: string
}

function LogSection({
  label,
  description,
  downloadHref,
  available,
  queryKey,
  fetchPage,
  filename,
}: LogSectionProps) {
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  async function handleDownload() {
    setDownloading(true)
    setDownloadError(null)
    try {
      const { blob, filename: name } = await apiFetchBlob(downloadHref)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = name
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setDownloadError('Download failed')
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="border border-border-base rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-surface-sunken border-b border-border-base">
        <div>
          <p className="text-sm font-medium text-content-primary">{label}</p>
          <p className="text-xs text-content-muted mt-0.5">{description}</p>
          {downloadError && <p className="text-xs text-status-error mt-0.5">{downloadError}</p>}
        </div>
        {available ? (
          <button
            onClick={handleDownload}
            disabled={downloading}
            className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md bg-surface-raised border border-border-strong text-content-primary hover:bg-surface-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {downloading ? 'Downloading…' : '↓ Download'}
          </button>
        ) : (
          <span className="shrink-0 text-sm text-content-disabled italic">Not available</span>
        )}
      </div>

      {/* Preview */}
      {!available ? (
        <p className="px-4 py-3 text-sm text-content-disabled italic">No file generated for this job.</p>
      ) : (
        <div className="px-4 py-4">
          <CsvPreviewPanel queryKey={queryKey} fetchPage={fetchPage} filename={filename} />
        </div>
      )}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function JobDetail() {
  const { runId, jobId } = useParams<{ runId: string; jobId: string }>()
  const navigate = useNavigate()
  const canViewFileContents = usePermission('files.view_contents')

  const { data: job, isLoading, isError, error } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => jobsApi.get(jobId!),
    enabled: !!jobId,
  })

  // Fetch run + plan to retrieve the step's operation and SOQL
  const { data: run } = useQuery({
    queryKey: ['run', runId],
    queryFn: () => runsApi.get(runId!),
    enabled: !!runId,
  })

  const { data: planDetail } = useQuery({
    queryKey: ['plan', run?.load_plan_id],
    queryFn: () => plansApi.get(run!.load_plan_id),
    enabled: !!run?.load_plan_id,
  })

  const step = planDetail?.load_steps?.find((s) => s.id === job?.load_step_id)
  const isQuery = step ? isQueryOperation(step.operation) : false

  if (isLoading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[200px]">
        <Spinner size="lg" />
      </div>
    )
  }

  if (isError) {
    const message = formatApiErrorStrict(error, 'Failed to load job details')
    return (
      <div className="p-6 space-y-4">
        <div className={ALERT_ERROR}>
          <p>{message}</p>
        </div>
        <Button variant="secondary" onClick={() => navigate(`/runs/${runId}`)}>
          Back to Run
        </Button>
      </div>
    )
  }

  // ── Overview tab ─────────────────────────────────────────────────────────────

  const overviewContent = job ? (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4 py-2">
      <MetaField label="Job ID (internal)">{job.id}</MetaField>
      <MetaField label="Salesforce Job ID">
        {job.sf_job_id && job.sf_instance_url ? (
          <a
            href={`${job.sf_instance_url}/lightning/setup/AsyncApiJobStatus/page?address=%2F${job.sf_job_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-content-link hover:underline font-mono"
          >
            {job.sf_job_id}
          </a>
        ) : (
          job.sf_job_id ?? '—'
        )}
      </MetaField>
      {step && (
        <MetaField label="Operation">
          <Badge variant="neutral">{operationLabel(step.operation)}</Badge>
        </MetaField>
      )}
      {!isQuery && (
        <MetaField label="Partition Index">{job.partition_index}</MetaField>
      )}
      <MetaField label="Status">
        <Badge variant={jobStatusVariant(job.status)} dot>
          {job.status}
        </Badge>
      </MetaField>
      {isQuery ? (
        <MetaField label="Rows Returned">{job.records_processed ?? '—'}</MetaField>
      ) : (
        <>
          <MetaField label="Records Processed">{job.records_processed ?? '—'}</MetaField>
          <MetaField label="Records Failed">{job.records_failed ?? '—'}</MetaField>
        </>
      )}
      <MetaField label="Started At">{formatDate(job.started_at)}</MetaField>
      <MetaField label="Completed At">{formatDate(job.completed_at)}</MetaField>
      {job.error_message && (
        <div className="col-span-full">
          <MetaField label="Error Message">
            <p className="text-sm text-error-text font-mono whitespace-pre-wrap">{job.error_message}</p>
          </MetaField>
        </div>
      )}
      {isQuery && step?.soql && (
        <div className="col-span-full">
          <MetaField label="SOQL">
            <pre className="mt-1 rounded-md bg-surface-code text-content-code px-3 py-2 text-xs font-mono whitespace-pre-wrap leading-relaxed overflow-x-auto">
              {step.soql}
            </pre>
          </MetaField>
        </div>
      )}
    </dl>
  ) : null

  // ── Raw SF Payload tab ────────────────────────────────────────────────────────

  let payloadContent: ReactNode

  if (job?.sf_api_response) {
    let formatted: string
    try {
      formatted = JSON.stringify(JSON.parse(job.sf_api_response), null, 2)
    } catch {
      formatted = job.sf_api_response
    }
    payloadContent = (
      <div className="rounded-md bg-surface-code overflow-auto max-h-[500px]">
        <pre className="p-4 text-xs text-content-code font-mono whitespace-pre leading-relaxed">
          {formatted}
        </pre>
      </div>
    )
  } else {
    payloadContent = (
      <p className="text-sm text-content-disabled italic py-4">
        Not available — no Salesforce API response recorded for this job.
      </p>
    )
  }

  // ── Logs tab ──────────────────────────────────────────────────────────────────

  const logsContent = job ? (
    <div className="space-y-4 py-2">
      {isQuery ? (
        <LogSection
          label="Result File"
          description="Concatenated query result CSV produced by the query executor."
          downloadHref={jobsApi.successCsvUrl(jobId!)}
          available={!!job.success_file_path}
          queryKey={['job-preview', 'success', jobId]}
          fetchPage={(params) => jobsApi.previewSuccessCsv(jobId!, params)}
          filename={job.success_file_path ? basename(job.success_file_path) : undefined}
        />
      ) : (
        <>
          <LogSection
            label="Success CSV"
            description="Records successfully processed by Salesforce."
            downloadHref={jobsApi.successCsvUrl(jobId!)}
            available={!!job.success_file_path}
            queryKey={['job-preview', 'success', jobId]}
            fetchPage={(params) => jobsApi.previewSuccessCsv(jobId!, params)}
            filename={job.success_file_path ? basename(job.success_file_path) : undefined}
          />
          <LogSection
            label="Error CSV"
            description="Records that failed to process with error details."
            downloadHref={jobsApi.errorCsvUrl(jobId!)}
            available={!!job.error_file_path}
            queryKey={['job-preview', 'error', jobId]}
            fetchPage={(params) => jobsApi.previewErrorCsv(jobId!, params)}
            filename={job.error_file_path ? basename(job.error_file_path) : undefined}
          />
          <LogSection
            label="Unprocessed CSV"
            description="Records not submitted due to job cancellation."
            downloadHref={jobsApi.unprocessedCsvUrl(jobId!)}
            available={!!job.unprocessed_file_path}
            queryKey={['job-preview', 'unprocessed', jobId]}
            fetchPage={(params) => jobsApi.previewUnprocessedCsv(jobId!, params)}
            filename={job.unprocessed_file_path ? basename(job.unprocessed_file_path) : undefined}
          />
        </>
      )}
    </div>
  ) : null

  const tabs = [
    { id: 'overview', label: 'Overview', content: overviewContent },
    { id: 'payload', label: 'Raw SF Payload', content: payloadContent },
    ...(canViewFileContents
      ? [{ id: 'logs', label: 'Logs', content: logsContent }]
      : []),
  ]

  return (
    <div className="p-6 space-y-6">
      {/* Breadcrumb + title */}
      <div>
        <nav className="flex items-center gap-2 text-sm text-content-muted mb-1">
          <Link to="/runs" className="hover:text-content-primary transition-colors">
            Runs
          </Link>
          <span aria-hidden="true">›</span>
          <Link to={`/runs/${runId}`} className="hover:text-content-primary transition-colors">
            Run {runId ? truncateId(runId) : '…'}
          </Link>
          <span aria-hidden="true">›</span>
          <span className="text-content-primary">Job {jobId ? truncateId(jobId) : '…'}</span>
        </nav>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-content-primary">Job Detail</h1>
          {job && (
            <Badge variant={jobStatusVariant(job.status)} dot>
              {job.status}
            </Badge>
          )}
        </div>
      </div>

      {/* Main content card */}
      <Card padding={false}>
        <Tabs tabs={tabs} className="px-6 pt-4" />
      </Card>
    </div>
  )
}
