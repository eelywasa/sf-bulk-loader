import type { ApiValidationError } from './types'

const BASE_URL = import.meta.env.VITE_API_URL ?? ''

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

  validationMessages(): string[] {
    if (Array.isArray(this.detail)) {
      return this.detail.map((e) => `${e.loc.join('.')}: ${e.msg}`)
    }
    return [this.message]
  }
}

// ─── Core fetch wrapper ───────────────────────────────────────────────────────

export async function apiFetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`

  const headers = new Headers(init?.headers)
  if (typeof init?.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(url, { ...init, headers })

  if (!response.ok) {
    let detail: string | ApiValidationError[] | undefined
    let message = response.statusText || `HTTP ${response.status}`

    try {
      const body = await response.json()
      if (response.status === 422 && Array.isArray(body.detail)) {
        detail = body.detail as ApiValidationError[]
        message = 'Validation error'
      } else if (typeof body.detail === 'string') {
        detail = body.detail
        message = body.detail
      } else if (typeof body.message === 'string') {
        message = body.message
      }
    } catch {
      // JSON parse failed — keep statusText as message
    }

    throw new ApiError({ status: response.status, message, detail })
  }

  // 204 No Content — return undefined
  if (response.status === 204) {
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

// ─── Object-style API (used by endpoints) ────────────────────────────────────

export const api = {
  get: apiGet,
  post: apiPost,
  put: apiPut,
  delete: apiDelete,
}
