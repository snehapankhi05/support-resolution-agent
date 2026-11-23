import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../api'
import { truncateId } from '../api/orders'
import { categoryLabel, listTickets, type Ticket } from '../api/tickets'
import { InlineAlert } from '../components/InlineAlert'
import { StatusBadge } from '../components/StatusBadge'
import { productNamesText } from '../lib/productLabel'

/** Same null-message copy as CasePage — never invent a payment outcome. */
function displayMessage(ticket: Ticket): string {
  if (ticket.message) return ticket.message
  if (ticket.refund_status === 'reserved') {
    return 'Payment processing — not confirmed yet.'
  }
  if (ticket.status === 'open') {
    return 'Case opened — waiting for the workflow to update this message.'
  }
  return 'Waiting for a verified status message.'
}

/**
 * Customer cases list from GET /tickets. Created-at is omitted — TicketResponse
 * does not include that field.
 */
export function CasesPage() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)
    listTickets()
      .then((rows) => {
        if (cancelled) return
        // API returns oldest-first; show newest first for browsing.
        setTickets([...rows].reverse())
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setTickets(null)
        setError(err instanceof ApiError ? err.detail : 'Could not load your cases')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-brand text-2xl font-semibold text-ink">Cases</h1>
          <p className="mt-1 text-sm text-text-muted">
            Support cases on this account. Status messages are verified server-side.
          </p>
        </div>
        <Link
          to="/cases/new"
          className="min-h-10 rounded-md bg-accent px-3 py-2 text-sm font-semibold text-text-on-ink hover:bg-accent-hover"
        >
          New case
        </Link>
      </header>

      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}

      {tickets === null && error === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading cases…
        </p>
      ) : null}

      {tickets !== null && tickets.length === 0 ? (
        <p className="rounded-md border border-line bg-paper px-4 py-6 text-sm text-text-muted">
          No cases yet.{' '}
          <Link to="/cases/new" className="font-medium text-accent hover:text-accent-hover">
            Open a case
          </Link>
        </p>
      ) : null}

      {tickets !== null && tickets.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {tickets.map((ticket) => {
            const escalated = ticket.status === 'escalated'
            const product = productNamesText(ticket.product_names)
            return (
              <li key={ticket.id}>
                <Link
                  to={`/cases/${ticket.id}`}
                  className={`block rounded-lg border bg-paper px-4 py-4 transition-colors hover:border-accent sm:px-5 ${
                    escalated
                      ? 'border-warn/40 bg-warn-soft/40'
                      : 'border-line'
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-ink">
                        {categoryLabel(ticket.category)}
                      </p>
                      {product ? (
                        <p className="mt-0.5 text-sm text-text">{product}</p>
                      ) : null}
                      <p className="mt-0.5 font-mono text-xs tabular-nums text-text-muted">
                        {truncateId(ticket.id)}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge label={`Ticket: ${ticket.status}`} />
                      {ticket.refund_status ? (
                        <StatusBadge label={`Refund: ${ticket.refund_status}`} />
                      ) : null}
                    </div>
                  </div>

                  {escalated ? (
                    <p className="mt-3 text-xs font-medium uppercase tracking-wide text-warn">
                      Specialist attention pending
                    </p>
                  ) : null}

                  <p
                    className={`mt-2 text-sm leading-snug ${
                      escalated ? 'text-ink' : 'text-text'
                    }`}
                  >
                    {displayMessage(ticket)}
                  </p>

                  <p className="mt-3 text-xs font-medium text-accent">View case →</p>
                </Link>
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}
