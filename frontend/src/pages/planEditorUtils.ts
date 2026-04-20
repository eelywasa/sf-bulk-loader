import { ApiError } from '../api/client'
import type { ApiValidationError, StepPreviewResponse, ValidateSoqlResponse } from '../api/types'

// ─── Form types ───────────────────────────────────────────────────────────────

export interface PlanFormData {
  name: string
  description: string
  connection_id: string
  abort_on_step_failure: boolean
  error_threshold_pct: string
  max_parallel_jobs: string
  output_connection_id: string
}

export const EMPTY_PLAN_FORM: PlanFormData = {
  name: '',
  description: '',
  connection_id: '',
  abort_on_step_failure: true,
  error_threshold_pct: '10',
  max_parallel_jobs: '5',
  output_connection_id: '',
}

export interface StepFormData {
  object_name: string
  operation: string
  csv_file_pattern: string
  soql: string
  partition_size: string
  external_id_field: string
  assignment_rule_id: string
  input_connection_id: string
}

export const EMPTY_STEP_FORM: StepFormData = {
  object_name: '',
  operation: 'insert',
  csv_file_pattern: '',
  soql: '',
  partition_size: '10000',
  external_id_field: '',
  assignment_rule_id: '',
  input_connection_id: '',
}

// ─── Preview state ────────────────────────────────────────────────────────────

export type PreviewEntry =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; kind: 'dml'; data: StepPreviewResponse }
  | { status: 'success'; kind: 'query'; data: ValidateSoqlResponse }
  | { status: 'error'; message: string }

// ─── Constants ────────────────────────────────────────────────────────────────

export const OPERATIONS = [
  { value: 'insert', label: 'Insert' },
  { value: 'update', label: 'Update' },
  { value: 'upsert', label: 'Upsert' },
  { value: 'delete', label: 'Delete' },
  { value: 'query', label: 'Query' },
  { value: 'queryAll', label: 'Query All (incl. deleted)' },
] as const

export const QUERY_OPERATIONS = new Set(['query', 'queryAll'])

export function isQueryOp(operation: string): boolean {
  return QUERY_OPERATIONS.has(operation)
}

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
    case 'query':
    case 'queryAll':
      return 'neutral' as const
    default:
      return 'neutral' as const
  }
}

/**
 * Client-side floor validation for SOQL: non-empty, contains SELECT and FROM.
 * Server is authoritative — this only gates the save button locally.
 */
export function validateSoqlClientSide(soql: string): string | null {
  if (!soql.trim()) return 'SOQL query is required.'
  const upper = soql.toUpperCase()
  if (!upper.includes('SELECT')) return 'SOQL must contain SELECT.'
  if (!upper.includes('FROM')) return 'SOQL must contain FROM.'
  return null
}
