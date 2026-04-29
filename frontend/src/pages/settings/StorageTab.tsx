/**
 * Storage settings tab — desktop-only (SFBL-257).
 *
 * Lets the user change the input and output directories via the native
 * folder picker (Electron IPC) or by typing an absolute path directly.
 * Changes take effect immediately without restarting the app.
 */

import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faFolderOpen, faTriangleExclamation } from '@fortawesome/free-solid-svg-icons'
import { Button } from '../../components/ui/Button'
import { useToast } from '../../components/ui/Toast'
import { getSettingsCategory, updateSettingsCategory } from '../../api/endpoints'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  FIELD_ERROR_OUTLINE,
  ERROR_TEXT_CLASS,
  ALERT_ERROR,
} from '../../components/ui/formStyles'
import { ApiError } from '../../api/client'
import { formatApiError } from '../../api/errors'
import clsx from 'clsx'

interface DirState {
  desktop_input_dir: string
  desktop_output_dir: string
}

interface FieldErrors {
  desktop_input_dir?: string
  desktop_output_dir?: string
}

declare global {
  interface Window {
    electronAPI?: {
      backendUrl: string
      selectDirectory: () => Promise<string | null>
    }
  }
}

const FIELDS: { key: keyof DirState; label: string; description: string }[] = [
  {
    key: 'desktop_input_dir',
    label: 'Input directory',
    description: 'CSV files are read from this directory. Changes take effect immediately.',
  },
  {
    key: 'desktop_output_dir',
    label: 'Output directory',
    description: 'Result CSVs are written to this directory. Changes take effect immediately.',
  },
]

export function StorageTab() {
  const toast = useToast()
  const queryClient = useQueryClient()

  const { data, isLoading, error: loadError } = useQuery({
    queryKey: ['settings', 'storage'],
    queryFn: () => getSettingsCategory('storage'),
    staleTime: 30_000,
    retry: false,
  })

  const settings = data?.data?.settings ?? []

  const [draft, setDraft] = useState<DirState>({
    desktop_input_dir: '',
    desktop_output_dir: '',
  })
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})

  useEffect(() => {
    if (settings.length > 0) {
      const initial: Partial<DirState> = {}
      for (const s of settings) {
        if (s.key === 'desktop_input_dir' || s.key === 'desktop_output_dir') {
          initial[s.key] = String(s.value ?? '')
        }
      }
      setDraft((prev) => ({ ...prev, ...initial }))
      setFieldErrors({})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  const serverValues: DirState = {
    desktop_input_dir:
      String(settings.find((s) => s.key === 'desktop_input_dir')?.value ?? ''),
    desktop_output_dir:
      String(settings.find((s) => s.key === 'desktop_output_dir')?.value ?? ''),
  }

  const isDirty =
    draft.desktop_input_dir !== serverValues.desktop_input_dir ||
    draft.desktop_output_dir !== serverValues.desktop_output_dir

  function handleChange(key: keyof DirState, value: string) {
    setDraft((prev) => ({ ...prev, [key]: value }))
    setFieldErrors((prev) => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  async function handleBrowse(key: keyof DirState) {
    if (!window.electronAPI?.selectDirectory) return
    const chosen = await window.electronAPI.selectDirectory()
    if (chosen) {
      handleChange(key, chosen)
    }
  }

  const mutation = useMutation({
    mutationFn: () => {
      const patch: Record<string, string> = {}
      if (draft.desktop_input_dir !== serverValues.desktop_input_dir) {
        patch.desktop_input_dir = draft.desktop_input_dir
      }
      if (draft.desktop_output_dir !== serverValues.desktop_output_dir) {
        patch.desktop_output_dir = draft.desktop_output_dir
      }
      return updateSettingsCategory('storage', patch)
    },
    onSuccess: (result) => {
      queryClient.setQueryData(['settings', 'storage'], result)
      toast.success('Storage settings saved')
      setFieldErrors({})
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 422 && Array.isArray(err.detail)) {
          const newErrors: FieldErrors = {}
          for (const fe of err.detail as unknown as { field: string; error: string }[]) {
            if (fe.field === 'desktop_input_dir' || fe.field === 'desktop_output_dir') {
              newErrors[fe.field] = fe.error
            }
          }
          setFieldErrors(newErrors)
          toast.error("Couldn't save storage settings")
          return
        }
      }
      toast.error(formatApiError(err, 'Save failed'))
    },
  })

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[1, 2].map((i) => (
          <div key={i} className="h-10 bg-surface-raised rounded animate-pulse" />
        ))}
      </div>
    )
  }

  if (loadError) {
    return (
      <p className="text-sm text-error-text">
        Failed to load storage settings:{' '}
        {loadError instanceof Error ? loadError.message : 'Unknown error'}
      </p>
    )
  }

  const hasErrors = Object.keys(fieldErrors).length > 0

  return (
    <div className="space-y-6">
      <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-5">
        {FIELDS.map(({ key, label, description }) => (
          <div key={key} className="space-y-1">
            <label htmlFor={`storage-${key}`} className={LABEL_CLASS}>
              {label}
            </label>
            <div className="flex gap-2">
              <input
                id={`storage-${key}`}
                type="text"
                value={draft[key]}
                onChange={(e) => handleChange(key, e.target.value)}
                placeholder="/path/to/directory"
                className={clsx('flex-1', INPUT_CLASS, fieldErrors[key] && FIELD_ERROR_OUTLINE)}
              />
              {window.electronAPI?.selectDirectory && (
                <button
                  type="button"
                  onClick={() => handleBrowse(key)}
                  title="Browse for folder"
                  className="
                    inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md
                    border border-border-strong bg-surface-raised
                    text-content-secondary hover:bg-surface-hover
                    focus:outline-none focus:ring-2 focus:ring-border-focus
                    transition-colors duration-150 flex-shrink-0
                  "
                >
                  <FontAwesomeIcon icon={faFolderOpen} className="w-4 h-4" aria-hidden="true" />
                  Browse
                </button>
              )}
            </div>
            <p className="text-xs text-content-muted">{description}</p>
            {fieldErrors[key] && <p className={ERROR_TEXT_CLASS}>{fieldErrors[key]}</p>}
          </div>
        ))}
      </section>

      {hasErrors && (
        <div className={`${ALERT_ERROR} flex items-start gap-2`}>
          <FontAwesomeIcon
            icon={faTriangleExclamation}
            className="w-4 h-4 flex-shrink-0 mt-0.5"
            aria-hidden="true"
          />
          <div>
            <p className="font-medium">Couldn't save storage settings</p>
            <ul className="mt-1 list-disc list-inside space-y-0.5">
              {Object.entries(fieldErrors).map(([k, v]) => (
                <li key={k} className="text-xs">
                  <span className="font-mono">{k}</span>: {v}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="flex items-center gap-4">
        <Button
          type="button"
          disabled={!isDirty || mutation.isPending}
          loading={mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          Save
        </Button>
        {mutation.isSuccess && !isDirty && (
          <span className="text-xs text-success-text">Saved</span>
        )}
      </div>
    </div>
  )
}
