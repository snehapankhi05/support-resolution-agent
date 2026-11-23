import type { ProposalReview } from '../api/proposals'

/** Stats derived only from the current pending GET /proposals list. */
export function pendingQueueStats(rows: ProposalReview[]) {
  const pendingCount = rows.length
  const totalAmountMinor = rows.reduce(
    (sum, row) => sum + row.proposal_evidence.amount_minor,
    0,
  )
  let oldestCreatedAt: string | null = null
  for (const row of rows) {
    const created = row.proposal_evidence.created_at
    if (oldestCreatedAt === null || created < oldestCreatedAt) {
      oldestCreatedAt = created
    }
  }
  return { pendingCount, totalAmountMinor, oldestCreatedAt }
}

export function clipSummary(summary: string, max = 72): string {
  const trimmed = summary.trim()
  if (trimmed.length <= max) return trimmed
  return `${trimmed.slice(0, max - 1)}…`
}
