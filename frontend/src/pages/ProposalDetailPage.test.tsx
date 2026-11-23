/** @vitest-environment jsdom */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api'
import {
  DECISION_ALREADY_RECORDED,
  DECISION_RECORDED_ENQUEUE_FAILED,
  type ProposalReview,
} from '../api/proposals'
import { ProposalDetailPage } from './ProposalDetailPage'

const getProposal = vi.fn()
const decideProposal = vi.fn()

vi.mock('../api/proposals', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/proposals')>()
  return {
    ...actual,
    getProposal: (...args: unknown[]) => getProposal(...args),
    decideProposal: (...args: unknown[]) => decideProposal(...args),
  }
})

const pendingReview: ProposalReview = {
  proposal_evidence: {
    proposal_id: 'prop-1',
    status: 'pending_approval',
    created_at: '2026-01-15T10:00:00Z',
    order_id: 'order-1',
    amount_minor: 249_900,
    policy_effect: 'allow',
    policy_versions: [{ policy_key: 'refund', version: 1 }],
    payload_hash: 'hash-abcdef',
  },
  customer_context: {
    ticket_id: 'ticket-1',
    summary: 'Headphones never arrived',
  },
  product_names: ['Headphones'],
}

function renderDetail(proposalId = 'prop-1') {
  return render(
    <MemoryRouter initialEntries={[`/review/proposals/${proposalId}`]}>
      <Routes>
        <Route
          path="/review/proposals/:proposalId"
          element={<ProposalDetailPage />}
        />
        <Route path="/review/queue" element={<p>Queue</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProposalDetailPage decision wiring', () => {
  beforeEach(() => {
    getProposal.mockReset()
    decideProposal.mockReset()
    getProposal.mockResolvedValue(pendingReview)
  })

  it('503 enqueue-failed locks actions and Retry repeats the same decision', async () => {
    const user = userEvent.setup()
    decideProposal.mockRejectedValueOnce(
      new ApiError(503, DECISION_RECORDED_ENQUEUE_FAILED),
    )
    decideProposal.mockResolvedValueOnce({
      proposal_id: 'prop-1',
      proposal_status: 'approved',
      approval_status: 'approved',
    })

    renderDetail()
    await screen.findByRole('button', { name: 'Approve' })

    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(
      await screen.findByRole('button', { name: 'Retry resume' }),
    ).toBeInTheDocument()
    expect(screen.getByText(DECISION_RECORDED_ENQUEUE_FAILED)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    })
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled()
    expect(decideProposal).toHaveBeenCalledWith('prop-1', 'approve')

    await user.click(screen.getByRole('button', { name: 'Retry resume' }))

    await waitFor(() => {
      expect(decideProposal).toHaveBeenCalledTimes(2)
    })
    expect(decideProposal).toHaveBeenNthCalledWith(2, 'prop-1', 'approve')
  })

  it('409 conflict unlocks Approve/Reject so the reviewer can try again', async () => {
    const user = userEvent.setup()
    decideProposal.mockRejectedValue(
      new ApiError(409, 'policy_conflict'),
    )

    renderDetail()
    const approve = await screen.findByRole('button', { name: 'Approve' })

    await user.click(approve)

    expect(await screen.findByText('policy_conflict')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry resume' })).not.toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
    })
    expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled()
  })

  it('already-recorded keeps actions locked without Retry', async () => {
    const user = userEvent.setup()
    decideProposal.mockRejectedValue(
      new ApiError(409, DECISION_ALREADY_RECORDED),
    )

    renderDetail()
    const reject = await screen.findByRole('button', { name: 'Reject' })

    await user.click(reject)

    expect(
      await screen.findByText(/decision was already recorded/i),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry resume' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled()
  })
})
