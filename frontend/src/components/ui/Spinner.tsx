import clsx from 'clsx'

export type SpinnerSize = 'xs' | 'sm' | 'md' | 'lg'

export interface SpinnerProps {
  size?: SpinnerSize
  'aria-label'?: string
  className?: string
}

const SIZE_CLASSES: Record<SpinnerSize, string> = {
  xs: 'h-4 w-4 border-2',
  sm: 'h-5 w-5 border-2',
  md: 'h-7 w-7 border-2',
  lg: 'h-8 w-8 border-4',
}

export function Spinner({
  size = 'md',
  'aria-label': ariaLabel = 'Loading',
  className,
}: SpinnerProps) {
  return (
    <span role="status" aria-label={ariaLabel} className="inline-flex">
      <span
        aria-hidden="true"
        className={clsx(
          'inline-block rounded-full border-accent border-t-transparent motion-safe:animate-spin',
          SIZE_CLASSES[size],
          className,
        )}
      />
      <span className="sr-only">Loading…</span>
    </span>
  )
}
