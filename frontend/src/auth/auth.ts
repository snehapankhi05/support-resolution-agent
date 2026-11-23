import { apiFetch } from '../api'
import { clearAccessToken, getAccessToken, setAccessToken } from '../api/token'

import type { LoginCredentials, MeResponse, TokenResponse } from './types'

/**
 * Exchange email/password for a bearer token and store it in sessionStorage.
 * Does not read JWT claims for role — call loadMe() for identity.
 */
export async function login(credentials: LoginCredentials): Promise<TokenResponse> {
  const body = {
    email: credentials.email.trim(),
    password: credentials.password,
  }
  const token = await apiFetch<TokenResponse>('/auth/token', {
    method: 'POST',
    auth: false,
    body: JSON.stringify(body),
  })
  if (typeof token.access_token !== 'string' || !token.access_token.trim()) {
    throw new Error('login response missing access_token')
  }
  setAccessToken(token.access_token)
  return token
}

/**
 * Load the account named by the stored token. Actor and role come from the API
 * (database), never from decoding the JWT.
 */
export async function loadMe(): Promise<MeResponse> {
  if (getAccessToken() === null) {
    throw new Error('authentication required')
  }
  return apiFetch<MeResponse>('/me')
}

/** Drop the local session. There is no logout HTTP endpoint. */
export function logout(): void {
  clearAccessToken()
}
