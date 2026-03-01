import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ApiError, apiFetch, apiGet, apiPost, apiPut, apiDelete } from '../../api/client'

// Helper to create a mock Response
function mockResponse(
  body: unknown,
  { status = 200, headers = {} }: { status?: number; headers?: Record<string, string> } = {},
): Response {
  const json = JSON.stringify(body)
  return new Response(json, {
    status,
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
