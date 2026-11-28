# ADR 0003: The model proposes; deterministic code decides

**Status:** accepted

## Decision
Amounts, ownership, eligibility, approval validity and state transitions are enforced in
ordinary code and database constraints. Model-shaped outputs (summaries, retrieval drafts)
are untrusted until schema validation and deterministic re-checks in code.

That decision still holds. It is the safety rule: a model, if one is ever added, may not
choose the amount, the owner, the approval, or the payment.

## Earlier proposal
The first wording of this decision assumed a model on the investigation path. An LLM
would classify the customer's request and draft the reply. Code would then re-check
eligibility, the amount, the approval, and the payment. That proposal is historical.
It is not the workflow in this repository.

## Implemented architecture
`app/workflows/support_graph.py` does not call a language model. The stored
`tickets.category` selects the branch:

- `damaged_item` escalates and creates no proposal.
- `order_status` reads the order and stops. It does not propose a refund.
- `refund_request` calls `create_refund_proposal` in-process.

`evaluate_refund_eligibility` decides eligibility and the integer-paise amount from
order, return, and refund facts. `current_refund_policy` permits a proposal only from
policy metadata. The customer summary is not an input to either check. Customer
sentences are fixed strings from `app/services/messages.py`, stored on the ticket or
read from the refund row. They are not model drafts.

A human approves or rejects a pending proposal. `execute_approved_refund` runs only
after that stored approval. The value passed to `Command(resume=...)` is ignored.
No LLM decides refund eligibility, the amount, approval, or payment execution. An LLM
is not required for pytest, the Phase 9 harness, or the three demos.

## Consequences
- Retrieval similarity is never proof of eligibility.
- Customer text, tool output, and policy chunk text stay untrusted. They do not select
  the branch, set the amount, approve a refund, or authorize a payment.
- Evaluation measures resulting database state, not just response text.
