import { Navigate } from 'react-router-dom'

import { useAuth } from '../auth'
import { homePathFor } from '../auth/roles'
import { AuthLoading } from './AuthLoading'

/** Unknown paths: wait, then send to role home or /login. */
export function SessionRedirect() {
  const { user, status } = useAuth()

  if (status === 'loading') {
    return <AuthLoading />
  }

  if (status === 'authenticated' && user !== null) {
    return <Navigate to={homePathFor(user)} replace />
  }

  return <Navigate to="/login" replace />
}
