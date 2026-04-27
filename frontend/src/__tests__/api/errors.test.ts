import { describe, it, expect } from 'vitest'
import { ApiError } from '../../api/client'
import { formatApiError, formatApiErrorStrict, formatApiErrors } from '../../api/errors'

describe('formatApiError', () => {
  it('returns ApiError.message verbatim', () => {
    const err = new ApiError({
      status: 409,
      message: 'Email address is already registered.',
      code: 'email_in_use',
    })
    expect(formatApiError(err, 'fallback')).toBe('Email address is already registered.')
  })

  it('returns plain Error.message verbatim', () => {
    const err = new Error('Network unreachable')
    expect(formatApiError(err, 'fallback')).toBe('Network unreachable')
  })

  it('returns the fallback for non-Error values', () => {
    expect(formatApiError(undefined, 'Failed to load')).toBe('Failed to load')
    expect(formatApiError(null, 'Failed to load')).toBe('Failed to load')
    expect(formatApiError('a thrown string', 'Failed to load')).toBe('Failed to load')
    expect(formatApiError({ random: 'object' }, 'Failed to load')).toBe('Failed to load')
  })

  it('returns the fallback when ApiError.message is empty', () => {
    const err = new ApiError({ status: 500, message: '' })
    expect(formatApiError(err, 'Server unavailable')).toBe('Server unavailable')
  })

  it('returns the fallback when Error.message is empty', () => {
    const err = new Error('')
    expect(formatApiError(err, 'Something went wrong')).toBe('Something went wrong')
  })
})

describe('formatApiErrorStrict', () => {
  it('returns ApiError.message verbatim', () => {
    const err = new ApiError({
      status: 404,
      message: 'Job not found.',
      code: 'job_not_found',
    })
    expect(formatApiErrorStrict(err, 'Failed to load job')).toBe('Job not found.')
  })

  it('returns the fallback for plain Error (does NOT leak err.message)', () => {
    // Strict mode is the whole point: don't show "Network failure" /
    // "Failed to fetch" as user-facing copy.
    const err = new Error('Network failure')
    expect(formatApiErrorStrict(err, 'Failed to load job')).toBe('Failed to load job')
  })

  it('returns the fallback for non-Error values', () => {
    expect(formatApiErrorStrict(undefined, 'Failed to load')).toBe('Failed to load')
    expect(formatApiErrorStrict(null, 'Failed to load')).toBe('Failed to load')
    expect(formatApiErrorStrict('thrown', 'Failed to load')).toBe('Failed to load')
  })

  it('returns the fallback when ApiError.message is empty', () => {
    const err = new ApiError({ status: 500, message: '' })
    expect(formatApiErrorStrict(err, 'Server unavailable')).toBe('Server unavailable')
  })
})

describe('formatApiErrors', () => {
  it('expands 422 array detail into per-field messages', () => {
    const err = new ApiError({
      status: 422,
      message: 'Validation error',
      detail: [
        { type: 'missing', loc: ['body', 'name'], msg: 'Field required' },
        { type: 'string_type', loc: ['body', 'client_id'], msg: 'Must be a string' },
      ],
    })
    expect(formatApiErrors(err, 'fallback')).toEqual([
      'name — Field required',
      'client_id — Must be a string',
    ])
  })

  it('returns single-element array for non-422 ApiError', () => {
    const err = new ApiError({
      status: 409,
      message: 'Email in use',
      code: 'email_in_use',
    })
    expect(formatApiErrors(err, 'fallback')).toEqual(['Email in use'])
  })

  it('returns single-element array for plain Error', () => {
    const err = new Error('Boom')
    expect(formatApiErrors(err, 'fallback')).toEqual(['Boom'])
  })

  it('returns single-element fallback array for non-Error values', () => {
    expect(formatApiErrors(null, 'fallback')).toEqual(['fallback'])
    expect(formatApiErrors(undefined, 'fallback')).toEqual(['fallback'])
    expect(formatApiErrors('thrown string', 'fallback')).toEqual(['fallback'])
  })

  it('returns single-element array for ApiError with structured detail (not validation array)', () => {
    const err = new ApiError({
      status: 400,
      message: 'Bad request',
      detail: { error: 'bad_input', message: 'Bad request' },
    })
    expect(formatApiErrors(err, 'fallback')).toEqual(['Bad request'])
  })
})
