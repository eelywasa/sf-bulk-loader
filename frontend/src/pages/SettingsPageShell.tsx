/**
 * SettingsPageShell — shared layout for all admin settings pages.
 *
 * Handles:
 *  - Page header with title + 60s cache propagation callout
 *  - Form rendering with dirty-tracking and Save button
 *  - Success toast on save
 *  - 422 field-level error highlighting
 *  - 403 redirect to /
 *  - Admin guard (redirects non-admins to /)
 */

import { useState, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faRotate, faTriangleExclamation, faCircleInfo } from '@fortawesome/free-solid-svg-icons'
import { Button } from '../components/ui/Button'
import { useToast } from '../components/ui/Toast'
import { useAuth } from '../context/AuthContext'
import { usePermission } from '../hooks/usePermission'
import { getSettingsCategory, updateSettingsCategory } from '../api/endpoints'
import type { SettingValue, SettingsPatch } from '../api/types'
import {
  INPUT_CLASS,
  LABEL_CLASS,
  FIELD_ERROR_OUTLINE,
  ERROR_TEXT_CLASS,
  ALERT_ERROR,
} from '../components/ui/formStyles'
import { MaskedSecretInput } from '../components/ui/MaskedSecretInput'
import clsx from 'clsx'
import { ApiError } from '../api/client'

interface FieldError {
  field: string
  error: string
}

// ─── Field renderer ────────────────────────────────────────────────────────────

function SettingField({
  setting,
  draftValue,
  onChange,
  fieldError,
}: {
  setting: SettingValue
  draftValue: string | number | boolean
  onChange: (key: string, value: string | number | boolean) => void
  fieldError?: string
}) {
  const id = `setting-${setting.key}`

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <label htmlFor={id} className={LABEL_CLASS}>
          {setting.key}
        </label>
        {setting.restart_required && (
          <span
            className="inline-flex items-center gap-1 text-xs font-medium bg-warning-bg border border-warning-border text-warning-text rounded px-1.5 py-0.5"
            title="Changing this setting requires a server restart to take effect"
          >
            <FontAwesomeIcon icon={faRotate} className="w-3 h-3" />
            Restart required
          </span>
        )}
      </div>

      {setting.is_secret ? (
        <MaskedSecretInput
          id={id}
          value={typeof draftValue === 'string' ? draftValue : ''}
          onChange={(v) => onChange(setting.key, v)}
        />
      ) : setting.type === 'bool' ? (
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            id={id}
            type="checkbox"
            checked={Boolean(draftValue)}
            onChange={(e) => onChange(setting.key, e.target.checked)}
            className="rounded border-border-base text-accent focus:ring-accent"
          />
          <span className="text-sm text-content-secondary">Enabled</span>
        </label>
      ) : setting.type === 'int' ? (
        <input
          id={id}
          type="number"
          step="1"
          value={typeof draftValue === 'number' ? draftValue : ''}
          onChange={(e) => onChange(setting.key, e.target.valueAsNumber)}
          className={clsx(INPUT_CLASS, fieldError && FIELD_ERROR_OUTLINE)}
        />
      ) : setting.type === 'float' ? (
        <input
          id={id}
          type="number"
          step="0.01"
          value={typeof draftValue === 'number' ? draftValue : ''}
          onChange={(e) => onChange(setting.key, e.target.valueAsNumber)}
          className={clsx(INPUT_CLASS, fieldError && FIELD_ERROR_OUTLINE)}
        />
      ) : (
        <input
          id={id}
          type="text"
          value={typeof draftValue === 'string' ? draftValue : ''}
          onChange={(e) => onChange(setting.key, e.target.value)}
          className={clsx(INPUT_CLASS, fieldError && FIELD_ERROR_OUTLINE)}
        />
      )}

      {setting.description && (
        <p className="text-xs text-content-muted">{setting.description}</p>
      )}

      {fieldError && <p className={ERROR_TEXT_CLASS}>{fieldError}</p>}
    </div>
  )
}

// ─── Main shell ────────────────────────────────────────────────────────────────

interface SettingsPageShellProps {
  category: string
  title: string
  /** Optional extra content rendered before the form fields (e.g. callouts) */
  preamble?: React.ReactNode
  /** Optional content rendered after the form fields (e.g. test-send section) */
  footer?: React.ReactNode
  /** Filter or reorder the raw settings list before it reaches the form */
  filterSettings?: (settings: SettingValue[]) => SettingValue[]
  /** Group settings into named sections for visual separation */
  sections?: Array<{
    title: string
    keys: string[]
    note?: React.ReactNode
  }>
}

