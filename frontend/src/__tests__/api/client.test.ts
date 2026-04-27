import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ApiError, apiFetch, apiGet, apiPost, apiPut, apiDelete, getStoredToken, storeToken, clearStoredToken } from '../../api/client'

// Helper to create a mock Response.
// statusText is passed explicitly so tests pin behaviour rather than depending
// on the runtime's default mapping (per fetch spec, Response.statusText defaults
// to "" for unknown statuses, and even for known statuses the value differs
// across runtimes).
function mockResponse(
  body: unknown,
  {
    status = 200,
    statusText = '',
    headers = {},
  }: { status?: number; statusText?: string; headers?: Record<string, string> } = {},
): Response {
  const json = JSON.stringify(body)
  return new Response(json, {
    status,
    statusText,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function mockEmptyResponse(status = 204): Response {
  return new Response(null, { status })
}

describe('ApiError', () => {
  it('has correct name', () => {
    const err = new ApiError({ status: 400, message: 'Bad request' })
    expect(err.name).toBe('ApiError')
    expect(err.message).toBe('Bad request')
    expect(err.status).toBe(400)
  })

  it('stores detail as string', () => {
    const err = new ApiError({ status: 404, message: 'Not found', detail: 'Resource missing' })
    expect(err.detail).toBe('Resource missing')
  })

  it('stores detail as ApiValidationError array', () => {
    const detail = [{ type: 'missing', loc: ['body', 'name'], msg: 'Field required' }]
    const err = new ApiError({ status: 422, message: 'Validation error', detail })
    expect(err.detail).toEqual(detail)
  })

  it('validationMessages returns message when detail is a string', () => {
    const err = new ApiError({ status: 400, message: 'Bad request', detail: 'Some detail' })
    expect(err.validationMessages()).toEqual(['Bad request'])
  })

  it('validationMessages formats each validation error', () => {
    const detail = [
      { type: 'missing', loc: ['body', 'name'], msg: 'Field required' },
      { type: 'string_type', loc: ['body', 'client_id'], msg: 'Must be a string' },
    ]
    const err = new ApiError({ status: 422, message: 'Validation error', detail })
    expect(err.validationMessages()).toEqual([
      'body.name: Field required',
      'body.client_id: Must be a string',
    ])
  })

  it('is an instance of Error', () => {
    const err = new ApiError({ status: 500, message: 'Server error' })
    expect(err).toBeInstanceOf(Error)
    expect(err).toBeInstanceOf(ApiError)
  })
})

describe('apiFetch', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns parsed JSON on 200', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ id: '1', name: 'Test' }))
    const result = await apiFetch<{ id: string; name: string }>('/api/test')
    expect(result).toEqual({ id: '1', name: 'Test' })
  })

  it('returns undefined on 204 No Content', async () => {
    vi.mocked(fetch).mockResolvedValue(mockEmptyResponse(204))
    const result = await apiFetch<void>('/api/test')
    expect(result).toBeUndefined()
  })

  it('throws ApiError with 422 detail array on validation failure', async () => {
    const detail = [{ type: 'missing', loc: ['body', 'name'], msg: 'Field required', input: null }]
    vi.mocked(fetch).mockResolvedValue(mockResponse({ detail }, { status: 422 }))

    await expect(apiFetch('/api/test', { method: 'POST', body: '{}' })).rejects.toSatisfy(
      (err: unknown) => {
        const e = err as ApiError
        return e instanceof ApiError && e.status === 422 && Array.isArray(e.detail)
      },
    )
  })

  it('throws ApiError with string detail on 400', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({ detail: 'Connection not found' }, { status: 400 }),
    )

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      return e instanceof ApiError && e.status === 400 && e.detail === 'Connection not found'
    })
  })

  it('throws ApiError with message fallback when detail is missing', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({ message: 'Something went wrong' }, { status: 500 }),
    )

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      return e instanceof ApiError && e.status === 500 && e.message === 'Something went wrong'
    })
  })

  it('throws ApiError using statusText when JSON parse fails', async () => {
    const badRes = new Response('not json at all', { status: 503 })
    vi.mocked(fetch).mockResolvedValue(badRes)

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      return e instanceof ApiError && e.status === 503
    })
  })

  // ─── Structured-detail (FastAPI HTTPException(detail={error,message})) ────

  it('extracts message and code from structured detail on 4xx', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockResponse(
        { detail: { error: 'email_in_use', message: 'Email address is already registered.' } },
        { status: 409, statusText: 'Conflict' },
      ),
    )

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      return (
        e instanceof ApiError &&
        e.status === 409 &&
        e.message === 'Email address is already registered.' &&
        e.code === 'email_in_use' &&
        typeof e.detail === 'object' &&
        !Array.isArray(e.detail) &&
        (e.detail as { error?: string }).error === 'email_in_use'
      )
    })
  })

  it('extracts message and code from structured detail on non-array 422', async () => {
    // admin_users.py and others use detail={error, message} for some 422 paths
    // (e.g. invalid_profile_id), distinct from Pydantic's array shape.
    vi.mocked(fetch).mockResolvedValue(
      mockResponse(
        { detail: { error: 'invalid_profile_id', message: 'Profile not found.' } },
        { status: 422, statusText: 'Unprocessable Entity' },
      ),
    )

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      return (
        e instanceof ApiError &&
        e.status === 422 &&
        e.message === 'Profile not found.' &&
        e.code === 'invalid_profile_id'
      )
    })
  })

  it('sets code from structured detail and falls back when message is missing', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockResponse(
        { detail: { error: 'rate_limited' } },
        { status: 429, statusText: 'Too Many Requests' },
      ),
    )

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      // No backend message → message keeps the explicit statusText fallback.
      // We pin this on the explicit statusText we passed, not on a runtime
      // default that varies by environment.
      return (
        e instanceof ApiError &&
        e.status === 429 &&
        e.code === 'rate_limited' &&
        e.message === 'Too Many Requests'
      )
    })
  })

  it('sets message from structured detail without code when error is missing', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockResponse(
        { detail: { message: 'Server is temporarily unavailable.' } },
        { status: 503, statusText: 'Service Unavailable' },
      ),
    )

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      return (
        e instanceof ApiError &&
        e.status === 503 &&
        e.message === 'Server is temporarily unavailable.' &&
        e.code === undefined
      )
    })
  })

  it('falls back to statusText when structured detail is empty', async () => {
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({ detail: {} }, { status: 418, statusText: "I'm a teapot" }),
    )

    await expect(apiFetch('/api/test')).rejects.toSatisfy((err: unknown) => {
      const e = err as ApiError
      return (
        e instanceof ApiError &&
        e.status === 418 &&
        e.message === "I'm a teapot" &&
        e.code === undefined
      )
    })
  })

  it('sets Content-Type: application/json when body is a string', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({}))
    await apiFetch('/api/test', { method: 'POST', body: '{"name":"x"}' })

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = init?.headers as Headers
    expect(headers.get('Content-Type')).toBe('application/json')
  })

  it('does not set Content-Type when body is FormData', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({}))
    const form = new FormData()
    await apiFetch('/api/test', { method: 'POST', body: form })

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = init?.headers as Headers
    expect(headers.get('Content-Type')).toBeNull()
  })

  it('does not set Content-Type when no body is present', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({}))
    await apiFetch('/api/test')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = init?.headers as Headers
    expect(headers.get('Content-Type')).toBeNull()
  })
})

