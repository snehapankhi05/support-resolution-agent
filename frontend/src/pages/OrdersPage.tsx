import { useEffect, useState } from 'react'

import { ApiError } from '../api'
import { listOrders, truncateId, type Order } from '../api/orders'
import { InlineAlert } from '../components/InlineAlert'
import { StatusBadge } from '../components/StatusBadge'
import { formatOrderMoney } from '../lib/money'
import { productNamesText } from '../lib/productLabel'

function formatDeliveredAt(value: string | null): string {
  if (value === null || value === '') return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('en-IN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/**
 * Customer orders list — API fields only; no eligibility or invented columns.
 */
export function OrdersPage() {
  const [orders, setOrders] = useState<Order[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)
    listOrders()
      .then((rows) => {
        if (!cancelled) setOrders(rows)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setOrders(null)
        setError(err instanceof ApiError ? err.detail : 'Could not load orders')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="font-brand text-2xl font-semibold text-ink">Orders</h1>
        <p className="mt-1 text-sm text-text-muted">
          Your orders from this account. Amounts are shown in paise converted to rupees.
        </p>
      </header>

      {error ? <InlineAlert tone="danger">{error}</InlineAlert> : null}

      {orders === null && error === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading orders…
        </p>
      ) : null}

      {orders !== null && orders.length === 0 ? (
        <p className="rounded-md border border-line bg-paper px-4 py-6 text-sm text-text-muted">
          No orders yet for this account.
        </p>
      ) : null}

      {orders !== null && orders.length > 0 ? (
        <>
          <div className="hidden overflow-x-auto rounded-lg border border-line bg-paper md:block">
            <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
              <thead className="border-b border-line text-xs uppercase tracking-wide text-text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Order</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Paid</th>
                  <th className="px-4 py-3 font-medium">Refunded</th>
                  <th className="px-4 py-3 font-medium">Delivered</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => {
                  const product = productNamesText(order.product_names)
                  return (
                  <tr key={order.id} className="border-b border-line last:border-b-0 hover:bg-fog/60">
                    <td className="px-4 py-3 text-text">
                      {product ? <span className="block text-sm">{product}</span> : null}
                      <span className="block font-mono text-xs tabular-nums text-text-muted">
                        {truncateId(order.id)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge label={order.status} />
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      {formatOrderMoney(order.paid_amount_minor, order.currency)}
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      {formatOrderMoney(order.refund_committed_minor, order.currency)}
                    </td>
                    <td className="px-4 py-3 text-text-muted">
                      {formatDeliveredAt(order.delivered_at)}
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <ul className="flex flex-col gap-3 md:hidden">
            {orders.map((order) => {
              const product = productNamesText(order.product_names)
              return (
              <li
                key={order.id}
                className="rounded-lg border border-line bg-paper px-4 py-3 text-sm"
              >
                {product ? <p className="text-sm font-medium text-text">{product}</p> : null}
                <p className="font-mono text-xs tabular-nums text-text-muted">
                  {truncateId(order.id)}
                </p>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <StatusBadge label={order.status} />
                  <span className="tabular-nums font-medium">
                    {formatOrderMoney(order.paid_amount_minor, order.currency)}
                  </span>
                </div>
                <p className="mt-2 text-text-muted">
                  Refunded{' '}
                  <span className="tabular-nums text-text">
                    {formatOrderMoney(order.refund_committed_minor, order.currency)}
                  </span>
                </p>
                <p className="mt-1 text-text-muted">
                  Delivered {formatDeliveredAt(order.delivered_at)}
                </p>
              </li>
              )
            })}
          </ul>
        </>
      ) : null}
    </div>
  )
}
