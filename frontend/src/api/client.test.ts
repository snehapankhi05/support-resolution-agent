import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './errors'

const getAccessToken = vi.fn<() => string | null>()
const clearAccessToken = vi.fn<() => void>()

vi.mock('./token', () => ({
  getAccessToken: () => getAccessToken(),
  clearAccessToken: () => clearAccessToken(),
}))

import { apiFetch } from './client'

function jsonResponse(
  status: number,
  body: unknown,
  statusText = 'OK',
): Response {
  const text =
    body === null || body === undefined ? '' : JSON.stringify(body)
  return new Response(text, {
    status,
    statusText,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('apiFetch', () => {
  beforeEach(() => {
    getAccessToken.mockReset()
    clearAccessToken.mockReset()
    getAccessToken.mockReturnValue(null)
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns parsed JSON on success and attaches Bearer when a token exists', async () => {
    getAccessToken.mockReturnValue('tok-abc')
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValue(jsonResponse(200, { id: '1', ok: true }))

    await expect(apiFetch<{ id: string }>('/orders')).resolves.toEqual({
      id: '1',
      ok: true,
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    const [, init] = fetchMock.mock.calls[0]!
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer tok-abc')
    expect(headers.get('Accept')).toBe('application/json')
    expect(clearAccessToken).not.toHaveBeenCalled()
  })

  it('does not set Authorization when auth is false', async () => {
    getAccessToken.mockReturnValue('tok-abc')
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValue(jsonResponse(200, { access_token: 'x' }))

    await apiFetch('/auth/token', {
      auth: false,
      method: 'POST',
      body: JSON.stringify({ email: 'a@b.c', password: 'p' }),
    })

    const [, init] = fetchMock.mock.calls[0]!
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBeNull()
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(getAccessToken).not.toHaveBeenCalled()
  })

  it('clears the token and throws ApiError on 401', async () => {
    getAccessToken.mockReturnValue('stale')
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(401, { detail: 'authentication required' }, 'Unauthorized'),
    )

    const err = await apiFetch('/me').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({
      status: 401,
      detail: 'authentication required',
    })
    expect(clearAccessToken).toHaveBeenCalledOnce()
  })

  it('throws ApiError on non-401 HTTP errors without clearing the token', async () => {
    getAccessToken.mockReturnValue('tok')
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(409, { detail: 'policy_conflict' }, 'Conflict'),
    )

    const err = await apiFetch('/proposals/p1/decision', {
      method: 'POST',
      body: JSON.stringify({ decision: 'approve' }),
    }).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, detail: 'policy_conflict' })
    expect(clearAccessToken).not.toHaveBeenCalled()
  })

  it('maps fetch rejection to ApiError with status 0', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('Failed to fetch'))

    const err = await apiFetch('/health').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 0, detail: 'Failed to fetch' })
    expect(clearAccessToken).not.toHaveBeenCalled()
  })

  it('returns null for an empty successful body', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('', { status: 200 }))
    await expect(apiFetch('/health')).resolves.toBeNull()
  })
})
