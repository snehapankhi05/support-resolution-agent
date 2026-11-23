# Three-minute demo script (Phase 12)

Synthetic data only. Amounts are INR; storage is integer paise. Prefer database
**`support_ui`**, mock payments on `:9001`, Celery worker (and Beat for Demo C), API
`:8000`, Vite `:5173`. Keep API and worker on the **same** `DATABASE_URL` and `REDIS_URL`.

Logical seed mapping (not literal `ORD-*` ids — use the UI order list):

| Demo label | Customer | Product cue | Seed shape |
|---|---|---|---|
| ORD-1001 style | Asha (`asha@seed.example`) | Headphones ₹2,499 | Eligible return when not already refunded |
| ORD-1002 style | Rohan (`rohan@seed.example`) | Smart watch ₹8,999 | Too old / no return → blocked |
| ORD-1003 style | Meera (`meera@seed.example`) | Keyboard ₹1,299 | Eligible; use timeout mode for Demo C |

Password for provisioned seed logins: `demo-password`. Reviewer: `priya@seed.example`.

---

## Demo A — Approved refund

**Expect:** one `succeeded` refund, customer message with confirmed amount, no second provider create.

1. Start mock payments (normal mode), worker, API, Vite.
2. Log in as **Asha** → **New case** → pick the eligible headphones order → category
   **Refund request** → submit.
3. Case page: wait until message shows awaiting approval (or equivalent verified sentence).
4. Log out → log in as **Priya** → **Queue** → open the proposal.
5. Confirm **Evidence** (amount/order/hash) is separate from **Customer context**.
6. **Approve**. If 503 enqueue-failed appears, use **Retry resume** (same decision).
7. As Asha, open the case: message should confirm the refund; badges show succeeded/resolved.

**Ledger check (optional SQL):** one `refund_operations` row for that proposal,
`status = succeeded`, `refund_committed_minor` matches reserved paise.

---

## Demo B — Blocked request

**Expect:** **0** refund operations, **0** provider creates, honest refusal/escalation message.

1. Log in as **Rohan** → new **Refund request** on the smart-watch / too-old order.
2. Wait for the verified message (no return / outside window — wording from code).
3. Confirm reviewer queue has **no** payable proposal for this ticket (or none pending for pay).

---

## Demo C — Timeout then adopt one refund

**Expect:** exactly **one** provider refund record and one ledger row ending `succeeded`
after reconcile; customer may briefly see “couldn’t confirm”.

1. Point the mock provider at **timeout-after-accept** (or the project’s equivalent control
   mode — see `mock_services/payments`).
2. As **Meera** (or another eligible order), submit a refund request; as **Priya**, approve.
3. Observe ledger/refund status `unknown` (or reserved with uncertain outcome) and the
   cautious customer sentence.
4. Ensure **Celery Beat** (or publish `payments.discover` / reconcile) is running so adoption
   can run without a second POST.
5. After reconcile: one provider record, one `succeeded` operation, customer message updates
   to the confirmed amount. Restarting the worker mid-flight must not create a second refund.

---

## Recording notes

- OBS/Loom optional; this file is the source of truth in the repo.
- Do not run Beat or the Phase 9 harness against `support` or the shared `support_test` ledger
  used by concurrent pytest.
- Never expose `execute_approved_refund` through MCP or the UI.
