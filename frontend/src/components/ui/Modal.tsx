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

      {/* Panel container.
          `max-h-full` + a flex column is what keeps a tall modal usable: the
          panel can never grow past the viewport, so the footer stays on screen
          and the *body* scrolls instead. Without it the panel overflows a short
          viewport in both directions and — because neither this container nor
          the page scrolls — the footer buttons become unreachable: the step
          editor could not be saved or cancelled below roughly 830px of viewport
          height (SFBL-405). */}
      <div className="fixed inset-0 flex items-center justify-center p-4">
        <DialogPanel
          className={clsx(
            'bg-surface-elevated rounded-lg w-full overflow-hidden',
            'flex flex-col max-h-full',
            OVERLAY_SHADOW_CLASS,
            sizeClasses[size],
          )}
        >
          {/* Header — fixed; shrink-0 so it is never squeezed by a long body. */}
          {(title || description) && (
            <div className="px-6 py-4 border-b border-border-base shrink-0">
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

          {/* Body — the only scrolling region. `min-h-0` is required: a flex
              child defaults to min-height:auto, which refuses to shrink below
              its content and would reintroduce the overflow this fixes. */}
          <div className="px-6 py-4 overflow-y-auto min-h-0 flex-1">{children}</div>

          {/* Footer — fixed; always reachable however long the body is. */}
          {footer && (
            <div className="px-6 py-4 border-t border-border-base bg-surface-sunken flex justify-end gap-3 shrink-0">
              {footer}
            </div>
          )}
        </DialogPanel>
      </div>
    </Dialog>
  )
}
