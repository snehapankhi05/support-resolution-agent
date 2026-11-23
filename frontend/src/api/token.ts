/** sessionStorage key for the bearer token (approved P1 storage choice). */
export const ACCESS_TOKEN_KEY = 'sr_access_token'

export function getAccessToken(): string | null {
  const value = sessionStorage.getItem(ACCESS_TOKEN_KEY)
  if (value === null) return null
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export function setAccessToken(token: string): void {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, token)
}

/** Clears the stored token. Used on logout and on HTTP 401 from the API. */
export function clearAccessToken(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_KEY)
}