export function SettingsPageShell({
  category,
  title,
  preamble,
  footer,
  filterSettings,
  sections,
}: SettingsPageShellProps) {
  const navigate = useNavigate()
  const toast = useToast()
  const { user } = useAuth()
  const canManageSettings = usePermission('system.settings')
  const queryClient = useQueryClient()

  // Settings gate — redirect users lacking system.settings to /
  useEffect(() => {
    if (user && !canManageSettings) {
      navigate('/', { replace: true })
    }
  }, [user, canManageSettings, navigate])

  const {
    data: categoryData,
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['settings', category],
    queryFn: () => getSettingsCategory(category),
    staleTime: 30_000,
    retry: false,
  })

  const cacheTtl = categoryData?.cacheTtl ?? 60
  const settings = categoryData?.data?.settings ?? []
  const filteredSettings = filterSettings ? filterSettings(settings) : settings

  // Draft state — keyed by setting key
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>({})
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  // Initialise draft from loaded settings
  useEffect(() => {
    if (settings.length > 0) {
      const initial: Record<string, string | number | boolean> = {}
      for (const s of settings) {
        // For secrets the server returns "***"; we show blank (keep-existing)
        if (s.is_secret) {
          initial[s.key] = ''
        } else {
          initial[s.key] = s.value ?? ''
        }
      }
      setDraft(initial)
      setFieldErrors({})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryData])

  const isDirty = settings.some((s) => {
    const orig = s.is_secret ? '' : (s.value ?? '')
    return draft[s.key] !== orig
  })

  const handleChange = useCallback(
    (key: string, value: string | number | boolean) => {
      setDraft((prev) => ({ ...prev, [key]: value }))
      setFieldErrors((prev) => {
        if (!prev[key]) return prev
        const next = { ...prev }
        delete next[key]
        return next
      })
    },
    [],
  )

  const mutation = useMutation({
    mutationFn: async () => {
      // Build patch with only changed fields
      const patch: SettingsPatch = {}
      for (const s of settings) {
        const orig = s.is_secret ? '' : (s.value ?? '')
        const current = draft[s.key]
        if (current !== orig) {
          patch[s.key] = current
        }
      }
      return updateSettingsCategory(category, patch)
    },
    onSuccess: (result) => {
      queryClient.setQueryData(['settings', category], result)
      toast.success('Settings saved')
      setFieldErrors({})
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 403) {
          navigate('/', { replace: true })
          return
        }
        if (err.status === 422 && Array.isArray(err.detail)) {
          const newErrors: Record<string, string> = {}
          for (const fe of (err.detail as unknown as FieldError[])) {
            if (fe.field && fe.error) {
              newErrors[fe.field] = fe.error
            }
          }
          setFieldErrors(newErrors)
          toast.error("Some settings couldn't be saved")
          return
        }
      }
      toast.error(err instanceof Error ? err.message : 'Save failed')
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    mutation.mutate()
  }

  // Handle 403 on load
  useEffect(() => {
    if (loadError instanceof ApiError && loadError.status === 403) {
      navigate('/', { replace: true })
    }
  }, [loadError, navigate])

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="h-6 w-48 bg-surface-raised rounded animate-pulse mb-4" />
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 bg-surface-raised rounded animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (loadError && !(loadError instanceof ApiError && loadError.status === 403)) {
    return (
      <div className="p-6">
        <p className="text-sm text-error-text">
          Failed to load settings: {loadError instanceof Error ? loadError.message : 'Unknown error'}
        </p>
      </div>
    )
  }

  // Render fields — optionally grouped into sections
  function renderFields(keys?: string[]) {
    const toRender = keys
      ? filteredSettings.filter((s) => keys.includes(s.key))
      : filteredSettings
    return toRender.map((s) => (
      <SettingField
        key={s.key}
        setting={s}
        draftValue={draft[s.key] ?? ''}
        onChange={handleChange}
        fieldError={fieldErrors[s.key]}
      />
    ))
  }

  return (
    <div className="p-6 max-w-3xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-content-primary">{title}</h1>
        {cacheTtl > 0 && (
          <p className="mt-1 text-xs text-content-muted flex items-center gap-1.5">
            <FontAwesomeIcon icon={faCircleInfo} className="w-3.5 h-3.5 flex-shrink-0" />
            Changes take up to {cacheTtl}s to propagate across workers.
          </p>
        )}
      </div>

      {preamble}

      <form onSubmit={handleSubmit} className="space-y-6">
        {sections ? (
          sections.map((sec) => (
            <section
              key={sec.title}
              className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4"
            >
              <h2 className="text-sm font-semibold text-content-primary">{sec.title}</h2>
              {sec.note && <div>{sec.note}</div>}
              {renderFields(sec.keys)}
            </section>
          ))
        ) : (
          <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
            {renderFields()}
          </section>
        )}

        {Object.keys(fieldErrors).length > 0 && (
          <div className={`${ALERT_ERROR} flex items-start gap-2`}>
            <FontAwesomeIcon icon={faTriangleExclamation} className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium">Some settings couldn't be saved</p>
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
            type="submit"
            disabled={!isDirty || mutation.isPending}
            loading={mutation.isPending}
          >
            Save
          </Button>
          {mutation.isSuccess && !isDirty && (
            <span className="text-xs text-success-text">Saved</span>
          )}
        </div>
      </form>

      {footer && <div className="mt-8">{footer}</div>}
    </div>
  )
}
