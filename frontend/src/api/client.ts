import type { ApiValidationError } from './types'

// ─── ApiError class ───────────────────────────────────────────────────────────

export class ApiError extends Error {
  readonly status: number
  readonly detail?: string | ApiValidationError[]
  readonly code?: string

  constructor({
    status,
    message,
    detail,
    code,
  }: {
    status: number
    message: string
    detail?: string | ApiValidationError[]
    code?: string
  }) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
  }

  /**
   * Returns a human-readable summary of all validation errors.
   * Only meaningful when `detail` is an array (422 response).
   */
  validationMessages(): string[] {
    if (!Array.isArray(this.detail)) return [this.message]
    return this.detail.map((e) => `${e.loc.join('.')}: ${e.msg}`)
  }
}

// ─── Core fetch wrapper ───────────────────────────────────────────────────────

/**
 * Wraps `fetch` with:
 *  - JSON Content-Type header by default (skipped for FormData / non-object bodies)
 *  - Automatic `ApiError` thrown on non-2xx responses
 *  - 422 bodies are mapped to `detail: ApiValidationError[]`
 *  - All other error bodies are mapped to `detail: string`
 *  - 204 No Content is returned as `undefined`
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)

  // Auto-set JSON content type when body is a plain string/object (not FormData/Blob)
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(path, { ...init, headers })

  if (!res.ok) {
    let detail: string | ApiValidationError[] | undefined
    let message: string

    try {
      const body = (await res.json()) as { detail?: unknown; message?: unknown }

      if (res.status === 422 && Array.isArray(body.detail)) {
        detail = body.detail as ApiValidationError[]
        message = 'Validation error'
      } else if (typeof body.detail === 'string') {
        detail = body.detail
        message = body.detail
      } else if (typeof body.message === 'string') {
        message = body.message
        detail = body.message
      } else {
        message = `HTTP ${res.status}: ${res.statusText}`
        detail = message
      }
    } catch {
      message = `HTTP ${res.status}: ${res.statusText}`
    }

    throw new ApiError({ status: res.status, message, detail })
  }

  if (res.status === 204) {
    return undefined as T
  }

  return res.json() as Promise<T>
}

// ─── Convenience helpers ──────────────────────────────────────────────────────

export function apiGet<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'GET' })
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'PUT',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

export function apiDelete<T = void>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'DELETE' })
}
