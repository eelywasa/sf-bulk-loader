import { useNavigate } from 'react-router-dom'
import { EmptyState, Button } from '../components/ui'

export default function PlansPage() {
  const navigate = useNavigate()

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Load Plans</h1>
          <p className="mt-1 text-sm text-gray-500">
            Define and manage data load configurations.
          </p>
        </div>
        <Button onClick={() => navigate('/plans/new')}>New Plan</Button>
      </div>

      <EmptyState
        title="No load plans yet"
        description="Create a load plan to define which Salesforce objects to load, in what order, with which CSV files."
        action={<Button onClick={() => navigate('/plans/new')}>Create Plan</Button>}
      />
    </div>
  )
}
