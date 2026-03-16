import { type ReactNode } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { jobsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { JobStatus } from '../api/types'
import { Badge, Button, Card, Tabs } from '../components/ui'
import type { BadgeVariant } from '../components/ui/Badge'

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

// ─── Sub-components ───────────────────────────────────────────────────────────

function MetaField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">{label}</dt>
      <dd className="text-sm text-gray-900">{children}</dd>
    </div>
  )
}

interface DownloadRowProps {
  label: string
  description: string
  href: string
  available: boolean
}

function DownloadRow({ label, description, href, available }: DownloadRowProps) {
  return (
    <div className="flex items-start justify-between gap-4 p-4 rounded-lg border border-gray-100 bg-gray-50">
      <div>
        <p className="text-sm font-medium text-gray-900">{label}</p>
        <p className="text-xs text-gray-500 mt-0.5">{description}</p>
      </div>
      {available ? (
        <a
          href={href}
          download
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 transition-colors"
        >
          ↓ Download
        </a>
      ) : (
        <span className="shrink-0 text-sm text-gray-400 italic">Not available</span>
      )}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function JobDetail() {
  const { runId, jobId } = useParams<{ runId: string; jobId: string }>()
  const navigate = useNavigate()

  const { data: job, isLoading, isError, error } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => jobsApi.get(jobId!),
    enabled: !!jobId,
  })

  if (isLoading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[200px]" aria-label="Loading">
        <div className="h-8 w-8 rounded-full border-4 border-blue-600 border-t-transparent animate-spin" />
      </div>
    )
  }

  if (isError) {
    const message = error instanceof ApiError ? error.message : 'Failed to load job details'
    return (
      <div className="p-6 space-y-4">
        <div className="rounded-md bg-red-50 border border-red-200 p-4">
          <p className="text-sm text-red-700">{message}</p>
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
      <MetaField label="Salesforce Job ID">{job.sf_job_id ?? '—'}</MetaField>
      <MetaField label="Partition Index">{job.partition_index}</MetaField>
      <MetaField label="Status">
        <Badge variant={jobStatusVariant(job.status)} dot>
          {job.status}
        </Badge>
      </MetaField>
      <MetaField label="Records Processed">{job.records_processed ?? '—'}</MetaField>
      <MetaField label="Records Failed">{job.records_failed ?? '—'}</MetaField>
      <MetaField label="Started At">{formatDate(job.started_at)}</MetaField>
      <MetaField label="Completed At">{formatDate(job.completed_at)}</MetaField>
      {job.error_message && (
        <div className="col-span-full">
          <MetaField label="Error Message">
            <p className="text-sm text-red-700 font-mono whitespace-pre-wrap">{job.error_message}</p>
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
      <div className="rounded-md bg-gray-900 overflow-auto max-h-[500px]">
        <pre className="p-4 text-xs text-gray-100 font-mono whitespace-pre leading-relaxed">
          {formatted}
        </pre>
      </div>
    )
  } else {
    payloadContent = (
      <p className="text-sm text-gray-400 italic py-4">
        Not available — no Salesforce API response recorded for this job.
      </p>
    )
  }

  // ── Downloads tab ─────────────────────────────────────────────────────────────

  const downloadsContent = job ? (
    <div className="space-y-3 py-2">
      <DownloadRow
        label="Success CSV"
        description="Records successfully processed by Salesforce."
        href={jobsApi.successCsvUrl(jobId!)}
        available={!!job.success_file_path}
      />
      <DownloadRow
        label="Error CSV"
        description="Records that failed to process with error details."
        href={jobsApi.errorCsvUrl(jobId!)}
        available={!!job.error_file_path}
      />
      <DownloadRow
        label="Unprocessed CSV"
        description="Records not submitted due to job cancellation."
        href={jobsApi.unprocessedCsvUrl(jobId!)}
        available={!!job.unprocessed_file_path}
      />
    </div>
  ) : null

  const tabs = [
    { id: 'overview', label: 'Overview', content: overviewContent },
    { id: 'payload', label: 'Raw SF Payload', content: payloadContent },
    { id: 'downloads', label: 'Downloads', content: downloadsContent },
  ]

  return (
    <div className="p-6 space-y-6">
      {/* Breadcrumb + title */}
      <div>
        <nav className="flex items-center gap-2 text-sm text-gray-500 mb-1">
          <Link to="/runs" className="hover:text-gray-900 transition-colors">
            Runs
          </Link>
          <span aria-hidden="true">›</span>
          <Link to={`/runs/${runId}`} className="hover:text-gray-900 transition-colors">
            Run {runId ? truncateId(runId) : '…'}
          </Link>
          <span aria-hidden="true">›</span>
          <span className="text-gray-900">Job {jobId ? truncateId(jobId) : '…'}</span>
        </nav>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Job Detail</h1>
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
