import { EmptyState, Button } from '../components/ui'

export default function Connections() {
  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Connections</h1>
          <p className="mt-1 text-sm text-gray-500">
            Manage Salesforce org connections.
          </p>
        </div>
        <Button>New Connection</Button>
      </div>

      <EmptyState
        title="No connections yet"
        description="Add a Salesforce connection to get started. You'll need a Connected App with JWT Bearer auth."
        action={<Button>Add Connection</Button>}
      />
    </div>
  )
}
