/** @vitest-environment jsdom */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Ticket } from '../api/tickets'
import { POLL_INTERVAL_MS } from '../lib/casePolling'
import { CasePage } from './CasePage'

const getTicket = vi.fn()

vi.mock('../api/tickets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tickets')>()
  return {
    ...actual,
    getTicket: (...args: unknown[]) => getTicket(...args),
  }
})

function ticket(partial: Partial<Ticket> & Pick<Ticket, 'message' | 'status'>): Ticket {
  return {
    id: 'ticket-1',
    order_id: 'order-1',
    category: 'order_status',
    summary: 'Where is my order?',
    refund_status: null,
    ...partial,
  }
}

function renderCase(ticketId = 'ticket-1') {
  return render(
    <MemoryRouter initialEntries={[`/cases/${ticketId}`]}>
      <Routes>
        <Route path="/cases/:ticketId" element={<CasePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CasePage message and polling wiring', () => {
  beforeEach(() => {
    getTicket.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllTimers()
  })

  it('shows reserved null-message copy while refund is reserved', async () => {
    getTicket.mockResolvedValue(
      ticket({
        message: null,
        status: 'awaiting_approval',
        refund_status: 'reserved',
        category: 'refund_request',
      }),
    )

    renderCase()

    expect(
      await screen.findByText('Payment processing — not confirmed yet.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Checking for updates…')).toBeInTheDocument()
  })

  it('stops polling when the ticket is settled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    getTicket.mockResolvedValue(
      ticket({
        message: 'The latest order status is delivered.',
        status: 'open',
        refund_status: null,
      }),
    )

    renderCase()

    expect(
      await screen.findByText('The latest order status is delivered.'),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('Checking for updates…'),
    ).not.toBeInTheDocument()
    expect(getTicket).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS.fast * 3)
    })

    expect(getTicket).toHaveBeenCalledTimes(1)
  })

  it('Refresh fetches again without waiting for the poll timer', async () => {
    vi.useRealTimers()
    const user = userEvent.setup()
    getTicket.mockResolvedValue(
      ticket({
        message: 'The latest order status is delivered.',
        status: 'open',
      }),
    )

    renderCase()
    await screen.findByText('The latest order status is delivered.')
    expect(getTicket).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      expect(getTicket).toHaveBeenCalledTimes(2)
    })
  })
})
