# ADR 0001: LangGraph checkpoints are not the financial ledger

**Status:** accepted

## Context
LangGraph checkpoints let a workflow pause for human approval and resume after a restart.
They store workflow state, not verified money movement.

## Decision
- Refund accounting lives in application tables (`orders.refund_committed_minor`,
  `refund_operations`) with CHECK and UNIQUE constraints.
- Checkpoint tables live in the separate `langgraph` schema.
- Approvals bind to an immutable `action_proposals` row (payload + hash).

## Consequences
- A restarted or replayed workflow cannot create a second refund: the unique
  `idempotency_key` rejects it at the database level.
- Over-refunding is blocked by a CHECK constraint even if application code has a bug.
- Cost: refund logic is written twice (checks in code for good errors, constraints as the
  last line of defence). This is intentional.
