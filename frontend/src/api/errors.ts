/**
 * Normalize FastAPI's `detail` field into a single reviewer/customer-facing string.
 * Handles a plain string, validation-error arrays, and unknown shapes.
 */
export function formatApiDetail(detail: unknown, fallback = 'Request failed'): string {
  if (typeof detail === 'string' && detail.trim()) {
    return detail.trim()
  }
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => formatValidationItem(item)).filter(Boolean)
    if (parts.length > 0) return parts.join('; ')
  }
  if (detail !== null && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (typeof record.msg === 'string' && record.msg.trim()) {
      return record.msg.trim()
    }
  }
  return fallback
}

function formatValidationItem(item: unknown): string {
  if (typeof item === 'string') return item.trim()
  if (item === null || typeof item !== 'object') return ''
  const record = item as Record<string, unknown>
  const msg = typeof record.msg === 'string' ? record.msg.trim() : ''
  if (!msg) return ''
  const loc = Array.isArray(record.loc)
    ? record.loc
        .filter((part): part is string | number => typeof part === 'string' || typeof part === 'number')
        .filter((part) => part !== 'body')
        .join('.')
    : ''
  return loc ? `${loc}: ${msg}` : msg
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly body: unknown

  constructor(status: number, detail: string, body: unknown = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}
