import { useState, useMemo, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { runsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { JobRecord, LoadStep } from '../api/types'
import { useLiveRun } from '../hooks/useLiveRun'
import { Badge, Button, Card, Modal, Progress } from '../components/ui'
import type { BadgeVariant } from '../components/ui/Badge'
import { useToast } from '../components/ui/Toast'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function formatElapsed(startedAt: string | null | undefined, completedAt: string | null | undefined): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = completedAt ? new Date(completedAt).getTime() : Date.now()
  const ms = end - start
  const totalSeconds = Math.floor(ms / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

/** Derive a display status for a step based on its jobs. */
function deriveStepStatus(
  jobs: JobRecord[],
): { label: string; variant: BadgeVariant } {
  if (jobs.length === 0) return { label: 'pending', variant: 'pending' }

  const statuses = jobs.map((j) => j.status)

  if (statuses.some((s) => s === 'failed')) return { label: 'failed', variant: 'failed' }
  if (statuses.some((s) => s === 'aborted')) return { label: 'aborted', variant: 'aborted' }
  if (statuses.every((s) => s === 'job_complete')) return { label: 'complete', variant: 'completed' }
  if (statuses.some((s) => s === 'in_progress' || s === 'upload_complete' || s === 'uploading'))
    return { label: 'running', variant: 'running' }
  return { label: 'pending', variant: 'pending' }
}

// ─── Step panel ───────────────────────────────────────────────────────────────

interface StepPanelProps {
  step: LoadStep
  jobs: JobRecord[]
  runId: string
  defaultExpanded?: boolean
}

function StepPanel({ step, jobs, runId, defaultExpanded = false }: StepPanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const stepStatus = deriveStepStatus(jobs)

  const totalProcessed = jobs.reduce((n, j) => n + (j.records_processed ?? 0), 0)
  const totalFailed = jobs.reduce((n, j) => n + (j.records_failed ?? 0), 0)

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      {/* Header (always visible) */}
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 hover:bg-gray-100 text-left"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={`Step ${step.sequence}: ${step.object_name}`}
      >
        <div className="flex items-center gap-3 flex-wrap min-w-0">
          <span className="text-xs font-mono font-semibold text-gray-500 shrink-0">
            #{step.sequence}
          </span>
          <span className="font-medium text-gray-900 truncate">{step.object_name}</span>
          <Badge variant="neutral" className="capitalize">
            {step.operation}
          </Badge>
          <Badge variant={stepStatus.variant}>{stepStatus.label}</Badge>
          <span className="text-xs text-gray-500">{jobs.length} job{jobs.length !== 1 ? 's' : ''}</span>
          {jobs.length > 0 && (
            <span className="text-xs text-gray-500">
              {totalProcessed} processed · {totalFailed} failed
            </span>
          )}
        </div>
        <span className="ml-2 text-gray-400 shrink-0 text-xs">{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Expanded job list */}
      {expanded && (
        <div className="divide-y divide-gray-100">
          {jobs.length === 0 ? (
            <p className="px-4 py-3 text-sm text-gray-400 italic">No jobs started yet.</p>
          ) : (
            jobs.map((job) => (
              <div
                key={job.id}
                className="flex items-start justify-between px-4 py-3 text-sm hover:bg-gray-50"
              >
                <div className="flex flex-col gap-1 min-w-0 flex-1">
                  <div className="flex items-center gap-3 flex-wrap min-w-0">
                    <span className="text-xs text-gray-500 font-mono shrink-0">
                      Part {job.partition_index}
                    </span>
                    <Badge variant={job.status as BadgeVariant}>{job.status}</Badge>
                    {job.records_processed != null && (
                      <span className="text-gray-600">
                        {job.records_processed} processed
                        {(job.records_failed ?? 0) > 0 && (
                          <span className="text-red-600 ml-1">
                            · {job.records_failed} failed
                          </span>
                        )}
                      </span>
                    )}
                    {job.error_message && (
                      <span className="text-red-500 text-xs truncate max-w-[20rem]" title={job.error_message}>
                        {job.error_message}
                      </span>
                    )}
                  </div>
                  {job.status === 'in_progress' && (job.total_records ?? 0) > 0 && (
                    <Progress
                      value={Math.round(((job.records_processed ?? 0) / job.total_records!) * 100)}
                      label={`${(job.records_processed ?? 0).toLocaleString()} / ${job.total_records!.toLocaleString()} records`}
                      showValue
                      color="blue"
                      size="sm"
                      className="max-w-xs"
                    />
                  )}
                </div>
                <Link
                  to={`/runs/${runId}/jobs/${job.id}`}
                  className="ml-2 text-blue-600 hover:underline text-xs shrink-0"
                  onClick={(e) => e.stopPropagation()}
                >
                  Details
                </Link>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function RunDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { success: toastSuccess, error: toastError } = useToast()

  const [abortModalOpen, setAbortModalOpen] = useState(false)
  const [includeSuccess, setIncludeSuccess] = useState(true)
  const [includeErrors, setIncludeErrors] = useState(true)
  const [includeUnprocessed, setIncludeUnprocessed] = useState(true)

  const { run, jobs, planDetail, isLoading, isError, error, isLive } = useLiveRun(id ?? '')

  // ── Abort mutation ──────────────────────────────────────────────────────────
  const abortMutation = useMutation({
    mutationFn: () => runsApi.abort(id!),
    onSuccess: () => {
      toastSuccess('Abort request sent.')
      setAbortModalOpen(false)
      // Refresh run data immediately
      void queryClient.invalidateQueries({ queryKey: ['runs', id] })
    },
    onError: (err: unknown) => {
      setAbortModalOpen(false)
      if (err instanceof ApiError && err.status === 409) {
        toastError('Run is not abortable (already finished or abort in progress).')
      } else {
        toastError('Failed to abort run.')
      }
    },
  })

  // ── Step grouping ───────────────────────────────────────────────────────────
  const jobsByStep = useMemo(() => {
    const grouped: Record<string, JobRecord[]> = {}
    for (const job of jobs) {
      if (!grouped[job.load_step_id]) grouped[job.load_step_id] = []
      grouped[job.load_step_id].push(job)
    }
    return grouped
  }, [jobs])

  const sortedSteps = useMemo(
    () => [...(planDetail?.load_steps ?? [])].sort((a, b) => a.sequence - b.sequence),
    [planDetail],
  )

  // ── Live aggregates from jobs (used while run is in progress) ───────────────
  const liveSuccess = useMemo(() => jobs.reduce((n, j) => n + (j.records_processed ?? 0), 0), [jobs])
  const liveErrors = useMemo(() => jobs.reduce((n, j) => n + (j.records_failed ?? 0), 0), [jobs])
  const liveTotal = useMemo(() => liveSuccess + liveErrors, [liveSuccess, liveErrors])

  // Prefer run-level totals once finalised, otherwise use live job aggregates
  const displaySuccess = (run?.total_success != null && !isLive) ? run.total_success : liveSuccess
  const displayErrors = (run?.total_errors != null && !isLive) ? run.total_errors : liveErrors
  const displayTotal = (run?.total_records != null && !isLive) ? run.total_records : (liveTotal > 0 ? liveTotal : (run?.total_records ?? null))

  // ── Progress calculation ────────────────────────────────────────────────────
  const successPct = useMemo(() => {
    if (!displayTotal || displayTotal === 0) return 0
    return Math.round((displaySuccess / displayTotal) * 100)
  }, [displaySuccess, displayTotal])

  const noneSelected = !includeSuccess && !includeErrors && !includeUnprocessed
  const hasLogs = jobs.some(
    (j) => j.success_file_path || j.error_file_path || j.unprocessed_file_path,
  )

  const handleDownloadLogs = useCallback(() => {
    if (!id) return
    const url = runsApi.logsZipUrl(id, {
      success: includeSuccess,
      errors: includeErrors,
      unprocessed: includeUnprocessed,
    })
    const a = document.createElement('a')
    a.href = url
    a.download = `run_${id.slice(0, 8)}_logs.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }, [id, includeSuccess, includeErrors, includeUnprocessed])

  const progressColor = useMemo(() => {
    if (run?.status === 'failed') return 'red' as const
    if (run?.status === 'completed_with_errors') return 'orange' as const
    if (run?.status === 'aborted') return 'orange' as const
    if (displayErrors > 0) return 'orange' as const
    return 'green' as const
  }, [run])

  // ── Loading / error states ──────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="p-6">
        <p className="text-sm text-gray-400" aria-label="Loading">
          Loading run…
        </p>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="p-6 space-y-4">
        <nav className="flex items-center gap-2 text-sm text-gray-500">
          <Link to="/runs" className="hover:text-gray-900">
            Runs
          </Link>
          <span>›</span>
          <span className="text-gray-900">Error</span>
        </nav>
        <p className="text-sm text-red-500">
          Failed to load run.{' '}
          {error instanceof Error ? error.message : ''}
        </p>
        <Button variant="secondary" onClick={() => navigate('/runs')}>
          Back to Runs
        </Button>
      </div>
    )
  }

  if (!run) {
    return (
      <div className="p-6 space-y-4">
        <p className="text-sm text-gray-500">Run not found.</p>
        <Button variant="secondary" onClick={() => navigate('/runs')}>
          Back to Runs
        </Button>
      </div>
    )
  }

  // ── Main render ─────────────────────────────────────────────────────────────
  return (
    <div className="p-6 space-y-6">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-2 text-sm text-gray-500">
        <Link to="/runs" className="hover:text-gray-900">
          Runs
        </Link>
        <span>›</span>
        <span className="text-gray-900 font-mono">{run.id}</span>
      </nav>

      {/* Sticky summary header */}
      <div className="sticky top-0 z-10 bg-white border border-gray-200 rounded-lg px-6 py-4 shadow-sm space-y-3">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-bold text-gray-900">
              Run{' '}
              <span className="font-mono text-base">{run.id}</span>
            </h1>
            <Badge variant={run.status} dot>
              {run.status}
            </Badge>
            {isLive && (
              <span className="text-xs text-blue-500 animate-pulse">
                Polling…
              </span>
            )}
          </div>

          {/* Abort button — only while live */}
          {isLive && (
            <Button
              variant="danger"
              size="sm"
              onClick={() => setAbortModalOpen(true)}
            >
              Abort Run
            </Button>
          )}
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Total Records" value={displayTotal ?? '—'} />
          <Stat label="Successes" value={displaySuccess} valueClass="text-green-700" />
          <Stat label="Errors" value={displayErrors} valueClass={displayErrors > 0 ? 'text-red-700' : undefined} />
          <Stat label="Elapsed" value={formatElapsed(run.started_at, run.completed_at)} />
        </div>

        {/* Progress bar (only when we have record counts) */}
        {(displayTotal ?? 0) > 0 && (
          <Progress
            value={successPct}
            label={`${displaySuccess} / ${displayTotal} records succeeded`}
            showValue
            color={progressColor}
          />
        )}

        {/* Timestamps */}
        <div className="flex gap-6 text-xs text-gray-500 flex-wrap">
          <span>Started: {formatDate(run.started_at)}</span>
          {run.completed_at && (
            <span>Completed: {formatDate(run.completed_at)}</span>
          )}
          {run.initiated_by && <span>By: {run.initiated_by}</span>}
          {planDetail && (
            <Link
              to={`/plans/${run.load_plan_id}`}
              className="text-blue-600 hover:underline"
            >
              Plan: {planDetail.name}
            </Link>
          )}
        </div>

        {/* Error summary */}
        {run.error_summary && (
          <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-1.5">
            {run.error_summary}
          </p>
        )}
      </div>

      {/* Step accordion */}
      <Card title="Steps">
        {sortedSteps.length === 0 && !planDetail && (
          <p className="text-sm text-gray-400 py-4 text-center">
            {isLive ? 'Loading plan steps…' : 'No step information available.'}
          </p>
        )}
        {sortedSteps.length > 0 && (
          <div className="space-y-2">
            {sortedSteps.map((step) => (
              <StepPanel
                key={step.id}
                step={step}
                jobs={jobsByStep[step.id] ?? []}
                runId={run.id}
              />
            ))}
          </div>
        )}
      </Card>

      {/* Download Logs */}
      <Card title="Download Logs">
        <div className="space-y-4">
          <p className="text-sm text-gray-500">Select the log types to include in the ZIP download.</p>
          <div className="flex flex-wrap gap-6">
            {(
              [
                { id: 'success', label: 'Success Logs', checked: includeSuccess, set: setIncludeSuccess },
                { id: 'errors', label: 'Error Logs', checked: includeErrors, set: setIncludeErrors },
                { id: 'unprocessed', label: 'Unprocessed Records', checked: includeUnprocessed, set: setIncludeUnprocessed },
              ] as const
            ).map(({ id: cbId, label, checked, set }) => (
              <label key={cbId} className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(e) => set(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-sm text-gray-700">{label}</span>
              </label>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              onClick={handleDownloadLogs}
              disabled={noneSelected || !hasLogs}
            >
              ↓ Download ZIP
            </Button>
            {!hasLogs && (
              <span className="text-xs text-gray-400 italic">No log files available yet.</span>
            )}
          </div>
        </div>
      </Card>

      {/* Abort confirmation modal */}
      <Modal
        open={abortModalOpen}
        onClose={() => setAbortModalOpen(false)}
        title="Abort Run"
        size="sm"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setAbortModalOpen(false)}
              disabled={abortMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={abortMutation.isPending}
              onClick={() => abortMutation.mutate()}
            >
              Abort
            </Button>
          </>
        }
      >
        <p className="text-sm text-gray-700">
          Are you sure you want to abort run{' '}
          <span className="font-mono font-medium">{run.id.slice(0, 8)}…</span>?
        </p>
        <p className="mt-2 text-sm text-gray-500">
          In-progress Salesforce jobs will be aborted and pending jobs will not be submitted.
        </p>
      </Modal>
    </div>
  )
}

// ─── Small stat card ──────────────────────────────────────────────────────────

function Stat({
  label,
  value,
  valueClass,
}: {
  label: string
  value: string | number
  valueClass?: string
}) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className={clsx('text-lg font-semibold text-gray-900', valueClass)}>{value}</div>
    </div>
  )
}
