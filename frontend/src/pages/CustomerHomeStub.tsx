import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../api'
import { listOrders } from '../api/orders'
import { listTickets, type Ticket } from '../api/tickets'
import { useAuth } from '../auth'

/** Blueprint open-case set — count by ticket.status only, not message. */
const OPEN_CASE_STATUSES = new Set(['open', 'awaiting_customer', 'awaiting_approval'])

function countOpenCases(tickets: Ticket[]): number {
  return tickets.filter((row) => OPEN_CASE_STATUSES.has(row.status)).length
}

/**
 * Customer home — honest tiles from GET /orders and GET /tickets only.
 */
export function CustomerHomeStub() {
  const { user } = useAuth()
  const [orderCount, setOrderCount] = useState<number | null>(null)
  const [openCaseCount, setOpenCaseCount] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)
    Promise.all([listOrders(), listTickets()])
      .then(([orders, tickets]) => {
        if (cancelled) return
        setOrderCount(orders.length)
        setOpenCaseCount(countOpenCases(tickets))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setOrderCount(null)
        setOpenCaseCount(null)
        setError(err instanceof ApiError ? err.detail : 'Could not load your account summary')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const loading = orderCount === null && openCaseCount === null && error === null

  return (
    <div className="flex flex-col gap-8">
      <header>
        <h1 className="font-brand text-2xl font-semibold text-ink">
          Welcome{user?.name ? `, ${user.name}` : ''}
        </h1>
        <p className="mt-1 text-sm text-text-muted">
          Counts below come from your orders and tickets on this account.
        </p>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger"
        >
          {error}
        </p>
      ) : null}

      {loading ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading your summary…
        </p>
      ) : null}

      {orderCount !== null && openCaseCount !== null ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <HomeTile
            label="Orders"
            value={String(orderCount)}
            hint={orderCount === 0 ? 'No orders yet' : 'On this account'}
            to="/orders"
            cta="View orders"
          />
          <HomeTile
            label="Open cases"
            value={String(openCaseCount)}
            hint="Statuses: open, awaiting customer, or awaiting approval"
            to="/cases"
            cta="View cases"
          />
        </div>
      ) : null}
    </div>
  )
}

function HomeTile({
  label,
  value,
  hint,
  to,
  cta,
}: {
  label: string
  value: string
  hint: string
  to: string
  cta: string
}) {
  return (
    <div className="flex flex-col rounded-lg border border-line bg-paper px-4 py-4">
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">{label}</p>
      <p className="mt-2 font-brand text-3xl font-semibold tabular-nums text-ink">{value}</p>
      <p className="mt-1 text-xs text-text-muted">{hint}</p>
      <span className="mt-3 block h-0.5 w-8 bg-accent" aria-hidden="true" />
      <Link
        to={to}
        className="mt-4 text-sm font-medium text-accent hover:text-accent-hover"
      >
        {cta}
      </Link>
    </div>
  )
}
