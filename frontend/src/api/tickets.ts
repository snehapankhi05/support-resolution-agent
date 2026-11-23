import { apiFetch } from './client'

/** Mirrors app.schemas.customer.TicketCategory. */
export type TicketCategory = 'order_status' | 'refund_request' | 'damaged_item'

export const TICKET_CATEGORIES: readonly TicketCategory[] = [
  'order_status',
  'refund_request',
  'damaged_item',
] as const

/** Mirrors app.schemas.customer.TicketCreate — no customer_id. */
export type TicketCreate = {
  order_id: string
  category: TicketCategory
  summary: string
}

/** Mirrors app.schemas.customer.TicketResponse. */
export type Ticket = {
  id: string
  order_id: string | null
  category: string
  status: string
  summary: string
  refund_status: string | null
  message: string | null
  /** Catalog names for the linked order. Display only. */
  product_names?: string[]
}

export function createTicket(body: TicketCreate): Promise<Ticket> {
  return apiFetch<Ticket>('/tickets', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function listTickets(): Promise<Ticket[]> {
  return apiFetch<Ticket[]>('/tickets')
}

export function getTicket(ticketId: string): Promise<Ticket> {
  return apiFetch<Ticket>(`/tickets/${ticketId}`)
}

export function categoryLabel(category: string): string {
  switch (category) {
    case 'order_status':
      return 'Order status'
    case 'refund_request':
      return 'Refund request'
    case 'damaged_item':
      return 'Damaged item'
    default:
      return category
  }
}
