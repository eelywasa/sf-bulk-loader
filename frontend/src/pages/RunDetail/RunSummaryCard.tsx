import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { Badge, Button, Progress } from '../../components/ui'
import type { LoadRun, JobRecord, LoadPlanDetail } from '../../api/types'
import { formatDate, formatElapsed } from './utils'

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
      <div className="text-xs text-content-muted">{label}</div>
      <div className={clsx('text-lg font-semibold text-content-primary', valueClass)}>{value}</div>
    </div>
  )
}

interface RunSummaryCardProps {
  run: LoadRun
  jobs: JobRecord[]
  planDetail: LoadPlanDetail | undefined
  isLive: boolean
  isWsConnected: boolean
  onAbort: () => void
}

export function RunSummaryCard({
  run,
  jobs,
  planDetail,
  isLive,
  isWsConnected,
  onAbort,
}: RunSummaryCardProps) {
  const liveSuccess = useMemo(() => jobs.reduce((n, j) => n + (j.records_successful ?? 0), 0), [jobs])
  const liveErrors = useMemo(() => jobs.reduce((n, j) => n + (j.records_failed ?? 0), 0), [jobs])
  const liveTotal = useMemo(() => jobs.reduce((n, j) => n + (j.total_records ?? 0), 0), [jobs])

  const displaySuccess = run.total_success != null && !isLive ? run.total_success : liveSuccess
  const displayErrors = run.total_errors != null && !isLive ? run.total_errors : liveErrors
  const displayTotal =
    run.total_records != null
      ? run.total_records
      : liveTotal > 0
        ? liveTotal
        : null

  const successPct = useMemo(() => {
    if (!displayTotal || displayTotal === 0) return 0
    return Math.round((displaySuccess / displayTotal) * 100)
  }, [displaySuccess, displayTotal])

  const progressColor = useMemo(() => {
    if (run.status === 'failed') return 'red' as const
    if (run.status === 'completed_with_errors') return 'orange' as const
    if (run.status === 'aborted') return 'orange' as const
    if (displayErrors > 0) return 'orange' as const
    return 'green' as const
  }, [run.status, displayErrors])

  return (
    <div className="sticky top-0 z-10 bg-surface-raised border border-border-base rounded-lg px-6 py-4 shadow-sm space-y-3">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-xl font-bold text-content-primary">
            Run <span className="font-mono text-base">{run.id}</span>
          </h1>
          <Badge variant={run.status} dot>
            {run.status}
          </Badge>
          {isLive && (
            <span className="text-xs text-blue-500 animate-pulse">
              {isWsConnected ? 'Live' : 'Polling…'}
            </span>
          )}
        </div>

        {isLive && (
          <Button variant="danger" size="sm" onClick={onAbort}>
            Abort Run
          </Button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Total Records" value={displayTotal ?? '—'} />
        <Stat label="Successes" value={displaySuccess} valueClass="text-success-text" />
        <Stat
          label="Errors"
          value={displayErrors}
          valueClass={displayErrors > 0 ? 'text-error-text' : undefined}
        />
        <Stat label="Elapsed" value={formatElapsed(run.started_at, run.completed_at)} />
      </div>

      {(displayTotal ?? 0) > 0 && (
        <Progress
          value={successPct}
          label={`${displaySuccess} / ${displayTotal} records succeeded`}
          showValue
          color={progressColor}
        />
      )}

      <div className="flex gap-6 text-xs text-content-muted flex-wrap">
        <span>Started: {formatDate(run.started_at)}</span>
        {run.completed_at && <span>Completed: {formatDate(run.completed_at)}</span>}
        {run.initiated_by && <span>By: {run.initiated_by}</span>}
        {planDetail && (
          <Link to={`/plans/${run.load_plan_id}`} className="text-content-link hover:underline">
            Plan: {planDetail.name}
          </Link>
        )}
      </div>

      {(() => {
        // Render a terminal-failure banner whenever error_summary carries a
        // reason (auth_error, storage_error, or any other known key).
        // For failed runs with an error_summary but no recognized reason field,
        // fall back to a generic message so the UI never silently hides the
        // failure context. Preflight warnings have their own banner below.
        const reason = run.error_summary?.auth_error ?? run.error_summary?.storage_error
        if (reason) {
          return (
            <p className="text-xs text-error-text bg-error-bg rounded px-3 py-1.5">{reason}</p>
          )
        }
        if (run.status === 'failed' && run.error_summary) {
          return (
            <p className="text-xs text-error-text bg-error-bg rounded px-3 py-1.5">
              Run failed. See logs for details.
            </p>
          )
        }
        return null
      })()}

      {run.error_summary?.preflight_warnings && run.error_summary.preflight_warnings.length > 0 && (
        <div
          role="status"
          aria-label="Preflight warnings"
          className="text-xs text-warning-text bg-warning-bg rounded px-3 py-1.5 space-y-1"
        >
          <p className="font-medium">
            Total records is approximate — preflight could not count {run.error_summary.preflight_warnings.length}{' '}
            {run.error_summary.preflight_warnings.length === 1 ? 'step' : 'steps'}.
          </p>
          <ul className="list-disc list-inside space-y-0.5">
            {run.error_summary.preflight_warnings.map((w) => (
              <li key={w.step_id}>
                <span className="font-mono">{w.step_id}</span>: {w.error}{' '}
                <span className="text-content-muted">({w.outcome_code})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
