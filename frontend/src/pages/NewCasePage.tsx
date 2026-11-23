import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError } from '../api'
import { listOrders, truncateId, type Order } from '../api/orders'
import { createRun } from '../api/runs'
import {
  TICKET_CATEGORIES,
  categoryLabel,
  createTicket,
  type TicketCategory,
} from '../api/tickets'
import { formatOrderMoney } from '../lib/money'
import { productNamesText } from '../lib/productLabel'

const SUMMARY_MAX = 2000

function isTicketCategory(value: string): value is TicketCategory {
  return (TICKET_CATEGORIES as readonly string[]).includes(value)
}

function validateForm(
  orderId: string,
  category: string,
  summary: string,
): string | null {
  if (!orderId) return 'Select an order'
  if (!isTicketCategory(category)) return 'Select a category'
  const trimmed = summary.trim()
  if (!trimmed) return 'Describe the issue'
  if (trimmed.length > SUMMARY_MAX) {
    return `Summary must be at most ${SUMMARY_MAX} characters`
  }
  return null
}

/**
 * Open a support case: POST /tickets then POST /runs, then go to /cases/:id.
 * On run failure after ticket create, retry only POST /runs.
 */
export function NewCasePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const orderIdParam = searchParams.get('orderId') ?? ''

  const orderIdField = useId()
  const categoryField = useId()
  const summaryField = useId()
  const errorId = useId()

  const [orders, setOrders] = useState<Order[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [orderId, setOrderId] = useState('')
  const [category, setCategory] = useState<TicketCategory | ''>('')
  const [summary, setSummary] = useState('')

  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  /** Set after ticket succeeds; enables Retry start without a second ticket. */
  const [pendingTicketId, setPendingTicketId] = useState<string | null>(null)
  const inFlight = useRef(false)

  useEffect(() => {
    let cancelled = false
    setLoadError(null)
    listOrders()
      .then((rows) => {
        if (cancelled) return
        setOrders(rows)
        if (
          orderIdParam &&
          rows.some((order) => order.id === orderIdParam)
        ) {
          setOrderId(orderIdParam)
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setOrders(null)
        setLoadError(err instanceof ApiError ? err.detail : 'Could not load orders')
      })
    return () => {
      cancelled = true
    }
  }, [orderIdParam])

  async function startRunForTicket(ticketId: string) {
    await createRun({ ticket_id: ticketId })
    navigate(`/cases/${ticketId}`, { replace: true })
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inFlight.current) return

    const validation = validateForm(orderId, category, summary)
    if (validation !== null) {
      setFormError(validation)
      return
    }

    inFlight.current = true
    setSubmitting(true)
    setFormError(null)

    try {
      let ticketId = pendingTicketId
      if (ticketId === null) {
        const ticket = await createTicket({
          order_id: orderId,
          category: category as TicketCategory,
          summary: summary.trim(),
        })
        ticketId = ticket.id
        setPendingTicketId(ticketId)
      }
      await startRunForTicket(ticketId)
    } catch (err: unknown) {
      setFormError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Could not open the case',
      )
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  async function onRetryStart() {
    if (pendingTicketId === null || inFlight.current) return
    inFlight.current = true
    setSubmitting(true)
    setFormError(null)
    try {
      await startRunForTicket(pendingTicketId)
    } catch (err: unknown) {
      setFormError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Could not start the workflow',
      )
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  const busy = submitting
  const ordersEmpty = orders !== null && orders.length === 0
  const canSubmit = orders !== null && orders.length > 0 && !busy

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
      <header>
        <h1 className="font-brand text-2xl font-semibold text-ink">New case</h1>
        <p className="mt-1 text-sm text-text-muted">
          Choose an order, pick a category, and describe the issue. A reviewer
          approves any refund; this form does not decide amounts.
        </p>
      </header>

      {loadError ? (
        <p
          role="alert"
          className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger"
        >
          {loadError}
        </p>
      ) : null}

      {orders === null && loadError === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading orders…
        </p>
      ) : null}

      {ordersEmpty ? (
        <p className="rounded-md border border-line bg-paper px-4 py-6 text-sm text-text-muted">
          No orders on this account yet. You need an order before opening a case.
        </p>
      ) : null}

      {orders !== null && orders.length > 0 ? (
        <form
          className="flex flex-col gap-5 rounded-lg border border-line bg-paper p-5 sm:p-6"
          onSubmit={onSubmit}
          noValidate
        >
          {formError ? (
            <p
              id={errorId}
              role="alert"
              className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger"
            >
              {formError}
            </p>
          ) : null}

          {pendingTicketId !== null && formError !== null ? (
            <div className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-sm text-warn">
              <p>
                Your case was saved, but the workflow was not queued. Retry start
                without creating another case.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onRetryStart()}
                className="mt-2 min-h-10 rounded-md border border-warn/40 bg-paper px-3 py-1.5 text-sm font-medium text-warn hover:bg-paper/80 disabled:opacity-60"
              >
                {busy ? 'Starting…' : 'Retry start'}
              </button>
            </div>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <label htmlFor={orderIdField} className="text-sm font-medium text-text">
              Order
            </label>
            <select
              id={orderIdField}
              name="order_id"
              value={orderId}
              disabled={busy || pendingTicketId !== null}
              aria-invalid={formError !== null}
              aria-describedby={formError ? errorId : undefined}
              onChange={(event) => {
                setOrderId(event.target.value)
                setFormError(null)
              }}
              className="rounded-md border border-line bg-paper px-3 py-2.5 text-sm text-text outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lamp disabled:opacity-60"
            >
              <option value="">Select an order</option>
              {orders.map((order) => {
                const product = productNamesText(order.product_names)
                return (
                <option key={order.id} value={order.id}>
                  {product ? `${product} · ` : ''}
                  {truncateId(order.id)} · {order.status} ·{' '}
                  {formatOrderMoney(order.paid_amount_minor, order.currency)}
                </option>
                )
              })}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={categoryField} className="text-sm font-medium text-text">
              Category
            </label>
            <select
              id={categoryField}
              name="category"
              value={category}
              disabled={busy || pendingTicketId !== null}
              onChange={(event) => {
                const value = event.target.value
                setCategory(isTicketCategory(value) ? value : '')
                setFormError(null)
              }}
              className="rounded-md border border-line bg-paper px-3 py-2.5 text-sm text-text outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lamp disabled:opacity-60"
            >
              <option value="">Select a category</option>
              {TICKET_CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {categoryLabel(value)}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={summaryField} className="text-sm font-medium text-text">
              What happened?
            </label>
            <textarea
              id={summaryField}
              name="summary"
              rows={5}
              maxLength={SUMMARY_MAX}
              value={summary}
              disabled={busy || pendingTicketId !== null}
              onChange={(event) => {
                setSummary(event.target.value)
                setFormError(null)
              }}
              className="resize-y rounded-md border border-line bg-paper px-3 py-2.5 text-sm text-text outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lamp disabled:opacity-60"
            />
            <p className="text-xs text-text-muted">
              {summary.trim().length} / {SUMMARY_MAX}
            </p>
          </div>

          <button
            type="submit"
            disabled={!canSubmit || pendingTicketId !== null}
            aria-busy={busy}
            className="min-h-10 rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-text-on-ink transition-opacity hover:bg-accent-hover disabled:opacity-60"
          >
            {busy && pendingTicketId === null
              ? 'Opening case…'
              : busy
                ? 'Starting…'
                : 'Submit case'}
          </button>
        </form>
      ) : null}
    </div>
  )
}
