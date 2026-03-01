import { EmptyState } from '../components/ui'

export default function RunsPage() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Runs</h1>
        <p className="mt-1 text-sm text-gray-500">
          View and monitor load run history.
        </p>
      </div>

      <EmptyState
        title="No runs yet"
        description="Start a run from a load plan to see it here."
      />
    </div>
  )
}
