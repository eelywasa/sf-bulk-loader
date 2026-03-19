import { useState } from 'react'
import { Badge, Button } from '../../components/ui'
import type { LoadStep, JobRecord } from '../../api/types'
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

  const totalProcessed = jobs.reduce((n, j) => n + (j.records_processed ?? 0), 0)
  const totalFailed = jobs.reduce((n, j) => n + (j.records_failed ?? 0), 0)

  const hasRetryableJobs =
    stepStatus.label === 'failed' || (stepStatus.label === 'complete' && totalFailed > 0)

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 bg-gray-50">
        <button
          type="button"
          className="flex items-center gap-3 flex-wrap min-w-0 flex-1 text-left hover:bg-gray-100 rounded"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`Step ${step.sequence}: ${step.object_name}`}
        >
          <span className="text-xs font-mono font-semibold text-gray-500 shrink-0">
            #{step.sequence}
          </span>
          <span className="font-medium text-gray-900 truncate">{step.object_name}</span>
          <Badge variant="neutral" className="capitalize">
            {step.operation}
          </Badge>
          <Badge variant={stepStatus.variant}>{stepStatus.label}</Badge>
          <span className="text-xs text-gray-500">
            {jobs.length} job{jobs.length !== 1 ? 's' : ''}
          </span>
          {jobs.length > 0 && (
            <span className="text-xs text-gray-500">
              {totalProcessed} processed · {totalFailed} failed
            </span>
          )}
          <span className="ml-2 text-gray-400 shrink-0 text-xs">{expanded ? '▲' : '▼'}</span>
        </button>
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
      </div>

      {expanded && (
        <div className="divide-y divide-gray-100">
          <RunJobList jobs={jobs} runId={runId} />
        </div>
      )}
    </div>
  )
}
