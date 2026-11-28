/** @vitest-environment jsdom */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Escalation } from '../api/escalations'
import { EscalationDetailPage } from './EscalationDetailPage'

const getEscalation = vi.fn()

vi.mock('../api/escalations', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/escalations')>()
  return {
    ...actual,
    getEscalation: (...args: unknown[]) => getEscalation(...args),
  }
})

const escalated: Escalation = {
  ticket_id: 'ticket-1',
  category: 'damaged_item',
  product_names: ['Headphones'],
  resolution_message: 'A support specialist will review your case.',
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/review/escalations/ticket-1']}>
      <Routes>
        <Route path="/review/escalations/:ticketId" element={<EscalationDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('EscalationDetailPage', () => {
  beforeEach(() => {
    getEscalation.mockReset()
    getEscalation.mockResolvedValue(escalated)
  })

  it('shows the category, product, and stored sentence without a refund decision', async () => {
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Headphones' })).toBeTruthy()
    expect(screen.getAllByText('Damaged item').length).toBeGreaterThan(0)
    expect(screen.getByText('A support specialist will review your case.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull()
    expect(screen.queryByText(/₹/)).toBeNull()
  })
})
