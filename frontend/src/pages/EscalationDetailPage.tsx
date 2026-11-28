import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api'
import { getEscalation, type Escalation } from '../api/escalations'
import { categoryLabel } from '../api/tickets'
import { productNamesText } from '../lib/productLabel'

/**
 * One escalated ticket from GET /escalations/{id}. No decision and no amount.
 */
export function EscalationDetailPage() {
  const { ticketId } = useParams()
  const [row, setRow] = useState<Escalation | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!ticketId) return
    let cancelled = false
    setRow(null)
    setError(null)
    getEscalation(ticketId)
      .then((escalation) => {
        if (!cancelled) setRow(escalation)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setRow(null)
        setError(err instanceof ApiError ? err.detail : 'Could not load this case')
      })
    return () => {
      cancelled = true
    }
  }, [ticketId])

  const product = row ? productNamesText(row.product_names) : null

  return (
    <div className="flex flex-col gap-6">
      <p>
        <Link to="/review/escalations" className="text-sm font-medium text-accent hover:underline">
          Back to escalations
        </Link>
      </p>

      <header>
        <h1 className="font-brand text-2xl font-semibold text-ink">{product ?? 'Escalated case'}</h1>
        <p className="mt-1 text-sm text-text-muted">Read only. This case is not a refund proposal.</p>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger"
        >
          {error}
        </p>
      ) : null}

      {row === null && error === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading case…
        </p>
      ) : null}

      {row ? (
        <dl className="grid gap-4 text-sm">
          <div>
            <dt className="text-text-muted">Category</dt>
            <dd className="mt-1 text-text">{categoryLabel(row.category)}</dd>
          </div>
          <div>
            <dt className="text-text-muted">Product</dt>
            <dd className="mt-1 text-text">{product ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-text-muted">Stored message</dt>
            <dd className="mt-1 text-text">{row.resolution_message ?? 'No stored message.'}</dd>
          </div>
        </dl>
      ) : null}
    </div>
  )
}
