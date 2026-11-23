# ADR 0003: The model proposes; deterministic code decides

**Status:** accepted

## Decision
Amounts, ownership, eligibility, approval validity and state transitions are enforced in
ordinary code and database constraints. Model-shaped outputs (summaries, retrieval drafts)
are untrusted until schema validation and deterministic re-checks in code.

## Current implementation (Phase 6+)
The shipped LangGraph path is **deterministic**: ticket category selects the branch; resume
payload does not choose payment. An LLM is not required for pytest, the Phase 9 harness, or
the three demos. Earlier wording that “the LLM classifies” described the product rule
(propose vs decide), not a mandatory live classifier in this repo.

## Consequences
- Retrieval similarity is never proof of eligibility.
- Untrusted text can cause clarification, refusal, or escalation — never an unauthorized payment.
- Evaluation measures resulting database state, not just response text.
