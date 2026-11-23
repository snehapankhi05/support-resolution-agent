import { ApiError } from '../api/errors'
import {
  DECISION_ALREADY_RECORDED,
  DECISION_RECORDED_ENQUEUE_FAILED,
  PROPOSAL_NOT_PENDING,
  type ProposalDecisionResponse,
} from '../api/proposals'

/**
 * Classify POST /proposals/{id}/decision outcomes.
 * 503 with the exact enqueue-failed detail means the decision is saved.
 * Only "decision already recorded" among 409s means a prior decision exists.
 */
export type DecisionClassification =
  | {
      kind: 'recorded'
      source: 'success'
      response: ProposalDecisionResponse
    }
  | {
      kind: 'recorded'
      source: 'already_recorded' | 'enqueue_failed'
      detail: string
    }
  | {
      kind: 'not_pending'
      detail: string
    }
  | {
      kind: 'conflict'
      detail: string
    }
  | {
      kind: 'error'
      detail: string
    }

export function classifyDecisionSuccess(
  body: ProposalDecisionResponse,
): DecisionClassification {
  return { kind: 'recorded', source: 'success', response: body }
}

export function classifyDecisionFailure(err: unknown): DecisionClassification {
  if (!(err instanceof ApiError)) {
    return {
      kind: 'error',
      detail: err instanceof Error ? err.message : 'Decision request failed',
    }
  }

  if (err.status === 503 && err.detail === DECISION_RECORDED_ENQUEUE_FAILED) {
    return { kind: 'recorded', source: 'enqueue_failed', detail: err.detail }
  }

  if (err.status === 409 && err.detail === DECISION_ALREADY_RECORDED) {
    return { kind: 'recorded', source: 'already_recorded', detail: err.detail }
  }

  if (err.status === 409 && err.detail === PROPOSAL_NOT_PENDING) {
    return { kind: 'not_pending', detail: err.detail }
  }

  if (err.status === 409) {
    return { kind: 'conflict', detail: err.detail }
  }

  return { kind: 'error', detail: err.detail }
}

/** True when Approve/Reject must stay disabled (decision is stored). */
export function decisionLocksActions(result: DecisionClassification): boolean {
  return result.kind === 'recorded'
}
