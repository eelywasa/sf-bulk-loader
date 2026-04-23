import React from 'react'
import clsx from 'clsx'
import {
  BUTTON_BASE_CLASS,
  BUTTON_PRIMARY_COLORS,
  BUTTON_SECONDARY_COLORS,
  BUTTON_GHOST_COLORS,
} from './formStyles'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  children: React.ReactNode
}

const variantClasses: Record<ButtonVariant, string> = {
  primary: BUTTON_PRIMARY_COLORS,
  secondary: BUTTON_SECONDARY_COLORS,
  danger:
    'bg-red-600 text-content-inverse hover:bg-red-700 focus:ring-red-500 border border-transparent',
  ghost: BUTTON_GHOST_COLORS,
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-3 text-base',
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled,
  children,
  className,
  ...props
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={clsx(
        BUTTON_BASE_CLASS,
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...props}
    >
      {loading ? (
        <span className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="h-3.5 w-3.5 border-2 border-current border-t-transparent rounded-full animate-spin"
          />
          {children}
        </span>
      ) : (
        children
      )}
    </button>
  )
}
