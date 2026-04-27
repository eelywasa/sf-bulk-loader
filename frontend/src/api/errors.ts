import { ApiError } from './client'
import type { ApiValidationError } from './types'

/**
 * Format any thrown value as a single user-facing error string.
 *
 * Narrowing order:
 *   1. ApiError → use err.message (already enriched by parseErrorBody/apiFetch
 *      with structured-detail message when available).
 *   2. Error    → use err.message.
 *   3. Anything else → fallback.
 *
 * Use this for toasts and inline form errors where any thrown Error's message
 * is acceptable to surface. The fallback is required and carries the
 * page-specific copy ("Failed to delete connection",
 * "Something went wrong. Please try again.", etc.).
 */
export function formatApiError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback
  if (err instanceof Error) return err.message || fallback
  return fallback
}

/**
 * Strict variant: only surface ApiError messages; use the fallback for plain
 * Errors and anything else.
 *
 * Use this for top-level "page broken" inline error states (FilePicker,
 * FilesPage, JobDetail) where leaking a generic JavaScript Error message
 * (e.g. "Network failure", "Failed to fetch") would be jarring. Backend
 * errors via ApiError carry user-facing copy already; everything else is
 * better hidden behind the fallback.
 */
export function formatApiErrorStrict(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback
  return fallback
}

/**
 * Format any thrown value as a list of error strings, expanding 422 array
 * detail into per-field messages.
 *
 * Use this for forms that render Pydantic validation errors as separate
 * bullets. Non-validation errors collapse to a single-element array
 * containing the same string formatApiError would return.
 */
export function formatApiErrors(err: unknown, fallback: string): string[] {
  if (err instanceof ApiError && Array.isArray(err.detail)) {
    return (err.detail as ApiValidationError[]).map(
      (e) => `${e.loc.slice(1).join('.')} — ${e.msg}`,
    )
  }
  return [formatApiError(err, fallback)]
}
