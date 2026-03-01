import clsx from 'clsx'

export type ProgressColor = 'blue' | 'green' | 'red' | 'orange'

export interface ProgressProps {
  /** Value 0–100 */
  value: number
  label?: string
  showValue?: boolean
  color?: ProgressColor
  size?: 'sm' | 'md'
  className?: string
}

const colorClasses: Record<ProgressColor, string> = {
  blue: 'bg-blue-600',
  green: 'bg-green-500',
  red: 'bg-red-500',
  orange: 'bg-orange-500',
}

const sizeClasses = {
  sm: 'h-1.5',
  md: 'h-2',
}

export function Progress({
  value,
  label,
  showValue = false,
  color = 'blue',
  size = 'md',
  className,
}: ProgressProps) {
  const clamped = Math.max(0, Math.min(100, value))

  return (
    <div className={className}>
      {(label !== undefined || showValue) && (
        <div className="flex justify-between items-center mb-1">
          {label !== undefined && <span className="text-xs text-gray-500">{label}</span>}
          {showValue && <span className="text-xs font-medium text-gray-700">{clamped}%</span>}
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className={clsx('w-full bg-gray-200 rounded-full overflow-hidden', sizeClasses[size])}
      >
        <div
          className={clsx(
            'h-full rounded-full transition-all duration-300',
            colorClasses[color],
          )}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  )
}
