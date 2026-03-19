import { Link } from 'react-router-dom'
import { Badge, Progress } from '../../components/ui'
import type { BadgeVariant } from '../../components/ui/Badge'
import type { JobRecord } from '../../api/types'

interface RunJobListProps {
  jobs: JobRecord[]
  runId: string
}

export function RunJobList({ jobs, runId }: RunJobListProps) {
  if (jobs.length === 0) {
    return <p className="px-4 py-3 text-sm text-gray-400 italic">No jobs started yet.</p>
  }

  return (
    <>
      {jobs.map((job) => (
        <div
          key={job.id}
          className="flex items-start justify-between px-4 py-3 text-sm hover:bg-gray-50"
        >
          <div className="flex flex-col gap-1 min-w-0 flex-1">
            <div className="flex items-center gap-3 flex-wrap min-w-0">
              <span className="text-xs text-gray-500 font-mono shrink-0">
                Part {job.partition_index}
              </span>
              <Badge variant={job.status as BadgeVariant}>{job.status}</Badge>
              {job.records_processed != null && (
                <span className="text-gray-600">
                  {job.records_processed} processed
                  {(job.records_failed ?? 0) > 0 && (
                    <span className="text-red-600 ml-1">· {job.records_failed} failed</span>
                  )}
                </span>
              )}
              {job.error_message && (
                <span
                  className="text-red-500 text-xs truncate max-w-[20rem]"
                  title={job.error_message}
                >
                  {job.error_message}
                </span>
              )}
            </div>
            {job.status === 'in_progress' && (job.total_records ?? 0) > 0 && (
              <Progress
                value={Math.round(((job.records_processed ?? 0) / job.total_records!) * 100)}
                label={`${(job.records_processed ?? 0).toLocaleString()} / ${job.total_records!.toLocaleString()} records`}
                showValue
                color="blue"
                size="sm"
                className="max-w-xs"
              />
            )}
          </div>
          <Link
            to={`/runs/${runId}/jobs/${job.id}`}
            className="ml-2 text-blue-600 hover:underline text-xs shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            Details
          </Link>
        </div>
      ))}
    </>
  )
}
