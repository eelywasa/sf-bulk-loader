import React from 'react'
import { Dialog, DialogPanel, DialogTitle } from '@headlessui/react'
import clsx from 'clsx'
import { OVERLAY_SHADOW_CLASS } from './formStyles'

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl'

export interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  description?: string
  size?: ModalSize
  children: React.ReactNode
  footer?: React.ReactNode
  closeOnBackdropClick?: boolean
}

const sizeClasses: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
}

export function Modal({
  open,
  onClose,
  title,
  description,
  size = 'md',
  children,
  footer,
  closeOnBackdropClick = true,
}: ModalProps) {
  return (
    <Dialog open={open} onClose={closeOnBackdropClick ? onClose : () => {}} className="relative z-50">
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/40" aria-hidden="true" />

      {/* Panel container */}
      <div className="fixed inset-0 flex items-center justify-center p-4">
        <DialogPanel
          className={clsx(
            'bg-surface-elevated rounded-lg w-full overflow-hidden',
            OVERLAY_SHADOW_CLASS,
            sizeClasses[size],
          )}
        >
          {/* Header */}
          {(title || description) && (
            <div className="px-6 py-4 border-b border-border-base">
              {title && (
                <DialogTitle className="text-lg font-semibold text-content-primary">
                  {title}
                </DialogTitle>
              )}
              {description && (
                <p className="mt-1 text-sm text-content-muted">{description}</p>
              )}
            </div>
          )}

          {/* Body */}
          <div className="px-6 py-4">{children}</div>

          {/* Footer */}
          {footer && (
            <div className="px-6 py-4 border-t border-border-base bg-surface-sunken flex justify-end gap-3">
              {footer}
            </div>
          )}
        </DialogPanel>
      </div>
    </Dialog>
  )
}
