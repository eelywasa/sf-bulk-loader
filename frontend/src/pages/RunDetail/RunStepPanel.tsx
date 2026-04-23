import { useState } from 'react'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faChevronDown, faChevronRight } from '@fortawesome/free-solid-svg-icons'
import { Badge, Button, Progress } from '../../components/ui'
import type { ProgressColor } from '../../components/ui/Progress'
import type { LoadStep, JobRecord } from '../../api/types'
import { isQueryOperation, operationLabel } from '../../api/types'
import { RunJobList } from './RunJobList'
import { deriveStepStatus } from './utils'

export interface StepPanelProps {
  step: LoadStep
  jobs: JobRecord[]
  runId: string
  defaultExpanded?: boolean
  isLive: boolean
  onRetry?: (stepId: string) => void
  retryPending?: boolean
}

export function RunStepPanel({
  step,
  jobs,
  runId,
  defaultExpanded = false,
  isLive,
  onRetry,
  retryPending,
}: StepPanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const stepStatus = deriveStepStatus(jobs)
  const isQuery = isQueryOperation(step.operation)

  const totalProcessed = jobs.reduce((n, j) => n + (j.records_processed ?? 0), 0)
  const totalFailed = jobs.reduce((n, j) => n + (j.records_failed ?? 0), 0)
  const totalRecords = jobs.reduce((n, j) => n + (j.total_records ?? 0), 0)
  const progressValue = totalRecords > 0 ? Math.round((totalProcessed / totalRecords) * 100) : 0

  const progressColor: ProgressColor =
    stepStatus.label === 'complete' ? 'green'
    : stepStatus.label === 'failed' ? 'red'
    : 'blue'

  const hasRetryableJobs =
    !isQuery && (stepStatus.label === 'failed' || (stepStatus.label === 'complete' && totalFailed > 0))

  return (
    <div className="border border-border-base rounded-lg overflow-hidden">
      {/* Header */}
      <div className="bg-surface-sunken px-5 py-4">
        {/* Top row: toggle button takes all space, retry + chevron on the right */}
        <div className="flex items-center gap-3 min-w-0">
          <button
            type="button"
            className="flex items-center gap-3 flex-wrap min-w-0 flex-1 text-left"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={`Step ${step.sequence}: ${step.object_name}`}
          >
            <span className="text-xs font-mono font-semibold text-content-muted shrink-0">
              #{step.sequence}
            </span>
            <span className="font-medium text-content-primary truncate">{step.object_name}</span>
            <Badge variant="neutral" className="shrink-0">
              {operationLabel(step.operation)}
            </Badge>
            <Badge variant={stepStatus.variant} className="shrink-0">{stepStatus.label}</Badge>
            <span className="text-xs text-content-muted shrink-0">
              {jobs.length} job{jobs.length !== 1 ? 's' : ''}
            </span>
          </button>

          {/* Retry button — before the chevron */}
          {!isLive && hasRetryableJobs && onRetry && (
            <Button
              variant="secondary"
              size="sm"
              loading={retryPending}
              disabled={retryPending}
              onClick={(e) => {
                e.stopPropagation()
                onRetry(step.id)
              }}
            >
              Retry Failed Records
            </Button>
          )}

          {/* Chevron — always rightmost */}
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="w-5 h-5 flex items-center justify-center text-content-muted hover:text-content-primary transition-colors shrink-0"
            tabIndex={-1}
            aria-hidden="true"
          >
            <FontAwesomeIcon
              icon={expanded ? faChevronDown : faChevronRight}
              className="w-3 h-3"
            />
          </button>
        </div>

        {/* Progress row — only when there are jobs */}
        {jobs.length > 0 && (
          <div className="mt-3 flex items-center gap-3">
            <Progress
              value={progressValue}
              color={progressColor}
              size="sm"
              className="flex-1"
            />
            <span className="text-xs text-content-muted shrink-0 whitespace-nowrap">
              {isQuery ? (
                <>{totalProcessed.toLocaleString()} rows returned</>
              ) : (
                <>
                  {totalProcessed.toLocaleString()} processed
                  {totalFailed > 0 && (
                    <span className="text-error-text"> · {totalFailed.toLocaleString()} failed</span>
                  )}
                </>
              )}
            </span>
          </div>
        )}

        {/* SOQL block — only for query/queryAll steps */}
        {isQuery && step.soql && (
          <div className="mt-3">
            <p className="text-xs font-medium text-content-muted uppercase tracking-wide mb-1">SOQL</p>
            <pre className="rounded-md bg-surface-code text-content-code px-3 py-2 text-xs font-mono whitespace-pre-wrap leading-relaxed overflow-x-auto">
              {step.soql}
            </pre>
          </div>
        )}
      </div>

      {/* Job list */}
      {expanded && (
        <div className="divide-y divide-border-base">
          <RunJobList jobs={jobs} runId={runId} operation={step.operation} />
        </div>
      )}
    </div>
  )
}
