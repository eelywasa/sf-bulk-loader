import React from 'react'
import clsx from 'clsx'

export interface CardProps {
  title?: string
  subtitle?: string
  actions?: React.ReactNode
  children: React.ReactNode
  className?: string
  padding?: boolean
}

export function Card({ title, subtitle, actions, children, className, padding = true }: CardProps) {
  const hasHeader = title || actions

  return (
    <div className={clsx('bg-white rounded-lg border border-gray-200 shadow-sm', className)}>
      {hasHeader && (
        <div className="flex items-start justify-between px-6 py-4 border-b border-gray-200">
          <div>
            {title && <h3 className="text-base font-semibold text-gray-900">{title}</h3>}
            {subtitle && <p className="mt-0.5 text-sm text-gray-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2 ml-4">{actions}</div>}
        </div>
      )}
      <div className={clsx(padding && 'p-6')}>{children}</div>
    </div>
  )
}
