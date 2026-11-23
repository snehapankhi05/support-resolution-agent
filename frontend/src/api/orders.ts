import { apiFetch } from './client'

/** Mirrors app.schemas.customer.OrderResponse. */
export type Order = {
  id: string
  status: string
  currency: string
  paid_amount_minor: number
  refund_committed_minor: number
  delivered_at: string | null
  /** Catalog names. Display only. */
  product_names?: string[]
}

export function listOrders(): Promise<Order[]> {
  return apiFetch<Order[]>('/orders')
}

export function getOrder(orderId: string): Promise<Order> {
  return apiFetch<Order>(`/orders/${orderId}`)
}

export function truncateId(id: string, keep = 8): string {
  if (id.length <= keep * 2 + 1) return id
  return `${id.slice(0, keep)}…${id.slice(-keep)}`
}
