import React from 'react'
import clsx from 'clsx'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolderOpen } from '@fortawesome/free-solid-svg-icons'

export interface EmptyStateProps {
  title: string
  description?: string
  action?: React.ReactNode
  icon?: React.ReactNode
  className?: string
}

function DefaultIcon() {
  return <FontAwesomeIcon icon={faFolderOpen} className="h-12 w-12 text-gray-300 dark:text-gray-600" />
}

export function EmptyState({ title, description, action, icon, className }: EmptyStateProps) {
  return (
    <div className={clsx('flex flex-col items-center justify-center py-12 px-4 text-center', className)}>
      <div className="mb-4">{icon ?? <DefaultIcon />}</div>
      <h3 className="text-base font-semibold text-gray-900">{title}</h3>
      {description && (
        <p className="mt-1 text-sm text-gray-500 max-w-sm">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
