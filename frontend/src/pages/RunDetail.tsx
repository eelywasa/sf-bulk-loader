import { useParams, Link } from 'react-router-dom'
import { Card, Badge } from '../components/ui'

export default function RunDetail() {
  const { id } = useParams<{ id: string }>()

  return (
    <div className="p-6 space-y-6">
      <div>
        <nav className="flex items-center gap-2 text-sm text-gray-500 mb-1">
          <Link to="/runs" className="hover:text-gray-900">
            Runs
          </Link>
          <span>›</span>
          <span className="text-gray-900">Run {id}</span>
        </nav>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">Run Detail</h1>
          <Badge variant="pending">pending</Badge>
        </div>
      </div>

      <Card title="Summary">
        <p className="text-sm text-gray-400 py-4 text-center">
          Coming in Milestone 5 — Run mission control with step accordion and polling.
        </p>
      </Card>
    </div>
  )
}
