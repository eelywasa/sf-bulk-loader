import clsx from 'clsx'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faHexagonNodes } from '@fortawesome/free-solid-svg-icons'

export type BrandMarkSize = 'sm' | 'md' | 'lg'

export interface BrandMarkProps {
  size?: BrandMarkSize
  className?: string
}

const CONTAINER_CLASSES: Record<BrandMarkSize, string> = {
  sm: 'w-6 h-6',
  md: 'w-7 h-7',
  lg: 'w-8 h-8',
}

const ICON_CLASSES: Record<BrandMarkSize, string> = {
  sm: 'w-4 h-4',
  md: 'w-4 h-4',
  lg: 'w-5 h-5',
}

export function BrandMark({ size = 'md', className }: BrandMarkProps) {
  return (
    <div
      aria-hidden="true"
      className={clsx(
        'rounded bg-brand flex items-center justify-center flex-shrink-0',
        CONTAINER_CLASSES[size],
        className,
      )}
    >
      <FontAwesomeIcon icon={faHexagonNodes} className={clsx('text-white', ICON_CLASSES[size])} aria-hidden="true" />
    </div>
  )
}
