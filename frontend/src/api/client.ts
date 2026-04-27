import type { ApiValidationError, StructuredErrorDetail } from './types'

export const BASE_URL = import.meta.env.VITE_API_URL ?? ''

// ─── Token storage helpers ────────────────────────────────────────────────────

const TOKEN_KEY = 'auth_token'

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

// ─── ApiError class ───────────────────────────────────────────────────────────

export class ApiError extends Error {
  readonly status: number
  readonly detail?: string | ApiValidationError[] | StructuredErrorDetail
  readonly code?: string

  constructor({
    status,
    message,
    detail,
    code,
  }: {
    status: number
    message: string
    detail?: string | ApiValidationError[] | StructuredErrorDetail
    code?: string
  }) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
  }

  validationMessages(): string[] {
    if (Array.isArray(this.detail)) {
      return this.detail.map((e) => `${e.loc.join('.')}: ${e.msg}`)
    }
    return [this.message]
  }
}

// ─── Error body parsing ───────────────────────────────────────────────────────

export interface ParsedErrorBody {
  message: string
  detail?: string | ApiValidationError[] | StructuredErrorDetail
  code?: string
}

/**
 * Read a non-OK Response and produce {message, detail, code} ready for an
 * ApiError. Centralised so endpoints that bypass apiFetch (header-needing
 * paths in endpoints.ts) can surface structured-detail errors the same way.
 *
 * Branch order matters — the first match wins:
 *   1. 422 with array detail → Pydantic validation; surface first error message.
 *   2. string detail        → legacy plain-string detail.
 *   3. object detail        → FastAPI HTTPException(detail={error, message}).
 *   4. top-level message    → fallback for non-FastAPI shapes.
 *   5. anything else        → keep the explicit-statusText fallback the caller seeded.
 */
export async function parseErrorBody(response: Response): Promise<ParsedErrorBody> {
  let detail: string | ApiValidationError[] | StructuredErrorDetail | undefined
  let message = response.statusText || `HTTP ${response.status}`
  let code: string | undefined

  try {
    const body = await response.json()
    if (response.status === 422 && Array.isArray(body.detail)) {
      detail = body.detail as ApiValidationError[]
      const first = (body.detail as ApiValidationError[])[0]
      message = first?.msg ? `${first.msg}` : 'Validation error'
    } else if (typeof body.detail === 'string') {
      detail = body.detail
      message = body.detail
    } else if (
      body.detail !== null &&
      typeof body.detail === 'object' &&
      !Array.isArray(body.detail)
    ) {
      const structured = body.detail as StructuredErrorDetail
      detail = structured
      if (typeof structured.message === 'string') message = structured.message
      if (typeof structured.error === 'string') code = structured.error
    } else if (typeof body.message === 'string') {
      message = body.message
    }
  } catch {
    // JSON parse failed — keep statusText as message
  }

  return { message, detail, code }
}

// ─── Core fetch wrapper ───────────────────────────────────────────────────────

export async function apiFetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`

  const headers = new Headers(init?.headers)
  if (typeof init?.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  // Inject auth token when present
  const token = getStoredToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(url, { ...init, headers })

  if (!response.ok) {
    // On 401, clear session and redirect to login if a token was present
    if (response.status === 401 && token) {
      clearStoredToken()
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }

    const { message, detail, code } = await parseErrorBody(response)
    throw new ApiError({ status: response.status, message, detail, code })
  }

  // 204 No Content or 202 Accepted with empty body — return undefined
  if (response.status === 204 || response.status === 202) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

// ─── Convenience helpers ──────────────────────────────────────────────────────

export function apiGet<T = unknown>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'GET' })
}

export function apiPost<T = unknown>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })
}

export function apiPut<T = unknown>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'PUT',
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })
}

export function apiDelete<T = void>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'DELETE' })
}

export async function apiFetchBlob(path: string): Promise<{ blob: Blob; filename: string }> {
  const url = `${BASE_URL}${path}`
  const headers = new Headers()
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(url, { headers })

  if (!response.ok) {
    if (response.status === 401 && token) {
      clearStoredToken()
      if (window.location.pathname !== '/login') window.location.href = '/login'
    }
    throw new ApiError({ status: response.status, message: `HTTP ${response.status}` })
  }

  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const filename = match?.[1] ?? path.split('/').pop() ?? 'download.csv'
  const blob = await response.blob()
  return { blob, filename }
}

// ─── Object-style API (used by endpoints) ────────────────────────────────────

export const api = {
  get: apiGet,
  post: apiPost,
  put: apiPut,
  delete: apiDelete,
}
