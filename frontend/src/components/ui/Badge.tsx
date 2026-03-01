import clsx from 'clsx'

export type BadgeVariant =
  | 'success'
  | 'warning'
  | 'error'
  | 'info'
  | 'neutral'
  // RunStatus
  | 'pending'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'aborted'
  // JobStatus
  | 'uploading'
  | 'upload_complete'
  | 'in_progress'
  | 'job_complete'

const variantClasses: Record<BadgeVariant, string> = {
  success: 'bg-green-100 text-green-800',
  warning: 'bg-orange-100 text-orange-800',
  error: 'bg-red-100 text-red-800',
  info: 'bg-blue-100 text-blue-800',
  neutral: 'bg-gray-100 text-gray-700',
  // Run statuses
  pending: 'bg-gray-100 text-gray-600',
  running: 'bg-blue-100 text-blue-800',
  completed: 'bg-green-100 text-green-800',
  completed_with_errors: 'bg-orange-100 text-orange-800',
  failed: 'bg-red-100 text-red-800',
  aborted: 'bg-gray-100 text-gray-500',
  // Job statuses
  uploading: 'bg-blue-100 text-blue-700',
  upload_complete: 'bg-blue-100 text-blue-700',
  in_progress: 'bg-blue-100 text-blue-800',
  job_complete: 'bg-green-100 text-green-800',
}

const variantDot: Record<BadgeVariant, string> = {
  success: 'bg-green-500',
  warning: 'bg-orange-500',
  error: 'bg-red-500',
  info: 'bg-blue-500',
  neutral: 'bg-gray-400',
  pending: 'bg-gray-400',
  running: 'bg-blue-500',
  completed: 'bg-green-500',
  completed_with_errors: 'bg-orange-500',
  failed: 'bg-red-500',
  aborted: 'bg-gray-400',
  uploading: 'bg-blue-400',
  upload_complete: 'bg-blue-400',
  in_progress: 'bg-blue-500',
  job_complete: 'bg-green-500',
}

export interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  dot?: boolean
  className?: string
}

export function Badge({ variant = 'neutral', children, dot = false, className }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium',
        variantClasses[variant],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={clsx('h-1.5 w-1.5 rounded-full', variantDot[variant])}
        />
      )}
      {children}
    </span>
  )
}
