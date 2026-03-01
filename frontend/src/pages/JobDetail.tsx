import { useParams, Link } from 'react-router-dom'
import { Card, Tabs } from '../components/ui'

export default function JobDetail() {
  const { runId, jobId } = useParams<{ runId: string; jobId: string }>()

  const tabs = [
    {
      id: 'overview',
      label: 'Overview',
      content: (
        <p className="text-sm text-gray-400 py-4 text-center">
          Coming in Milestone 6 — Job metadata and counts.
        </p>
      ),
    },
    {
      id: 'payload',
      label: 'Raw SF Payload',
      content: (
        <p className="text-sm text-gray-400 py-4 text-center">
          Coming in Milestone 6 — Salesforce API response JSON.
        </p>
      ),
    },
    {
      id: 'downloads',
      label: 'Downloads',
      content: (
        <p className="text-sm text-gray-400 py-4 text-center">
          Coming in Milestone 6 — CSV download links.
        </p>
      ),
    },
  ]

  return (
    <div className="p-6 space-y-6">
      <div>
        <nav className="flex items-center gap-2 text-sm text-gray-500 mb-1">
          <Link to="/runs" className="hover:text-gray-900">
            Runs
          </Link>
          <span>›</span>
          <Link to={`/runs/${runId}`} className="hover:text-gray-900">
            Run {runId}
          </Link>
          <span>›</span>
          <span className="text-gray-900">Job {jobId}</span>
        </nav>
        <h1 className="text-2xl font-bold text-gray-900">Job Detail</h1>
      </div>

      <Card padding={false}>
        <Tabs tabs={tabs} className="px-6 pt-4" />
      </Card>
    </div>
  )
}
