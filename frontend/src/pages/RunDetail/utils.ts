import type { JobRecord } from '../../api/types'
import type { BadgeVariant } from '../../components/ui/Badge'

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function formatElapsed(
  startedAt: string | null | undefined,
  completedAt: string | null | undefined,
): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = completedAt ? new Date(completedAt).getTime() : Date.now()
  const ms = end - start
  const totalSeconds = Math.floor(ms / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

export function deriveStepStatus(jobs: JobRecord[]): { label: string; variant: BadgeVariant } {
  if (jobs.length === 0) return { label: 'pending', variant: 'pending' }

  const statuses = jobs.map((j) => j.status)

  if (statuses.some((s) => s === 'failed')) return { label: 'failed', variant: 'failed' }
  if (statuses.some((s) => s === 'aborted')) return { label: 'aborted', variant: 'aborted' }
  if (statuses.every((s) => s === 'job_complete')) return { label: 'complete', variant: 'completed' }
  if (statuses.some((s) => s === 'in_progress' || s === 'upload_complete' || s === 'uploading'))
    return { label: 'running', variant: 'running' }
  return { label: 'pending', variant: 'pending' }
}
