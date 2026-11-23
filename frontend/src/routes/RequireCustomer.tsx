import { Navigate, Outlet } from 'react-router-dom'

import { useAuth } from '../auth'
import { homePathFor, isCustomer } from '../auth/roles'
import { AuthLoading } from './AuthLoading'

/**
 * Customer portal gate. Loading waits; error/unauthenticated → /login;
 * wrong portal → reviewer (or login) home.
 */
export function RequireCustomer() {
  const { user, status } = useAuth()

  if (status === 'loading') {
    return <AuthLoading />
  }

  if (status !== 'authenticated' || user === null) {
    return <Navigate to="/login" replace />
  }

  if (!isCustomer(user)) {
    return <Navigate to={homePathFor(user)} replace />
  }

  return <Outlet />
}
