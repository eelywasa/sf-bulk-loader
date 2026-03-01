import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api, ApiError } from '../../api/client'

// ─── Mock fetch ────────────────────────────────────────────────────────────────

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

function makeOkResponse(body: unknown, status = 200) {
  return {
    ok: true,
    status,
    statusText: 'OK',
    json: () => Promise.resolve(body),
  }
}

function makeErrorResponse(status: number, body: unknown, statusText = 'Error') {
  return {
    ok: false,
    status,
    statusText,
    json: () => Promise.resolve(body),
  }
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

describe('api.get', () => {
  beforeEach(() => mockFetch.mockClear())

  it('makes a GET request with JSON content-type header', async () => {
    mockFetch.mockResolvedValueOnce(makeOkResponse({ id: '1' }))
    const result = await api.get('/api/test')
    expect(result).toEqual({ id: '1' })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/test',
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    )
  })

  it('does not set a method (browser defaults to GET)', async () => {
    mockFetch.mockResolvedValueOnce(makeOkResponse({}))
    await api.get('/api/test')
    const callArgs = mockFetch.mock.calls[0][1] as RequestInit
    expect(callArgs.method).toBeUndefined()
  })
})

describe('api.post', () => {
  beforeEach(() => mockFetch.mockClear())

  it('makes a POST request with serialized body', async () => {
    mockFetch.mockResolvedValueOnce(makeOkResponse({ id: '2' }))
    await api.post('/api/test', { name: 'foo' })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/test',
      expect.objectContaining({
        method: 'POST',
        body: '{"name":"foo"}',
      }),
    )
  })

  it('makes a POST request without body when none provided', async () => {
    mockFetch.mockResolvedValueOnce(makeOkResponse({}))
    await api.post('/api/trigger')
    const callArgs = mockFetch.mock.calls[0][1] as RequestInit
    expect(callArgs.body).toBeUndefined()
  })
})

describe('api.put', () => {
  beforeEach(() => mockFetch.mockClear())

  it('makes a PUT request with serialized body', async () => {
    mockFetch.mockResolvedValueOnce(makeOkResponse({}))
    await api.put('/api/test/1', { name: 'bar' })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/test/1',
      expect.objectContaining({
        method: 'PUT',
        body: '{"name":"bar"}',
      }),
    )
  })
})

describe('api.delete', () => {
  beforeEach(() => mockFetch.mockClear())

  it('makes a DELETE request', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      statusText: 'No Content',
      json: () => Promise.resolve(undefined),
    })
    await api.delete('/api/test/1')
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/test/1',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('returns undefined for 204 No Content', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      statusText: 'No Content',
      json: () => Promise.resolve(undefined),
    })
    const result = await api.delete('/api/test/1')
    expect(result).toBeUndefined()
  })
})

describe('ApiError', () => {
  beforeEach(() => mockFetch.mockClear())

  it('throws ApiError on non-OK response', async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(404, { detail: 'Not found' }, 'Not Found'),
    )
    await expect(api.get('/api/missing')).rejects.toBeInstanceOf(ApiError)
  })

  it('maps string detail to ApiError message', async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(400, { detail: 'Invalid data' }, 'Bad Request'),
    )
    let caught: ApiError | undefined
    try {
      await api.get('/api/test')
    } catch (e) {
      caught = e as ApiError
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect(caught!.status).toBe(400)
    expect(caught!.message).toBe('Invalid data')
    expect(caught!.detail).toBe('Invalid data')
  })

  it('maps 422 array detail to ApiError.detail array', async () => {
    const detail = [
      { type: 'missing', loc: ['body', 'name'], msg: 'Field required', input: null },
    ]
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(422, { detail }, 'Unprocessable Entity'),
    )
    let caught: ApiError | undefined
    try {
      await api.get('/api/test')
    } catch (e) {
      caught = e as ApiError
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect(caught!.status).toBe(422)
    expect(caught!.message).toBe('Validation error')
    expect(caught!.detail).toEqual(detail)
  })

  it('uses statusText as message when JSON body has no detail field', async () => {
    mockFetch.mockResolvedValueOnce(
      makeErrorResponse(503, { error: 'service down' }, 'Service Unavailable'),
    )
    let caught: ApiError | undefined
    try {
      await api.get('/api/test')
    } catch (e) {
      caught = e as ApiError
    }
    expect(caught!.status).toBe(503)
    expect(caught!.message).toBe('Service Unavailable')
  })

  it('uses statusText when JSON parse fails', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: () => Promise.reject(new SyntaxError('bad json')),
    })
    let caught: ApiError | undefined
    try {
      await api.get('/api/test')
    } catch (e) {
      caught = e as ApiError
    }
    expect(caught!.status).toBe(500)
    expect(caught!.message).toBe('Internal Server Error')
  })

  it('exposes name as ApiError', async () => {
    mockFetch.mockResolvedValueOnce(makeErrorResponse(400, { detail: 'bad' }))
    let caught: ApiError | undefined
    try {
      await api.get('/api/test')
    } catch (e) {
      caught = e as ApiError
    }
    expect(caught!.name).toBe('ApiError')
  })
})
