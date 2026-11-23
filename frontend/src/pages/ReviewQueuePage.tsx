import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../api'
import { truncateId } from '../api/orders'
import { listProposals, type ProposalReview } from '../api/proposals'
import { formatPaiseAsInr } from '../lib/money'
import { clipSummary } from '../lib/proposalQueue'
import { productNamesText } from '../lib/productLabel'
import { formatCreatedAt, formatWaitingSince } from '../lib/time'

/**
 * Pending refund proposals only — from GET /proposals.
 */
export function ReviewQueuePage() {
  const [rows, setRows] = useState<ProposalReview[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async (manual: boolean) => {
    if (manual) setRefreshing(true)
    try {
      const list = await listProposals()
      setRows(list)
      setError(null)
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the queue')
      if (!manual) setRows(null)
    } finally {
      if (manual) setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setError(null)
    listProposals()
      .then((list) => {
        if (!cancelled) setRows(list)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setRows(null)
        setError(err instanceof ApiError ? err.detail : 'Could not load the queue')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-brand text-2xl font-semibold text-ink">Queue</h1>
          <p className="mt-1 text-sm text-text-muted">
            Current pending queue only — not historical analytics.
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
          <button
            type="button"
            className="font-medium underline"
            onClick={() => void load(true)}
          >
            Retry
          </button>
        </p>
      ) : null}

      {rows === null && error === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading queue…
        </p>
      ) : null}

      {rows !== null && rows.length === 0 ? (
        <p className="rounded-md border border-line bg-fog/60 px-4 py-8 text-center text-sm text-text-muted">
          Queue clear. No proposals waiting for a decision.
        </p>
      ) : null}

      {rows !== null && rows.length > 0 ? (
        <>
          <div className="hidden overflow-x-auto rounded-lg border border-line md:block">
            <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
              <thead className="border-b border-line text-xs uppercase tracking-wide text-text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Created</th>
                  <th className="px-4 py-3 font-medium">Amount</th>
                  <th className="px-4 py-3 font-medium">Order</th>
                  <th className="px-4 py-3 font-medium">Policy</th>
                  <th className="px-4 py-3 font-medium">Summary</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const ev = row.proposal_evidence
                  const id = ev.proposal_id
                  const product = productNamesText(row.product_names)
                  return (
                    <tr key={id} className="border-b border-line last:border-b-0 hover:bg-fog/60">
                      <td className="px-4 py-3 text-text-muted">
                        <Link
                          to={`/review/proposals/${id}`}
                          className="block text-text hover:text-accent"
                        >
                          {formatCreatedAt(ev.created_at)}
                          <span className="mt-0.5 block text-xs">
                            {formatWaitingSince(ev.created_at)}
                          </span>
                        </Link>
                      </td>
                      <td className="px-4 py-3 tabular-nums font-medium">
                        <Link to={`/review/proposals/${id}`} className="hover:text-accent">
                          {formatPaiseAsInr(ev.amount_minor)}
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        <Link to={`/review/proposals/${id}`} className="hover:text-accent">
                          <span className="block text-sm text-text">
                            {product ?? truncateId(ev.order_id)}
                          </span>
                          {product ? (
                            <span className="mt-0.5 block font-mono text-xs text-text-muted">
                              {truncateId(ev.order_id)}
                            </span>
                          ) : null}
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        <Link to={`/review/proposals/${id}`} className="hover:text-accent">
                          {ev.policy_effect}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-text-muted">
                        <Link to={`/review/proposals/${id}`} className="hover:text-accent">
                          {clipSummary(row.customer_context.summary)}
                        </Link>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <ul className="flex flex-col gap-3 md:hidden">
            {rows.map((row) => {
              const ev = row.proposal_evidence
              const product = productNamesText(row.product_names)
              return (
                <li key={ev.proposal_id}>
                  <Link
                    to={`/review/proposals/${ev.proposal_id}`}
                    className="block rounded-lg border border-line px-4 py-3 text-sm hover:border-accent"
                  >
                    <p className="tabular-nums font-medium">
                      {formatPaiseAsInr(ev.amount_minor)}
                    </p>
                    <p className="mt-1 text-sm text-text">
                      {product ?? truncateId(ev.order_id)}
                    </p>
                    <p className="mt-0.5 font-mono text-xs text-text-muted">
                      {product ? `${truncateId(ev.order_id)} · ` : ''}
                      {ev.policy_effect}
                    </p>
                    <p className="mt-2 text-text-muted">
                      {clipSummary(row.customer_context.summary)}
                    </p>
                    <p className="mt-1 text-xs text-text-muted">
                      {formatWaitingSince(ev.created_at)}
                    </p>
                  </Link>
                </li>
              )
            })}
          </ul>
        </>
      ) : null}
    </div>
  )
}
