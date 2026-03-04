import React, { createContext, useCallback, useContext, useState } from 'react'
import clsx from 'clsx'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import type { IconDefinition } from '@fortawesome/fontawesome-svg-core'
import {
  faCircleCheck,
  faCircleXmark,
  faTriangleExclamation,
  faCircleInfo,
} from '@fortawesome/free-solid-svg-icons'

export type ToastType = 'success' | 'error' | 'info' | 'warning'

export interface ToastItem {
  id: string
  type: ToastType
  message: string
}

interface ToastContextValue {
  toasts: ToastItem[]
  addToast: (toast: Omit<ToastItem, 'id'>) => void
  removeToast: (id: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const toastStyles: Record<ToastType, { container: string; icon: string; faIcon: IconDefinition }> = {
  success: {
    container: 'bg-white border-l-4 border-green-500',
    icon: 'text-green-500',
    faIcon: faCircleCheck,
  },
  error: {
    container: 'bg-white border-l-4 border-red-500',
    icon: 'text-red-500',
    faIcon: faCircleXmark,
  },
  warning: {
    container: 'bg-white border-l-4 border-orange-500',
    icon: 'text-orange-500',
    faIcon: faTriangleExclamation,
  },
  info: {
    container: 'bg-white border-l-4 border-blue-500',
    icon: 'text-blue-500',
    faIcon: faCircleInfo,
  },
}

function ToastContainer({
  toasts,
  onRemove,
}: {
  toasts: ToastItem[]
  onRemove: (id: string) => void
}) {
  if (toasts.length === 0) return null

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80"
    >
      {toasts.map((toast) => {
        const style = toastStyles[toast.type]
        return (
          <div
            key={toast.id}
            role="alert"
            className={clsx(
              'flex items-start gap-3 p-4 rounded shadow-lg',
              style.container,
            )}
          >
            <FontAwesomeIcon icon={style.faIcon} className={clsx('flex-shrink-0 w-5 h-5', style.icon)} />
            <p className="flex-1 text-sm text-gray-800">{toast.message}</p>
            <button
              onClick={() => onRemove(toast.id)}
              aria-label="Dismiss notification"
              className="flex-shrink-0 text-gray-400 hover:text-gray-600 transition-colors"
            >
              ×
            </button>
          </div>
        )
      })}
    </div>
  )
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const addToast = useCallback(
    (toast: Omit<ToastItem, 'id'>) => {
      const id = Math.random().toString(36).slice(2)
      setToasts((prev) => [...prev, { ...toast, id }])
      setTimeout(() => removeToast(id), 5000)
    },
    [removeToast],
  )

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')

  return {
    success: (message: string) => ctx.addToast({ type: 'success', message }),
    error: (message: string) => ctx.addToast({ type: 'error', message }),
    info: (message: string) => ctx.addToast({ type: 'info', message }),
    warning: (message: string) => ctx.addToast({ type: 'warning', message }),
  }
}
