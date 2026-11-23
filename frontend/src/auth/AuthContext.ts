import { createContext } from 'react'

import type { LoginCredentials, MeResponse } from './types'

/** Session lifecycle for the AuthProvider. */
export type AuthStatus = 'loading' | 'unauthenticated' | 'authenticated' | 'error'

export type AuthContextValue = {
  user: MeResponse | null
  status: AuthStatus
  /** Set when status is error, or when the last login/restore failed with a message. */
  error: string | null
  login: (credentials: LoginCredentials) => Promise<void>
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
