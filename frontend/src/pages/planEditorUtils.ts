import { ApiError } from '../api/client'
import type { ApiValidationError, StepPreviewResponse } from '../api/types'

// ─── Form types ───────────────────────────────────────────────────────────────

export interface PlanFormData {
  name: string
  description: string
  connection_id: string
  abort_on_step_failure: boolean
  error_threshold_pct: string
  max_parallel_jobs: string
}

export const EMPTY_PLAN_FORM: PlanFormData = {
  name: '',
  description: '',
  connection_id: '',
  abort_on_step_failure: true,
  error_threshold_pct: '10',
  max_parallel_jobs: '5',
}

export interface StepFormData {
  object_name: string
  operation: string
  csv_file_pattern: string
  partition_size: string
  external_id_field: string
  assignment_rule_id: string
  input_connection_id: string
}

export const EMPTY_STEP_FORM: StepFormData = {
  object_name: '',
  operation: 'insert',
  csv_file_pattern: '',
  partition_size: '10000',
  external_id_field: '',
  assignment_rule_id: '',
  input_connection_id: '',
}

// ─── Preview state ────────────────────────────────────────────────────────────

export type PreviewEntry =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: StepPreviewResponse }
  | { status: 'error'; message: string }

// ─── Constants ────────────────────────────────────────────────────────────────

export const OPERATIONS = [
  { value: 'insert', label: 'Insert' },
  { value: 'update', label: 'Update' },
  { value: 'upsert', label: 'Upsert' },
  { value: 'delete', label: 'Delete' },
] as const

export const INPUT_CLASS =
  'w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

export const LABEL_CLASS = 'block text-sm font-medium text-gray-700 mb-1'

// ─── Helpers ──────────────────────────────────────────────────────────────────

export function extractErrors(err: unknown): string[] {
  if (err instanceof ApiError) {
    if (Array.isArray(err.detail)) {
      return (err.detail as ApiValidationError[]).map(
        (e) => `${e.loc.slice(1).join('.')} — ${e.msg}`,
      )
    }
    if (err.message) return [err.message]
  }
  if (err instanceof Error) return [err.message]
  return ['An unexpected error occurred']
}

export function operationVariant(op: string) {
  switch (op) {
    case 'insert':
      return 'info' as const
    case 'update':
      return 'warning' as const
    case 'upsert':
      return 'success' as const
    case 'delete':
      return 'error' as const
    default:
      return 'neutral' as const
  }
}
