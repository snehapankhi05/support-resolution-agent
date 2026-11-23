import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../api'
import { truncateId } from '../api/orders'
import { listProposals, type ProposalReview } from '../api/proposals'
import { formatPaiseAsInr } from '../lib/money'
import { clipSummary, pendingQueueStats } from '../lib/proposalQueue'
import { productNamesText } from '../lib/productLabel'
import { formatWaitingSince } from '../lib/time'

/**
 * Review overview — stats and preview derived only from the live pending queue.
 */
export function ReviewOverviewPage() {
  const [rows, setRows] = useState<ProposalReview[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
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

  const stats = rows !== null ? pendingQueueStats(rows) : null
  const preview = rows !== null ? rows.slice(0, 5) : []

  return (
    <div className="flex flex-col gap-8">
      <header>
        <h1 className="font-brand text-2xl font-semibold text-ink">Review overview</h1>
        <p className="mt-1 text-sm text-text-muted">
          Current pending queue only — not historical analytics.
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

      {rows === null && error === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading overview…
        </p>
      ) : null}

      {stats !== null ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <StatTile label="Pending" value={String(stats.pendingCount)} />
          <StatTile
            label="Pending amount"
            value={formatPaiseAsInr(stats.totalAmountMinor)}
          />
          <StatTile
            label="Oldest waiting"
            value={
              stats.oldestCreatedAt
                ? formatWaitingSince(stats.oldestCreatedAt)
                : '—'
            }
          />
        </div>
      ) : null}

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-ink">Needs attention</h2>
          <Link
            to="/review/queue"
            className="text-sm font-medium text-accent hover:text-accent-hover"
          >
            Full queue
          </Link>
        </div>

        {rows !== null && rows.length === 0 ? (
          <p className="rounded-md border border-line bg-fog/60 px-4 py-8 text-center text-sm text-text-muted">
            Queue clear.
          </p>
        ) : null}

        {preview.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {preview.map((row) => {
              const ev = row.proposal_evidence
              const product = productNamesText(row.product_names)
              return (
                <li key={ev.proposal_id}>
                  <Link
                    to={`/review/proposals/${ev.proposal_id}`}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-4 py-3 text-sm hover:border-accent"
                  >
                    <span className="tabular-nums font-medium">
                      {formatPaiseAsInr(ev.amount_minor)}
                    </span>
                    <span className="text-text">
                      {product ?? truncateId(ev.order_id)}
                    </span>
                    {product ? (
                      <span className="font-mono text-xs text-text-muted">
                        {truncateId(ev.order_id)}
                      </span>
                    ) : null}
                    <span className="text-text-muted">
                      {formatWaitingSince(ev.created_at)}
                    </span>
                    <span className="w-full text-text-muted sm:w-auto sm:max-w-xs">
                      {clipSummary(row.customer_context.summary, 48)}
                    </span>
                  </Link>
                </li>
              )
            })}
          </ul>
        ) : null}
      </section>
    </div>
  )
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-fog/50 px-4 py-4">
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">{label}</p>
      <p className="mt-2 font-brand text-2xl font-semibold tabular-nums text-ink">{value}</p>
      <span className="mt-3 block h-0.5 w-8 bg-accent" aria-hidden="true" />
    </div>
  )
}
