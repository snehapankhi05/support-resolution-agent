import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/errors'
import {
  DECISION_ALREADY_RECORDED,
  DECISION_RECORDED_ENQUEUE_FAILED,
  PROPOSAL_NOT_PENDING,
  type ProposalDecisionResponse,
} from '../api/proposals'
import {
  classifyDecisionFailure,
  classifyDecisionSuccess,
  decisionLocksActions,
  type DecisionClassification,
} from './proposalDecision'

function label(result: DecisionClassification): string {
  return result.kind === 'recorded' ? `recorded:${result.source}` : result.kind
}

const successBody: ProposalDecisionResponse = {
  proposal_id: 'p1',
  proposal_status: 'approved',
  approval_status: 'approved',
}

/** Same eight cases as the former check-proposal-decision.mjs truth table. */
const DECISION_FIXTURES: ReadonlyArray<{
  name: string
  expect: string
  run: () => DecisionClassification
}> = [
  {
    name: '200 success',
    expect: 'recorded:success',
    run: () => classifyDecisionSuccess(successBody),
  },
  {
    name: '503 enqueue failed is recorded',
    expect: 'recorded:enqueue_failed',
    run: () =>
      classifyDecisionFailure(
        new ApiError(503, DECISION_RECORDED_ENQUEUE_FAILED),
      ),
  },
  {
    name: '503 other is error',
    expect: 'error',
    run: () =>
      classifyDecisionFailure(new ApiError(503, 'database unavailable')),
  },
  {
    name: '409 already recorded',
    expect: 'recorded:already_recorded',
    run: () =>
      classifyDecisionFailure(new ApiError(409, DECISION_ALREADY_RECORDED)),
  },
  {
    name: '409 not pending',
    expect: 'not_pending',
    run: () =>
      classifyDecisionFailure(new ApiError(409, PROPOSAL_NOT_PENDING)),
  },
  {
    name: '409 policy_conflict unlocks',
    expect: 'conflict',
    run: () =>
      classifyDecisionFailure(new ApiError(409, 'policy_conflict')),
  },
  {
    name: '409 amount_changed unlocks',
    expect: 'conflict',
    run: () =>
      classifyDecisionFailure(new ApiError(409, 'amount_changed')),
  },
  {
    name: '500 error',
    expect: 'error',
    run: () => classifyDecisionFailure(new ApiError(500, 'boom')),
  },
]

describe('proposalDecision classification', () => {
  it('covers all 8 decision fixtures', () => {
    expect(DECISION_FIXTURES).toHaveLength(8)
  })

  it.each(DECISION_FIXTURES)('$name → $expect', ({ expect: expected, run }) => {
    expect(label(run())).toBe(expected)
  })
})

describe('decisionLocksActions', () => {
  it('locks when kind is recorded (any source)', () => {
    expect(
      decisionLocksActions(classifyDecisionSuccess(successBody)),
    ).toBe(true)
    expect(
      decisionLocksActions(
        classifyDecisionFailure(
          new ApiError(503, DECISION_RECORDED_ENQUEUE_FAILED),
        ),
      ),
    ).toBe(true)
    expect(
      decisionLocksActions(
        classifyDecisionFailure(new ApiError(409, DECISION_ALREADY_RECORDED)),
      ),
    ).toBe(true)
  })

  it('does not lock conflict / not_pending / error', () => {
    expect(
      decisionLocksActions(
        classifyDecisionFailure(new ApiError(409, 'policy_conflict')),
      ),
    ).toBe(false)
    expect(
      decisionLocksActions(
        classifyDecisionFailure(new ApiError(409, PROPOSAL_NOT_PENDING)),
      ),
    ).toBe(false)
    expect(
      decisionLocksActions(classifyDecisionFailure(new ApiError(500, 'boom'))),
    ).toBe(false)
  })
})
