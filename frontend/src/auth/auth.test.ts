import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetch = vi.fn()
const getAccessToken = vi.fn<() => string | null>()
const setAccessToken = vi.fn<(token: string) => void>()
const clearAccessToken = vi.fn<() => void>()

vi.mock('../api', () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}))

vi.mock('../api/token', () => ({
  getAccessToken: () => getAccessToken(),
  setAccessToken: (token: string) => setAccessToken(token),
  clearAccessToken: () => clearAccessToken(),
}))

import { loadMe, login, logout } from './auth'

describe('login', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    getAccessToken.mockReset()
    setAccessToken.mockReset()
    clearAccessToken.mockReset()
  })

  it('posts credentials without auth, stores access_token, returns the body', async () => {
    const body = {
      access_token: '  jwt-token  ',
      token_type: 'bearer',
      expires_in: 3600,
    }
    apiFetch.mockResolvedValue(body)

    await expect(
      login({ email: '  asha@example.com  ', password: 'secret' }),
    ).resolves.toEqual(body)

    expect(apiFetch).toHaveBeenCalledWith('/auth/token', {
      method: 'POST',
      auth: false,
      body: JSON.stringify({
        email: 'asha@example.com',
        password: 'secret',
      }),
    })
    expect(setAccessToken).toHaveBeenCalledWith('  jwt-token  ')
  })

  it('rejects when access_token is missing or blank', async () => {
    apiFetch.mockResolvedValue({
      access_token: '   ',
      token_type: 'bearer',
      expires_in: 1,
    })

    await expect(
      login({ email: 'a@b.c', password: 'x' }),
    ).rejects.toThrow('login response missing access_token')
    expect(setAccessToken).not.toHaveBeenCalled()
  })
})

describe('loadMe', () => {
  beforeEach(() => {
    apiFetch.mockReset()
    getAccessToken.mockReset()
  })

  it('throws when no token is stored', async () => {
    getAccessToken.mockReturnValue(null)
    await expect(loadMe()).rejects.toThrow('authentication required')
    expect(apiFetch).not.toHaveBeenCalled()
  })

  it('GETs /me when a token exists', async () => {
    getAccessToken.mockReturnValue('tok')
    const me = {
      actor: 'customer',
      id: 'c1',
      email: 'asha@example.com',
      name: 'Asha',
      role: 'customer',
    }
    apiFetch.mockResolvedValue(me)

    await expect(loadMe()).resolves.toEqual(me)
    expect(apiFetch).toHaveBeenCalledWith('/me')
  })
})

describe('logout', () => {
  it('clears the stored token', () => {
    clearAccessToken.mockClear()
    logout()
    expect(clearAccessToken).toHaveBeenCalledOnce()
  })
})
