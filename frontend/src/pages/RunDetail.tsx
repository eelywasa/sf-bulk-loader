import { useState, useMemo } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import type { JobRecord } from '../api/types'
import { useLiveRun } from '../hooks/useLiveRun'
import { useRunActions } from '../hooks/useRunActions'
import { Button, Card, Modal } from '../components/ui'
import { RunSummaryCard } from './RunDetail/RunSummaryCard'
import { RunStepPanel } from './RunDetail/RunStepPanel'
import { RunLogDownload } from './RunDetail/RunLogDownloadModal'

export default function RunDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [abortModalOpen, setAbortModalOpen] = useState(false)

  const { run, jobs, planDetail, isLoading, isError, error, isLive, isWsConnected } = useLiveRun(
    id ?? '',
  )

  const { retryStep, isRetryPending, retryVariables, abort, isAbortPending } = useRunActions({
    runId: id ?? '',
    onAbortSettled: () => setAbortModalOpen(false),
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

  // ── Loading / error states ──────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="p-6">
        <p className="text-sm text-content-disabled" aria-label="Loading">
          Loading run…
        </p>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="p-6 space-y-4">
        <nav className="flex items-center gap-2 text-sm text-content-muted">
          <Link to="/runs" className="hover:text-content-primary">
            Runs
          </Link>
          <span>›</span>
          <span className="text-content-primary">Error</span>
        </nav>
        <p className="text-sm text-error-text">
          Failed to load run. {error instanceof Error ? error.message : ''}
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
        <p className="text-sm text-content-muted">Run not found.</p>
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
      <nav className="flex items-center gap-2 text-sm text-content-muted">
        <Link to="/runs" className="hover:text-content-primary">
          Runs
        </Link>
        <span>›</span>
        <span className="text-content-primary font-mono">{run.id}</span>
      </nav>

      <RunSummaryCard
        run={run}
        jobs={jobs}
        planDetail={planDetail}
        isLive={isLive}
        isWsConnected={isWsConnected}
        onAbort={() => setAbortModalOpen(true)}
      />

      {/* Step accordion */}
      <Card title="Steps">
        {sortedSteps.length === 0 && !planDetail && (
          <p className="text-sm text-content-disabled py-4 text-center">
            {isLive ? 'Loading plan steps…' : 'No step information available.'}
          </p>
        )}
        {sortedSteps.length > 0 && (
          <div className="space-y-3">
            {sortedSteps.map((step) => (
              <RunStepPanel
                key={step.id}
                step={step}
                jobs={jobsByStep[step.id] ?? []}
                runId={run.id}
                isLive={isLive}
                onRetry={retryStep}
                retryPending={isRetryPending && retryVariables?.stepId === step.id}
              />
            ))}
          </div>
        )}
      </Card>

      <RunLogDownload runId={run.id} jobs={jobs} />

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
              disabled={isAbortPending}
            >
              Cancel
            </Button>
            <Button variant="danger" loading={isAbortPending} onClick={abort}>
              Abort
            </Button>
          </>
        }
      >
        <p className="text-sm text-content-secondary">
          Are you sure you want to abort run{' '}
          <span className="font-mono font-medium">{run.id.slice(0, 8)}…</span>?
        </p>
        <p className="mt-2 text-sm text-content-muted">
          In-progress Salesforce jobs will be aborted and pending jobs will not be submitted.
        </p>
      </Modal>
    </div>
  )
}
