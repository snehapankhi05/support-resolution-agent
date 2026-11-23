import { describe, expect, it } from 'vitest'

import { ApiError, formatApiDetail } from './errors'

describe('formatApiDetail', () => {
  it('returns a trimmed string detail', () => {
    expect(formatApiDetail('  hello  ')).toBe('hello')
  })

  it('joins FastAPI validation-error arrays with loc (skipping body)', () => {
    expect(
      formatApiDetail([
        { loc: ['body', 'email'], msg: 'field required', type: 'missing' },
        { loc: ['body', 'password'], msg: 'ensure this value has at least 1 characters' },
      ]),
    ).toBe(
      'email: field required; password: ensure this value has at least 1 characters',
    )
  })

  it('uses msg from a plain object detail', () => {
    expect(formatApiDetail({ msg: '  not allowed  ' })).toBe('not allowed')
  })

  it('falls back for empty or unknown shapes', () => {
    expect(formatApiDetail(null)).toBe('Request failed')
    expect(formatApiDetail('   ')).toBe('Request failed')
    expect(formatApiDetail(42, 'custom')).toBe('custom')
    expect(formatApiDetail([])).toBe('Request failed')
  })
})

describe('ApiError', () => {
  it('stores status, detail, body and Error name/message', () => {
    const body = { detail: 'nope' }
    const err = new ApiError(409, 'conflict', body)
    expect(err).toBeInstanceOf(Error)
    expect(err.name).toBe('ApiError')
    expect(err.message).toBe('conflict')
    expect(err.status).toBe(409)
    expect(err.detail).toBe('conflict')
    expect(err.body).toBe(body)
  })

  it('defaults body to null', () => {
    expect(new ApiError(500, 'boom').body).toBeNull()
  })
})
