import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { runsApi, plansApi } from '../api/endpoints'
import type { RunListParams } from '../api/endpoints'
import type { LoadRun } from '../api/types'
import { Card, Badge, Button, EmptyState, DataTable, Spinner, type Column } from '../components/ui'
import type { BadgeVariant } from '../components/ui/Badge'
import { LABEL_CLASS, INPUT_CLASS, SELECT_CLASS } from '../components/ui/formStyles'
import { formatDatetime } from '../utils/formatters'

// ─── Helpers ──────────────────────────────────────────────────────────────────

const ALL_RUN_STATUSES: LoadRun['status'][] = [
  'pending',
  'running',
  'completed',
  'completed_with_errors',
  'failed',
  'aborted',
]

function statusVariant(status: LoadRun['status']): BadgeVariant {
  return status
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 10

export default function RunsPage() {
  const navigate = useNavigate()

  // ── Filter state ────────────────────────────────────────────────────────────
  const [planId, setPlanId] = useState('')
  const [runStatus, setRunStatus] = useState('')
  const [startedAfter, setStartedAfter] = useState('')
  const [startedBefore, setStartedBefore] = useState('')
  const [page, setPage] = useState(1)

  function buildFilters(): RunListParams {
    return {
      ...(planId ? { plan_id: planId } : {}),
      ...(runStatus ? { run_status: runStatus } : {}),
      ...(startedAfter ? { started_after: new Date(startedAfter).toISOString() } : {}),
      ...(startedBefore ? { started_before: new Date(startedBefore).toISOString() } : {}),
    }
  }

  function clearFilters() {
    setPlanId('')
    setRunStatus('')
    setStartedAfter('')
    setStartedBefore('')
    setPage(1)
  }

  const filters = buildFilters()

  // ── Data fetching ───────────────────────────────────────────────────────────
  const plansQuery = useQuery({
    queryKey: ['plans'],
    queryFn: plansApi.list,
  })

  const runsQuery = useQuery({
    queryKey: ['runs', filters],
    queryFn: () => runsApi.list(filters),
    refetchInterval: 10_000,
  })

  const plans = plansQuery.data ?? []
  const runs = runsQuery.data ?? []
  const totalPages = Math.max(1, Math.ceil(runs.length / PAGE_SIZE))
  const paginatedRuns = runs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  // Sync page state when a refetch causes the dataset to shrink
  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  // ── Table columns ───────────────────────────────────────────────────────────
  const planMap = new Map(plans.map((p) => [p.id, p]))

  const columns: Column<LoadRun>[] = [
    {
      key: 'id',
      header: 'Run ID',
      render: (run) => (
        <span className="font-mono text-xs text-content-secondary">
          {run.id.slice(0, 8)}…
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (run) => (
        <Badge variant={statusVariant(run.status)} dot>
          {run.status}
        </Badge>
      ),
    },
    {
      key: 'plan',
      header: 'Plan',
      render: (run) => {
        const plan = planMap.get(run.load_plan_id)
        return (
          <span className="text-content-secondary max-w-[10rem] truncate block">
            {plan?.name ?? (
              <span className="text-content-muted font-mono text-xs">
                {run.load_plan_id.slice(0, 8)}…
              </span>
            )}
          </span>
        )
      },
    },
    {
      key: 'total_records',
      header: 'Records',
      render: (run) => <span className="text-content-secondary">{run.total_records ?? '—'}</span>,
    },
    {
      key: 'total_success',
      header: 'Success',
      render: (run) => <span className="text-success-text">{run.total_success ?? '—'}</span>,
    },
    {
      key: 'total_errors',
      header: 'Errors',
      render: (run) => <span className="text-error-text">{run.total_errors ?? '—'}</span>,
    },
    {
      key: 'started_at',
      header: 'Started',
      render: (run) => (
        <span className="text-content-muted whitespace-nowrap">
          {formatDatetime(run.started_at)}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      className: 'text-right',
      render: (run) => (
        <Button
          variant="secondary"
          size="sm"
          onClick={(e) => {
            e.stopPropagation()
            navigate(`/runs/${run.id}`)
          }}
        >
          View
        </Button>
      ),
    },
  ]

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="p-6 space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-content-primary">Runs</h1>
        <p className="mt-1 text-sm text-content-muted">
          View and monitor load run history.
        </p>
      </div>

      {/* Filters */}
      <Card title="Filters">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {/* Plan filter */}
          <div>
            <label className={LABEL_CLASS}>
              Plan
            </label>
            <select
              value={planId}
              onChange={(e) => { setPlanId(e.target.value); setPage(1) }}
              className={SELECT_CLASS}
              aria-label="Filter by plan"
            >
              <option value="">All Plans</option>
              {plans.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          {/* Status filter */}
          <div>
            <label className={LABEL_CLASS}>
              Status
            </label>
            <select
              value={runStatus}
              onChange={(e) => { setRunStatus(e.target.value); setPage(1) }}
              className={SELECT_CLASS}
              aria-label="Filter by status"
            >
              <option value="">All Statuses</option>
              {ALL_RUN_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          {/* Started after */}
          <div>
            <label className={LABEL_CLASS}>
              Started After
            </label>
            <input
              type="datetime-local"
              value={startedAfter}
              onChange={(e) => { setStartedAfter(e.target.value); setPage(1) }}
              className={INPUT_CLASS}
              aria-label="Started after"
            />
          </div>

          {/* Started before */}
          <div>
            <label className={LABEL_CLASS}>
              Started Before
            </label>
            <input
              type="datetime-local"
              value={startedBefore}
              onChange={(e) => { setStartedBefore(e.target.value); setPage(1) }}
              className={INPUT_CLASS}
              aria-label="Started before"
            />
          </div>
        </div>

        {/* Clear filters */}
        {(planId || runStatus || startedAfter || startedBefore) && (
          <div className="mt-3 flex justify-end">
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              Clear filters
            </Button>
          </div>
        )}
      </Card>

      {/* Runs table */}
      <Card title="Load Runs">
        {runsQuery.isPending && (
          <div className="flex justify-center py-12">
            <Spinner size="md" />
          </div>
        )}

        {runsQuery.isError && (
          <p className="text-sm text-error-text py-6 text-center">
            Failed to load runs.{' '}
            {runsQuery.error instanceof Error ? runsQuery.error.message : ''}
          </p>
        )}

        {runsQuery.isSuccess && runs.length === 0 && (
          <EmptyState
            title="No runs found"
            description={
              planId || runStatus || startedAfter || startedBefore
                ? 'No runs match the current filters. Try clearing them.'
                : 'Start a run from a load plan to see it here.'
            }
          />
        )}

        {runsQuery.isSuccess && runs.length > 0 && (
          <>
            <DataTable
              columns={columns}
              data={paginatedRuns}
              keyExtractor={(run) => run.id}
              onRowClick={(run) => navigate(`/runs/${run.id}`)}
            />

            {totalPages > 1 && (
              <div className="flex items-center justify-between px-6 py-3 border-t border-border-base">
                <span className="text-sm text-content-muted">
                  {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, runs.length)} of {runs.length} runs
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                    aria-label="Previous page"
                  >
                    ‹ Prev
                  </Button>
                  <span className="px-2 text-sm text-content-secondary">
                    {page} / {totalPages}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page === totalPages}
                    aria-label="Next page"
                  >
                    Next ›
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
