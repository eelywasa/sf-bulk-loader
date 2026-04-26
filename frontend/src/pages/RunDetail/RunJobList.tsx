import { Link } from 'react-router-dom'
import { Badge, Progress } from '../../components/ui'
import type { BadgeVariant } from '../../components/ui/Badge'
import type { JobRecord, Operation } from '../../api/types'
import { isQueryOperation } from '../../api/types'

interface RunJobListProps {
  jobs: JobRecord[]
  runId: string
  operation?: Operation
}

export function RunJobList({ jobs, runId, operation }: RunJobListProps) {
  const isQuery = operation ? isQueryOperation(operation) : false
  if (jobs.length === 0) {
    return <p className="px-5 py-4 text-sm text-content-muted italic">No jobs started yet.</p>
  }

  return (
    <>
      {jobs.map((job) => (
        <div
          key={job.id}
          className="px-5 py-4 text-sm hover:bg-surface-hover"
        >
          {/* Top row: identity + Details link */}
          <div className="flex items-center justify-between gap-3 min-w-0">
            <div className="flex items-center gap-3 min-w-0 flex-wrap">
              <span className="text-xs text-content-muted font-mono shrink-0">
                Part {job.partition_index}
              </span>
              <Badge variant={job.status as BadgeVariant}>{job.status}</Badge>
            </div>
            <Link
              to={`/runs/${runId}/jobs/${job.id}`}
              className="text-content-link hover:underline text-xs shrink-0"
              onClick={(e) => e.stopPropagation()}
            >
              Details
            </Link>
          </div>

          {/* Bottom row: stats + error + progress */}
          {(job.records_processed != null || job.error_message || (job.status === 'in_progress' && (job.total_records ?? 0) > 0)) && (
            <div className="mt-2 flex flex-col gap-1.5 pl-0">
              <div className="flex items-center gap-2 flex-wrap">
                {job.records_processed != null && (
                  <span className="text-xs text-content-secondary">
                    {isQuery ? (
                      <>{job.records_processed.toLocaleString()} rows returned</>
                    ) : (
                      <>
                        {job.records_processed.toLocaleString()} processed
                        {(job.records_failed ?? 0) > 0 && (
                          <span className="text-error-text ml-1">
                            · {job.records_failed!.toLocaleString()} failed
                          </span>
                        )}
                      </>
                    )}
                  </span>
                )}
                {job.error_message && (
                  <span
                    className="text-error-text text-xs truncate max-w-[24rem]"
                    title={job.error_message}
                  >
                    {job.error_message}
                  </span>
                )}
              </div>
              {!isQuery && job.status === 'in_progress' && (job.total_records ?? 0) > 0 && (
                <Progress
                  value={Math.round(((job.records_processed ?? 0) / job.total_records!) * 100)}
                  label={`${(job.records_processed ?? 0).toLocaleString()} / ${job.total_records!.toLocaleString()} records`}
                  showValue
                  color="blue"
                  size="sm"
                  className="w-full"
                />
              )}
            </div>
          )}
        </div>
      ))}
    </>
  )
}
