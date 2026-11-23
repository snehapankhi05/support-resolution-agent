import { Navigate, Outlet } from 'react-router-dom'

import { getAccessToken } from '../api/token'
import { useAuth } from '../auth'
import { homePathFor } from '../auth/roles'
import { AuthLoading } from './AuthLoading'

/**
 * /login gate. Full-page wait only while restoring a stored token.
 * Credential submit with no token yet keeps the login form mounted (busy UI).
 * Context error is preserved for the form when status is error.
 */
export function GuestOnly() {
  const { user, status } = useAuth()

  if (status === 'loading' && getAccessToken() !== null) {
    return <AuthLoading />
  }

  if (status === 'authenticated' && user !== null) {
    return <Navigate to={homePathFor(user)} replace />
  }

  return <Outlet />
}
