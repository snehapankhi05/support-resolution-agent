import { ApiError, formatApiDetail } from './errors'
import { clearAccessToken, getAccessToken } from './token'

export type ApiFetchOptions = RequestInit & {
  /** When false, never attach Authorization. Default: attach token if present. */
  auth?: boolean
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

function detailFromBody(body: unknown, fallback: string): string {
  if (body !== null && typeof body === 'object' && 'detail' in body) {
    return formatApiDetail((body as { detail: unknown }).detail, fallback)
  }
  if (typeof body === 'string' && body.trim()) {
    return body.trim()
  }
  return fallback
}

/**
 * Same-origin fetch through the Vite proxy. Attaches the bearer token when present
 * (unless auth: false). On HTTP 401, clears the stored token then throws ApiError.
 */
export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { auth = true, headers: initHeaders, ...rest } = options
  const headers = new Headers(initHeaders)

  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }

  if (
    rest.body !== undefined &&
    rest.body !== null &&
    !(rest.body instanceof FormData) &&
    !headers.has('Content-Type')
  ) {
    headers.set('Content-Type', 'application/json')
  }

  if (auth) {
    const token = getAccessToken()
    if (token !== null) {
      headers.set('Authorization', `Bearer ${token}`)
    }
  }

  let response: Response
  try {
    response = await fetch(path, { ...rest, headers })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Network request failed'
    throw new ApiError(0, message)
  }

  const body = await readBody(response)

  if (response.status === 401) {
    clearAccessToken()
    throw new ApiError(
      401,
      detailFromBody(body, 'authentication required'),
      body,
    )
  }

  if (!response.ok) {
    throw new ApiError(
      response.status,
      detailFromBody(body, `HTTP ${response.status}`),
      body,
    )
  }

  return body as T
}
