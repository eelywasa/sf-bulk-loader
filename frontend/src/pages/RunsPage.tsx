import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { runsApi, plansApi } from '../api/endpoints'
import type { RunListParams } from '../api/endpoints'
import type { LoadRun } from '../api/types'
import { Card, Badge, Button, EmptyState } from '../components/ui'
import type { BadgeVariant } from '../components/ui/Badge'

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

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function RunsPage() {
  const navigate = useNavigate()

  // ── Filter state ────────────────────────────────────────────────────────────
  const [planId, setPlanId] = useState('')
  const [runStatus, setRunStatus] = useState('')
  const [startedAfter, setStartedAfter] = useState('')
  const [startedBefore, setStartedBefore] = useState('')

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

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="p-6 space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Runs</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          View and monitor load run history.
        </p>
      </div>

      {/* Filters */}
      <Card title="Filters">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {/* Plan filter */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Plan
            </label>
            <select
              value={planId}
              onChange={(e) => setPlanId(e.target.value)}
              className="block w-full rounded-md border border-gray-300 bg-white py-1.5 px-3 text-sm text-gray-900 shadow-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
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
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Status
            </label>
            <select
              value={runStatus}
              onChange={(e) => setRunStatus(e.target.value)}
              className="block w-full rounded-md border border-gray-300 bg-white py-1.5 px-3 text-sm text-gray-900 shadow-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
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
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Started After
            </label>
            <input
              type="datetime-local"
              value={startedAfter}
              onChange={(e) => setStartedAfter(e.target.value)}
              className="block w-full rounded-md border border-gray-300 bg-white py-1.5 px-3 text-sm text-gray-900 shadow-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
              aria-label="Started after"
            />
          </div>

          {/* Started before */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Started Before
            </label>
            <input
              type="datetime-local"
              value={startedBefore}
              onChange={(e) => setStartedBefore(e.target.value)}
              className="block w-full rounded-md border border-gray-300 bg-white py-1.5 px-3 text-sm text-gray-900 shadow-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
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
          <p
            className="text-sm text-gray-400 py-6 text-center"
            aria-label="Loading"
          >
            Loading runs…
          </p>
        )}

        {runsQuery.isError && (
          <p className="text-sm text-red-500 py-6 text-center">
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
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead>
                <tr>
                  {['Run ID', 'Status', 'Plan', 'Records', 'Success', 'Errors', 'Started', ''].map(
                    (h) => (
                      <th
                        key={h}
                        className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {runs.map((run) => {
                  const plan = plans.find((p) => p.id === run.load_plan_id)
                  return (
                    <tr
                      key={run.id}
                      className="hover:bg-gray-50 cursor-pointer"
                      onClick={() => navigate(`/runs/${run.id}`)}
                    >
                      <td className="px-4 py-2 font-mono text-xs text-blue-600">
                        {run.id.slice(0, 8)}…
                      </td>
                      <td className="px-4 py-2">
                        <Badge variant={statusVariant(run.status)} dot>
                          {run.status}
                        </Badge>
                      </td>
                      <td className="px-4 py-2 text-gray-700 max-w-[10rem] truncate">
                        {plan?.name ?? <span className="text-gray-400 font-mono text-xs">{run.load_plan_id.slice(0, 8)}…</span>}
                      </td>
                      <td className="px-4 py-2 text-gray-700">
                        {run.total_records ?? '—'}
                      </td>
                      <td className="px-4 py-2 text-green-700">
                        {run.total_success ?? '—'}
                      </td>
                      <td className="px-4 py-2 text-red-700">
                        {run.total_errors ?? '—'}
                      </td>
                      <td className="px-4 py-2 text-gray-500 whitespace-nowrap">
                        {formatDate(run.started_at)}
                      </td>
                      <td className="px-4 py-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/runs/${run.id}`)
                          }}
                        >
                          View
                        </Button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
