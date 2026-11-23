import { apiFetch } from './client'

/** Mirrors app.api.proposals.ProposalEvidence — stored payload only. */
export type ProposalEvidence = {
  proposal_id: string
  status: string
  created_at: string
  order_id: string
  amount_minor: number
  policy_effect: string
  policy_versions: Array<{ policy_key: string; version: number }>
  payload_hash: string
}

/** Mirrors app.api.proposals.CustomerContext — untrusted complaint text. */
export type CustomerContext = {
  ticket_id: string
  summary: string
}

/** Mirrors app.api.proposals.ProposalReview. */
export type ProposalReview = {
  proposal_evidence: ProposalEvidence
  customer_context: CustomerContext
  /** Order catalog names. Display only; not part of the approval hash. */
  product_names?: string[]
}

export type ProposalDecision = 'approve' | 'reject'

/** Mirrors app.api.proposals.ProposalDecisionResponse. */
export type ProposalDecisionResponse = {
  proposal_id: string
  proposal_status: string
  approval_status: string
}

export const DECISION_RECORDED_ENQUEUE_FAILED =
  'decision recorded; workflow resume was not queued'

export const DECISION_ALREADY_RECORDED = 'decision already recorded'
export const PROPOSAL_NOT_PENDING = 'proposal is not pending approval'

export function listProposals(): Promise<ProposalReview[]> {
  return apiFetch<ProposalReview[]>('/proposals')
}

export function getProposal(proposalId: string): Promise<ProposalReview> {
  return apiFetch<ProposalReview>(`/proposals/${proposalId}`)
}

export function decideProposal(
  proposalId: string,
  decision: ProposalDecision,
): Promise<ProposalDecisionResponse> {
  return apiFetch<ProposalDecisionResponse>(`/proposals/${proposalId}/decision`, {
    method: 'POST',
    body: JSON.stringify({ decision }),
  })
}
