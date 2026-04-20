import type { JobRecord } from '../../api/types'
import type { BadgeVariant } from '../../components/ui/Badge'

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function parseUtc(iso: string): number {
  // SQLite strips timezone info, so bare ISO strings must be treated as UTC
  const normalized = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : iso + 'Z'
  return new Date(normalized).getTime()
}

export function formatElapsed(
  startedAt: string | null | undefined,
  completedAt: string | null | undefined,
): string {
  if (!startedAt) return '—'
  const start = parseUtc(startedAt)
  const end = completedAt ? parseUtc(completedAt) : Date.now()
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
