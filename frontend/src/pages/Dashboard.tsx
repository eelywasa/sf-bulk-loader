import { useQuery } from '@tanstack/react-query'
import { Card, Badge, EmptyState } from '../components/ui'
import { healthApi, runsApi } from '../api/endpoints'
import type { LoadRun } from '../api/types'
import type { BadgeVariant } from '../components/ui/Badge'

function statusVariant(status: LoadRun['status']): BadgeVariant {
  // RunStatus values map directly to BadgeVariant
  return status
}

export default function Dashboard() {
  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: healthApi.get,
    refetchInterval: 30_000,
  })

  const runsQuery = useQuery({
    queryKey: ['runs'],
    queryFn: () => runsApi.list(),
    refetchInterval: 10_000,
  })

  const runs = runsQuery.data ?? []
  const activeRuns = runs.filter((r) => r.status === 'running' || r.status === 'pending').length
  const completedToday = runs.filter((r) => {
    if (r.status !== 'completed' && r.status !== 'completed_with_errors') return false
    if (!r.completed_at) return false
    const today = new Date().toDateString()
    return new Date(r.completed_at).toDateString() === today
  }).length
  const errorRuns = runs.filter((r) => r.status === 'failed' || r.status === 'completed_with_errors').length
  const errorRate = runs.length > 0 ? Math.round((errorRuns / runs.length) * 100) : 0

  const recentRuns = [...runs]
    .sort((a, b) => {
      const aTime = a.started_at ? new Date(a.started_at).getTime() : 0
      const bTime = b.started_at ? new Date(b.started_at).getTime() : 0
      return bTime - aTime
    })
    .slice(0, 10)

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="mt-1 text-sm text-gray-500">
            Overview of active runs, recent completions, and connection health.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-gray-500">API</span>
          {healthQuery.isPending ? (
            <Badge variant="neutral">Checking…</Badge>
          ) : healthQuery.isError ? (
            <Badge variant="error">Offline</Badge>
          ) : (
            <Badge variant="success">Online</Badge>
          )}
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <div className="text-sm font-medium text-gray-500">Active Runs</div>
          <div className="mt-2 text-3xl font-bold text-gray-900">
            {runsQuery.isPending ? (
              <span className="text-gray-300">—</span>
            ) : (
              activeRuns
            )}
          </div>
        </Card>
        <Card>
          <div className="text-sm font-medium text-gray-500">Completed Today</div>
          <div className="mt-2 text-3xl font-bold text-gray-900">
            {runsQuery.isPending ? (
              <span className="text-gray-300">—</span>
            ) : (
              completedToday
            )}
          </div>
        </Card>
        <Card>
          <div className="text-sm font-medium text-gray-500">Error Rate</div>
          <div className="mt-2 text-3xl font-bold text-gray-900">
            {runsQuery.isPending ? (
              <span className="text-gray-300">—</span>
            ) : (
              `${errorRate}%`
            )}
          </div>
        </Card>
      </div>

      {/* Recent runs table */}
      <Card title="Recent Runs">
        {runsQuery.isPending && (
          <p className="text-sm text-gray-400 py-4 text-center">Loading runs…</p>
        )}
        {runsQuery.isError && (
          <p className="text-sm text-red-500 py-4 text-center">
            Failed to load runs. Is the backend running?
          </p>
        )}
        {runsQuery.isSuccess && recentRuns.length === 0 && (
          <EmptyState
            title="No runs yet"
            description="Start a run from a Load Plan to see it here."
          />
        )}
        {runsQuery.isSuccess && recentRuns.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead>
                <tr>
                  {['Run ID', 'Status', 'Records', 'Successes', 'Errors', 'Started'].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {recentRuns.map((run) => (
                  <tr key={run.id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-xs text-gray-600 max-w-[9rem] truncate">
                      <a
                        href={`/runs/${run.id}`}
                        className="text-blue-600 hover:underline"
                      >
                        {run.id.slice(0, 8)}…
                      </a>
                    </td>
                    <td className="px-4 py-2">
                      <Badge variant={statusVariant(run.status)}>{run.status}</Badge>
                    </td>
                    <td className="px-4 py-2 text-gray-700">{run.total_records ?? '—'}</td>
                    <td className="px-4 py-2 text-green-700">{run.total_success ?? '—'}</td>
                    <td className="px-4 py-2 text-red-700">{run.total_errors ?? '—'}</td>
                    <td className="px-4 py-2 text-gray-500">
                      {run.started_at
                        ? new Date(run.started_at).toLocaleString()
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
