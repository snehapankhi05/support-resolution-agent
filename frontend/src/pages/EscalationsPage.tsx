import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../api'
import { listEscalations, type Escalation } from '../api/escalations'
import { categoryLabel } from '../api/tickets'
import { productNamesText } from '../lib/productLabel'

/**
 * Escalated tickets only, from GET /escalations. Not the refund queue.
 */
export function EscalationsPage() {
  const [rows, setRows] = useState<Escalation[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async (manual: boolean) => {
    if (manual) setRefreshing(true)
    try {
      const list = await listEscalations()
      setRows(list)
      setError(null)
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.detail : 'Could not load escalations')
      if (!manual) setRows(null)
    } finally {
      if (manual) setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    listEscalations()
      .then((list) => {
        if (!cancelled) setRows(list)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setRows(null)
        setError(err instanceof ApiError ? err.detail : 'Could not load escalations')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-brand text-2xl font-semibold text-ink">Escalations</h1>
          <p className="mt-1 text-sm text-text-muted">
            Escalated cases waiting for a specialist. This list cannot approve a refund.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={refreshing}
          aria-busy={refreshing}
          className="min-h-10 rounded-md border border-line bg-paper px-3 py-2 text-sm font-medium text-text hover:border-accent disabled:opacity-60"
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger"
        >
          {error}{' '}
          <button type="button" className="font-medium underline" onClick={() => void load(true)}>
            Retry
          </button>
        </p>
      ) : null}

      {rows === null && error === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading escalations…
        </p>
      ) : null}

      {rows !== null && rows.length === 0 ? (
        <p className="rounded-md border border-line bg-fog/60 px-4 py-8 text-center text-sm text-text-muted">
          No escalated cases.
        </p>
      ) : null}

      {rows !== null && rows.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {rows.map((row) => {
            const product = productNamesText(row.product_names)
            return (
              <li key={row.ticket_id}>
                <Link
                  to={`/review/escalations/${row.ticket_id}`}
                  className="block rounded-lg border border-line px-4 py-3 text-sm hover:border-accent"
                >
                  <p className="font-medium text-text">{product ?? '—'}</p>
                  <p className="mt-1 text-text-muted">{categoryLabel(row.category)}</p>
                  <p className="mt-2 text-text">
                    {row.resolution_message ?? 'No stored message.'}
                  </p>
                </Link>
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}
