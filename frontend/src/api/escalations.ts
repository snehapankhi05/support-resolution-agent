import { apiFetch } from './client'

/** Mirrors app.api.escalations.EscalationView. No amount and no complaint. */
export type Escalation = {
  ticket_id: string
  category: string
  product_names: string[]
  resolution_message: string | null
}

export function listEscalations(): Promise<Escalation[]> {
  return apiFetch<Escalation[]>('/escalations')
}

export function getEscalation(ticketId: string): Promise<Escalation> {
  return apiFetch<Escalation>(`/escalations/${ticketId}`)
}
