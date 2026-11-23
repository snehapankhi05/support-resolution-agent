import type { MeResponse } from './types'

/** Matches require_customer: token subject is a customer row. */
export function isCustomer(user: MeResponse): boolean {
  return user.actor === 'customer'
}

/** Matches require_reviewer: staff with role reviewer or admin. */
export function isReviewer(user: MeResponse): boolean {
  return user.actor === 'staff' && (user.role === 'reviewer' || user.role === 'admin')
}

/** Role home path from /me. Unknown combinations go to login. */
export function homePathFor(user: MeResponse | null): string {
  if (user === null) return '/login'
  if (isCustomer(user)) return '/'
  if (isReviewer(user)) return '/review'
  return '/login'
}
