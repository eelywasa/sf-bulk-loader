import { useParams, Link } from 'react-router-dom'
import { Card, Button } from '../components/ui'

export default function PlanEditor() {
  const { id } = useParams<{ id: string }>()
  const isNew = id === 'new'

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <nav className="flex items-center gap-2 text-sm text-gray-500 mb-1">
            <Link to="/plans" className="hover:text-gray-900">
              Load Plans
            </Link>
            <span>›</span>
            <span className="text-gray-900">{isNew ? 'New Plan' : `Plan ${id}`}</span>
          </nav>
          <h1 className="text-2xl font-bold text-gray-900">
            {isNew ? 'New Load Plan' : 'Edit Load Plan'}
          </h1>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => window.history.back()}>
            Cancel
          </Button>
          <Button>Save Plan</Button>
        </div>
      </div>

      <Card title="Plan Details">
        <p className="text-sm text-gray-400 py-4 text-center">
          Coming in Milestone 4 — Plan editor form.
        </p>
      </Card>

      <Card title="Load Steps">
        <p className="text-sm text-gray-400 py-4 text-center">
          Coming in Milestone 4 — Step CRUD and reorder.
        </p>
      </Card>
    </div>
  )
}