describe('token storage helpers', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => localStorage.clear())

  it('storeToken writes to localStorage', () => {
    storeToken('my-token')
    expect(localStorage.getItem('auth_token')).toBe('my-token')
  })

  it('getStoredToken returns the stored token', () => {
    localStorage.setItem('auth_token', 'abc')
    expect(getStoredToken()).toBe('abc')
  })

  it('getStoredToken returns null when empty', () => {
    expect(getStoredToken()).toBeNull()
  })

  it('clearStoredToken removes the token', () => {
    localStorage.setItem('auth_token', 'abc')
    clearStoredToken()
    expect(localStorage.getItem('auth_token')).toBeNull()
  })
})

describe('apiFetch auth injection', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 200 })))
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('injects Authorization header when token is stored', async () => {
    storeToken('valid-token')
    await apiFetch('/api/test')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init?.headers as Headers).get('Authorization')).toBe('Bearer valid-token')
  })

  it('does not inject Authorization header when no token is stored', async () => {
    await apiFetch('/api/test')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init?.headers as Headers).get('Authorization')).toBeNull()
  })

  it('does not overwrite an explicit Authorization header', async () => {
    storeToken('stored-token')
    await apiFetch('/api/test', { headers: { Authorization: 'Bearer explicit-token' } })
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init?.headers as Headers).get('Authorization')).toBe('Bearer explicit-token')
  })
})

describe('apiFetch 401 handling', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 }),
    ))
    vi.stubGlobal('location', { href: '', pathname: '/' })
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('clears token and redirects to /login on 401 when token was present', async () => {
    storeToken('expired-token')
    await expect(apiFetch('/api/test')).rejects.toBeInstanceOf(ApiError)
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(window.location.href).toBe('/login')
  })

  it('does not redirect on 401 when no token was present', async () => {
    await expect(apiFetch('/api/test')).rejects.toBeInstanceOf(ApiError)
    expect(window.location.href).toBe('')
  })

  it('clears token but does not redirect when already on /login', async () => {
    storeToken('expired-token')
    vi.stubGlobal('location', { href: '', pathname: '/login' })
    await expect(apiFetch('/api/test')).rejects.toBeInstanceOf(ApiError)
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(window.location.href).toBe('')
  })
})

describe('convenience helpers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse({ ok: true })))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('apiGet calls fetch with GET', async () => {
    await apiGet('/api/test')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).method).toBe('GET')
  })

  it('apiPost calls fetch with POST and serialised body', async () => {
    await apiPost('/api/test', { name: 'hello' })
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).method).toBe('POST')
    expect((init as RequestInit).body).toBe('{"name":"hello"}')
  })

  it('apiPost with no body sends no body', async () => {
    await apiPost('/api/test')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).body).toBeUndefined()
  })

  it('apiPut calls fetch with PUT and serialised body', async () => {
    await apiPut('/api/test', { name: 'world' })
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).method).toBe('PUT')
    expect((init as RequestInit).body).toBe('{"name":"world"}')
  })

  it('apiDelete calls fetch with DELETE', async () => {
    await apiDelete('/api/test')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).method).toBe('DELETE')
  })
})
