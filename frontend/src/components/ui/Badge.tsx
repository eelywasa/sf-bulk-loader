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

// ─── Variant → token mapping ───────────────────────────────────────────────────
// Semantic variants map to state token classes so they work in both modes without
// any dark: overrides. Neutral/pending/aborted use surface+muted since they have
// no semantic state connotation. Blue-family statuses (running, uploading, etc.)
// use info tokens. Fill colours for dots use the corresponding <state>-text token
// so they stay saturated enough to be legible on the <state>-bg background.
const variantClasses: Record<BadgeVariant, string> = {
  // Semantic states
  success:              'bg-success-bg text-success-text',
  warning:              'bg-warning-bg text-warning-text',
  error:                'bg-error-bg text-error-text',
  info:                 'bg-info-bg text-info-text',
  neutral:              'bg-surface-sunken text-content-muted',
  // Run statuses
  pending:              'bg-surface-sunken text-content-muted',
  running:              'bg-info-bg text-info-text',
  completed:            'bg-success-bg text-success-text',
  completed_with_errors:'bg-warning-bg text-warning-text',
  failed:               'bg-error-bg text-error-text',
  aborted:              'bg-surface-sunken text-content-muted',
  // Job statuses
  uploading:            'bg-info-bg text-info-text',
  upload_complete:      'bg-info-bg text-info-text',
  in_progress:          'bg-info-bg text-info-text',
  job_complete:         'bg-success-bg text-success-text',
}

const variantDot: Record<BadgeVariant, string> = {
  success:              'bg-success-text',
  warning:              'bg-warning-text',
  error:                'bg-error-text',
  info:                 'bg-info-text',
  neutral:              'bg-content-muted',
  pending:              'bg-content-muted',
  running:              'bg-info-text',
  completed:            'bg-success-text',
  completed_with_errors:'bg-warning-text',
  failed:               'bg-error-text',
  aborted:              'bg-content-muted',
  uploading:            'bg-info-text',
  upload_complete:      'bg-info-text',
  in_progress:          'bg-info-text',
  job_complete:         'bg-success-text',
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
