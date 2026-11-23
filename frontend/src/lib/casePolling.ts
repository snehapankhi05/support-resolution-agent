import type { Ticket } from '../api/tickets'

export type PollMode = 'fast' | 'slow' | 'stop'

export const POLL_INTERVAL_MS = {
  fast: 1500,
  slow: 10_000,
} as const

type TicketPollFields = Pick<Ticket, 'message' | 'refund_status' | 'status'>

/**
 * Decide whether CasePage should poll fast, slow, or stop.
 *
 * Corrects the blueprint “null message + awaiting_approval” row: the API usually
 * returns a non-null awaiting-approval sentence, so status alone keeps polling.
 */
export function pollingMode(ticket: TicketPollFields): PollMode {
  const { message, refund_status: refund, status } = ticket

  if (refund === 'unknown') return 'slow'

  if (refund === 'reserved' || refund === 'submitted' || refund === 'processing') {
    return 'fast'
  }

  if (refund === 'succeeded' || refund === 'failed') return 'stop'

  if (status === 'resolved' || status === 'escalated') return 'stop'

  if (status === 'awaiting_approval') return 'fast'

  if (message === null && status === 'open') return 'fast'

  // Non-null message, no refund row: order-status / ineligible / reject, etc.
  if (message !== null && refund === null) return 'stop'

  // Unused statuses (e.g. awaiting_customer) or odd shapes: keep looking if no message.
  if (message === null) return 'fast'

  return 'stop'
}

export type NullMessageHint = 'starting' | 'reserved' | 'generic'

/** Copy when API message is null — never invent a payment outcome. */
export function nullMessageHint(ticket: TicketPollFields): NullMessageHint {
  if (ticket.refund_status === 'reserved') return 'reserved'
  if (ticket.status === 'open') return 'starting'
  return 'generic'
}

/** Truth table for the corrected polling rules. */
export const POLL_MODE_FIXTURES: ReadonlyArray<{
  name: string
  ticket: TicketPollFields
  expect: PollMode
}> = [
  {
    name: 'early open null message',
    ticket: { message: null, refund_status: null, status: 'open' },
    expect: 'fast',
  },
  {
    name: 'awaiting approval with API sentence',
    ticket: {
      message: 'Your refund request is awaiting approval.',
      refund_status: null,
      status: 'awaiting_approval',
    },
    expect: 'fast',
  },
  {
    name: 'reserved null message',
    ticket: { message: null, refund_status: 'reserved', status: 'awaiting_approval' },
    expect: 'fast',
  },
  {
    name: 'submitted with processing sentence',
    ticket: {
      message: 'The payment service accepted your refund request. Processing is still pending.',
      refund_status: 'submitted',
      status: 'open',
    },
    expect: 'fast',
  },
  {
    name: 'processing',
    ticket: {
      message: 'The payment service accepted your refund request. Processing is still pending.',
      refund_status: 'processing',
      status: 'resolved',
    },
    expect: 'fast',
  },
  {
    name: 'unknown slow',
    ticket: {
      message: "I couldn't confirm the refund outcome yet. The case is being checked.",
      refund_status: 'unknown',
      status: 'open',
    },
    expect: 'slow',
  },
  {
    name: 'succeeded stops even if ticket still open',
    ticket: {
      message: 'Your refund of ₹2,499 has been confirmed.',
      refund_status: 'succeeded',
      status: 'open',
    },
    expect: 'stop',
  },
  {
    name: 'failed stops',
    ticket: {
      message: 'The refund could not be completed.',
      refund_status: 'failed',
      status: 'open',
    },
    expect: 'stop',
  },
  {
    name: 'resolved ticket stops',
    ticket: { message: null, refund_status: null, status: 'resolved' },
    expect: 'stop',
  },
  {
    name: 'escalated stops',
    ticket: {
      message: 'A support specialist will review your case.',
      refund_status: null,
      status: 'escalated',
    },
    expect: 'stop',
  },
  {
    name: 'reject message on reopened open ticket',
    ticket: {
      message: 'A reviewer declined this refund request.',
      refund_status: null,
      status: 'open',
    },
    expect: 'stop',
  },
  {
    name: 'order status sentence',
    ticket: {
      message: 'The latest order status is delivered.',
      refund_status: null,
      status: 'open',
    },
    expect: 'stop',
  },
]

export function pollModeFixtureFailures(): string[] {
  const failures: string[] = []
  for (const row of POLL_MODE_FIXTURES) {
    const got = pollingMode(row.ticket)
    if (got !== row.expect) {
      failures.push(`${row.name}: expected ${row.expect}, got ${got}`)
    }
  }
  return failures
}
