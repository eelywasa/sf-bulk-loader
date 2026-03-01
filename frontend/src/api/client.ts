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
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`

  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
    ...init,
  })

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

export const api = {
  get: <T>(path: string): Promise<T> => request<T>(path),

  post: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  put: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, {
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  delete: <T = void>(path: string): Promise<T> =>
    request<T>(path, { method: 'DELETE' }),
}
