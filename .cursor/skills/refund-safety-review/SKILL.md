---
name: refund-safety-review
description: Review code that touches refunds, approvals, payment calls or money state. Use when asked to review, audit or check refund, approval, idempotency, reconciliation or ledger code.
---

# Refund safety review

Review the code and report findings as PASS / FAIL / NOT VERIFIED per item, with file and
line references. Do not edit code unless asked.

## Checklist
1. Money is integer minor units everywhere. No floats.
2. Amount is recomputed in code from the order; never taken from the model or request body.
3. Approval is read from the database, bound to the exact proposal (`payload_hash`), not
   expired, and decided by an authenticated reviewer.
4. After a resume, order, amount, ownership and refundable balance are revalidated.
5. Reservation uses a short transaction with `SELECT ... FOR UPDATE` and commits BEFORE any
   network call.
6. No network call happens while a DB transaction is open.
7. The provider call uses a stable idempotency key stored on `refund_operations`; retries
   reuse the same key and payload.
8. Timeout or uncertain result sets status `unknown`; reconciliation queries the provider
   before any retry.
9. If the refund succeeded but the ticket update failed, only the ticket update is retried.
10. Status values stay distinct: submitted, processing, succeeded, failed, unknown.
11. Customer-facing text matches the verified state; it never claims more than the provider
    confirmed.
12. Audit events are written for each state change with redacted payloads.
13. There is a test for: duplicate request, concurrent requests, crash after provider
    accepts, and expired or changed approval.
14. No model-reachable tool can execute a refund.

## Output
A table of the 14 items with status, then the top 3 risks and the tests that are missing.
