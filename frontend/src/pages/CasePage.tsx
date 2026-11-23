import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api'
import { truncateId } from '../api/orders'
import { categoryLabel, getTicket, type Ticket } from '../api/tickets'
import { InlineAlert } from '../components/InlineAlert'
import { StatusBadge } from '../components/StatusBadge'
import {
  POLL_INTERVAL_MS,
  nullMessageHint,
  pollingMode,
  type PollMode,
} from '../lib/casePolling'
import { productNamesText } from '../lib/productLabel'

/**
 * Message-first case tracking with quiet polling (P2.3).
 * Settled / slow / fast rules live in lib/casePolling.ts.
 */
export function CasePage() {
  const { ticketId } = useParams<{ ticketId: string }>()
  const [ticket, setTicket] = useState<Ticket | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pollNotice, setPollNotice] = useState<string | null>(null)
  const [initialLoading, setInitialLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const ticketRef = useRef<Ticket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inFlightRef = useRef(false)
  const cancelledRef = useRef(false)

  ticketRef.current = ticket

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const schedule = useCallback(
    (mode: PollMode, load: () => void) => {
      if (mode === 'stop') return
      clearTimer()
      timerRef.current = setTimeout(load, POLL_INTERVAL_MS[mode])
    },
    [clearTimer],
  )

  const fetchTicket = useCallback(
    async (options: { initial: boolean; manual: boolean }) => {
      if (!ticketId || inFlightRef.current) return
      inFlightRef.current = true
      if (options.manual) setRefreshing(true)

      try {
        const row = await getTicket(ticketId)
        if (cancelledRef.current) return
        setTicket(row)
        setError(null)
        setPollNotice(null)
        schedule(pollingMode(row), () => {
          void fetchTicket({ initial: false, manual: false })
        })
      } catch (err: unknown) {
        if (cancelledRef.current) return
        const detail =
          err instanceof ApiError
            ? err.detail
            : err instanceof Error
              ? err.message
              : 'Could not load this case'
        const status = err instanceof ApiError ? err.status : 0

        if (status === 404) {
          clearTimer()
          setError(detail)
          if (options.initial) setTicket(null)
          return
        }

        if (options.initial && ticketRef.current === null) {
          setError(detail)
          setTicket(null)
          return
        }

        setPollNotice(detail)
        const mode = ticketRef.current ? pollingMode(ticketRef.current) : 'fast'
        if (mode === 'stop') return
        schedule(mode === 'slow' ? 'slow' : 'fast', () => {
          void fetchTicket({ initial: false, manual: false })
        })
      } finally {
        inFlightRef.current = false
        if (options.initial) setInitialLoading(false)
        if (options.manual) setRefreshing(false)
      }
    },
    [ticketId, schedule, clearTimer],
  )

  useEffect(() => {
    cancelledRef.current = false
    clearTimer()
    setTicket(null)
    ticketRef.current = null
    setError(null)
    setPollNotice(null)

    if (!ticketId) {
      setError('Case not found')
      setInitialLoading(false)
      return () => {
        cancelledRef.current = true
        clearTimer()
      }
    }

    setInitialLoading(true)
    void fetchTicket({ initial: true, manual: false })

    return () => {
      cancelledRef.current = true
      clearTimer()
    }
  }, [ticketId, fetchTicket, clearTimer])

  function onRefresh() {
    clearTimer()
    void fetchTicket({ initial: false, manual: true })
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-brand text-2xl font-semibold text-ink">Case</h1>
          <p className="mt-1 font-mono text-xs text-text-muted">
            {ticketId ? truncateId(ticketId) : '—'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onRefresh}
            disabled={!ticketId || refreshing || initialLoading}
            aria-busy={refreshing}
            className="min-h-10 rounded-md border border-line bg-paper px-3 py-2 text-sm font-medium text-text hover:border-accent disabled:opacity-60"
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <Link
            to="/cases/new"
            className="text-sm font-medium text-accent hover:text-accent-hover"
          >
            Open another case
          </Link>
        </div>
      </header>

      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}

      {pollNotice && ticket !== null ? (
        <InlineAlert tone="warn">Update paused briefly: {pollNotice}</InlineAlert>
      ) : null}

      {initialLoading && ticket === null && error === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading case…
        </p>
      ) : null}

      {ticket !== null ? <CaseBody ticket={ticket} /> : null}
    </div>
  )
}

function CaseBody({ ticket }: { ticket: Ticket }) {
  const mode = pollingMode(ticket)
  const hint = ticket.message === null ? nullMessageHint(ticket) : null

  return (
    <section
      className="flex flex-col gap-5 rounded-lg border border-line bg-paper p-5 sm:p-6"
      aria-busy={mode !== 'stop'}
    >
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
          Status message
        </p>
        <div className="mt-2" aria-live="polite">
          {ticket.message ? (
            <p className="case-outcome-enter text-xl font-medium leading-snug text-ink sm:text-2xl">
              {ticket.message}
            </p>
          ) : (
            <p className="rounded-md border border-warn/30 bg-warn-soft px-3 py-3 text-sm text-warn">
              {hint === 'reserved'
                ? 'Payment processing — not confirmed yet.'
                : hint === 'starting'
                  ? 'Case opened — waiting for the workflow to update this message.'
                  : 'Waiting for a verified status message.'}
            </p>
          )}
        </div>
        {mode !== 'stop' ? (
          <p className="mt-2 text-xs text-text-muted">
            {mode === 'slow'
              ? 'Checking again every 10 seconds…'
              : 'Checking for updates…'}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-2">
        <StatusBadge label={`Ticket: ${ticket.status}`} />
        {ticket.refund_status ? (
          <StatusBadge label={`Refund: ${ticket.refund_status}`} />
        ) : null}
        <StatusBadge label={categoryLabel(ticket.category)} />
      </div>

      <dl className="grid gap-3 border-t border-line pt-4 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-text-muted">Summary</dt>
          <dd className="mt-0.5 text-text">{ticket.summary}</dd>
        </div>
        <div>
          <dt className="text-text-muted">Product</dt>
          <dd className="mt-0.5 text-text">
            {productNamesText(ticket.product_names) ?? '—'}
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">Order</dt>
          <dd className="mt-0.5 font-mono text-xs tabular-nums text-text">
            {ticket.order_id ? truncateId(ticket.order_id) : '—'}
          </dd>
        </div>
      </dl>
    </section>
  )
}
