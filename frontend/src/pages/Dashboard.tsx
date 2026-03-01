import { Card } from '../components/ui'

export default function Dashboard() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="mt-1 text-sm text-gray-500">
          Overview of active runs, recent completions, and connection health.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {(['Active Runs', 'Completed Today', 'Error Rate'] as const).map((label) => (
          <Card key={label}>
            <div className="text-sm font-medium text-gray-500">{label}</div>
            <div className="mt-2 text-3xl font-bold text-gray-300">—</div>
          </Card>
        ))}
      </div>

      <Card title="Recent Runs">
        <p className="text-sm text-gray-400 py-4 text-center">
          Coming in Milestone 5 — Runs list.
        </p>
      </Card>
    </div>
  )
}
