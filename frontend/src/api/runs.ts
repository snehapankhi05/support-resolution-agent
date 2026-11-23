import { apiFetch } from './client'

/** Mirrors app.api.runs.RunCreate — ticket_id only. */
export type RunCreate = {
  ticket_id: string
}

/** Mirrors app.api.runs.RunResponse. */
export type Run = {
  run_id: string
  ticket_id: string
  thread_id: string
  status: string
  created: boolean
}

/**
 * Start (or re-enqueue) the workflow for a ticket this customer owns.
 * 201 when a new run is created; 200 when an active run already exists.
 */
export function createRun(body: RunCreate): Promise<Run> {
  return apiFetch<Run>('/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
