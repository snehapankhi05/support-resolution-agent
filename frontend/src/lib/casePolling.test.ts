import { describe, expect, it } from 'vitest'

import {
  POLL_MODE_FIXTURES,
  nullMessageHint,
  pollModeFixtureFailures,
  pollingMode,
} from './casePolling'

describe('pollingMode', () => {
  it('covers all 12 POLL_MODE_FIXTURES', () => {
    expect(POLL_MODE_FIXTURES).toHaveLength(12)
    expect(pollModeFixtureFailures()).toEqual([])
  })

  it.each(POLL_MODE_FIXTURES)('$name → $expect', ({ ticket, expect: mode }) => {
    expect(pollingMode(ticket)).toBe(mode)
  })
})

describe('nullMessageHint', () => {
  it('reserved refund → reserved copy key', () => {
    expect(
      nullMessageHint({
        message: null,
        refund_status: 'reserved',
        status: 'awaiting_approval',
      }),
    ).toBe('reserved')
  })

  it('open with null refund → starting', () => {
    expect(
      nullMessageHint({ message: null, refund_status: null, status: 'open' }),
    ).toBe('starting')
  })

  it('other shapes → generic', () => {
    expect(
      nullMessageHint({
        message: null,
        refund_status: null,
        status: 'awaiting_approval',
      }),
    ).toBe('generic')
  })
})
