import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { ApiError } from '../api'
import { getAccessToken } from '../api/token'
import {
  loadMe as fetchMe,
  login as requestLogin,
  logout as clearSession,
} from './auth'
import { AuthContext, type AuthStatus } from './AuthContext'
import type { LoginCredentials, MeResponse } from './types'

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail
  if (error instanceof Error && error.message.trim()) return error.message
  return 'authentication failed'
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function restore() {
      if (getAccessToken() === null) {
        if (!cancelled) {
          setUser(null)
          setError(null)
          setStatus('unauthenticated')
        }
        return
      }

      try {
        const me = await fetchMe()
        if (cancelled) return
        setUser(me)
        setError(null)
        setStatus('authenticated')
      } catch (err) {
        clearSession()
        if (cancelled) return
        setUser(null)
        setError(errorMessage(err))
        setStatus('error')
      }
    }

    void restore()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (credentials: LoginCredentials) => {
    setStatus('loading')
    setError(null)
    try {
      await requestLogin(credentials)
      const me = await fetchMe()
      setUser(me)
      setError(null)
      setStatus('authenticated')
    } catch (err) {
      clearSession()
      setUser(null)
      const message = errorMessage(err)
      setError(message)
      setStatus('error')
      throw err instanceof ApiError || err instanceof Error
        ? err
        : new Error(message)
    }
  }, [])

  const logout = useCallback(() => {
    clearSession()
    setUser(null)
    setError(null)
    setStatus('unauthenticated')
  }, [])

  const value = useMemo(
    () => ({ user, status, error, login, logout }),
    [user, status, error, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
