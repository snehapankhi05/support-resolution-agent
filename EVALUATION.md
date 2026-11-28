# Evaluation record

This document collects the evaluation evidence that exists in the repository on 28 November 2026. It is written for a technical interview or a portfolio review. It distinguishes a measured report, an automated assertion, a manual check, and a number that exists only in prose.

Nothing in this file was produced by rerunning the harness or the test suites. Application code, tests, migrations, and `evals/reports/phase9.json` were not changed to create it.

At the time of writing, local `main` matches `origin/main` at `eb1bc06`. [Actions run 6](https://github.com/snehapankhi05/support-resolution-agent/actions/runs/46145911877) on that commit succeeded. `d41e212` is the escalations commit from earlier the same day.

## How to read a result

| Kind of evidence | What it supports | What it does not support |
|---|---|---|
| Measured report | `evals/reports/phase9.json` matched a frozen ledger | Model accuracy, a holdout score, or a production rate |
| Automated assertion | A named pytest or Vitest test fails if that behavior changes | A coverage percentage, a latency number, or a security certification |
| Manual check | A person recorded a screen check on 22 November 2026 | A repeatable browser suite |
| Written record only | A number in `STATUS.md`, `learning_project.md`, or `docs/PROJECT_LEARNING_LOG.md` | A result that can be recalculated from a saved dataset or command log |

An assertion proves the behavior that its `assert` lines check. It does not prove the surrounding product is safe, complete, or ready for a real payment provider.

## What this system is evaluating

The running workflow does not call a language model. `app/workflows/support_graph.py` selects a branch from the stored ticket category. `damaged_item` escalates and creates no proposal. `order_status` explains and creates no proposal. `refund_request` calls `create_refund_proposal` in-process. `evaluate_refund_eligibility` decides eligibility and an integer-paise amount from order, return, and refund facts. Policy permission comes from document metadata, not from retrieved paragraph text. A human approves or rejects a pending proposal. `execute_approved_refund` runs only from the graph pay node after that stored approval. The value passed to `Command(resume=...)` is ignored.

ADR 0003 records an earlier proposal in which an LLM would classify the request and draft the reply. That proposal is history. It is not the shipped workflow. See `docs/adr/0003-ai-proposes-code-decides.md`.

## Phase 9 ledger harness

### What was evaluated, and why

Phase 9 asks whether twenty-five hand-built support cases leave the database and the payment provider in the state the project froze ahead of time. The question is ledger safety: unauthorized refunds and duplicate payments. It is not a model benchmark. There is no language model on this path, and the cases are not a random sample of customers.

### Methodology

Command recorded in the learning log: `uv run python -m evals.harness`.

`evals/harness.py` does the following:

- It refuses to run when its own source contains a direct call to `execute_approved_refund`. The only call site found in application code is `app/workflows/support_graph.py` line 158, which is the graph pay node. Payment during the harness happens only when a workflow reaches that node.
- It creates or reuses database `support_eval` and refuses to do that maintenance work from `support` or `support_test`.
- It points Redis at database `/15` so a worker on database 0 does not consume the messages.
- It upgrades `support_eval` with Alembic and requires revision `0005`.
- It reads inputs from `evals/cases.py` and frozen outcomes from `evals/expected.json`.
- It compares the actual ledger with `_compare`. A mismatch is a failure. The two safety counts below are also failures if they are not zero.
- It writes `evals/reports/phase9.json`.

Cases 7, 21, 22, and 23 finish with an open refund, so the harness runs them after discovery cases 24 and 25. That keeps Beat from seeing leftover open rows. Report keys stay 1 through 25.

`_compare` does not test every stored JSON key for equality. It skips `name`, `clean_case`, `forbidden_amount_minor`, `seeded_refund_rows`, and `allowed_run_statuses`. The last of those is checked separately: the actual run status must be one of the permitted values. A `clean_case` pointer compares only `ledger_status`, `provider_records`, and `create_attempts` with the named clean case. A `forbidden_amount_minor` fails the case when the committed paise equal that forbidden amount.

### The preserved result

`evals/reports/phase9.json` was read directly. It was not regenerated.

| Field | Value |
|---|---|
| Result keys | `1` through `25` (25 keys) |
| `passed` | `true` |
| `failures` | `[]` |
| `unauthorized_refunds` | `0` |
| `duplicate_payments` | `0` |
| `database` | `support_eval` |
| `expected_sha256` | `968e054c87960dfa3df3a8eba3b4decd8cfa7e394360254ef2f86c7b67e2ee4b` |

The SHA-256 was recomputed from the current bytes of `evals/expected.json`. It matches the value stored in the report. The report's top-level keys are `database`, `expected_sha256`, `failures`, `metrics`, `passed`, and `results`. There is no `elapsed_ms`.

`docs/PROJECT_LEARNING_LOG.md`, `learning_project.md`, and `STATUS.md` say this run took 64.76 seconds (`elapsed_ms` 64760). That is a written historical record. The current harness does not write a runtime, so the number cannot be read back from the JSON.

CI does not run this harness. `.github/workflows/ci.yml` runs Ruff and `uv run pytest -q`. No test file imports `evals.harness`.

### What 25/25 means

25/25 means `_compare` found no mismatches and both safety counts were zero. Each case was designed to exercise one situation. This is a scenario checklist with a frozen answer key. It is not classification accuracy, not a statistical holdout, and not a production service level.

### Safety-count formulas

For each case, using the actual report and the expected file:

```text
unexpected_rows     = max(0, actual.refund_rows - expected.refund_rows)
unexpected_attempts = max(0, actual.create_attempts - expected.create_attempts)
unexpected_records  = max(0, actual.provider_records - expected.provider_records)

unauthorized_refunds += unexpected_rows
if expected.create_attempts == 0:
    unauthorized_refunds += unexpected_attempts

duplicate_payments += unexpected_attempts + unexpected_records
```

In ordinary language: an extra refund row is an unauthorized refund. An extra provider create is also counted as unauthorized when the case was supposed to create nothing. An extra create attempt, and an extra provider record, are duplicate payments. A case that matches the frozen counts contributes zero. These are excess counts, not percentages.

The implementation is `evals/harness.py`, function `_metrics`.

### Case list and saved outcomes

Inputs are `evals/cases.py`. Outcomes below are the saved report, not a new run. Amounts are integer paise.

| Case | Name | What it asks | Saved outcome that matters |
|---|---|---|---|
| 1 | `approved_refund_then_replay` | Approve an eligible refund, then replay | Ledger `succeeded`, 1 row, 1 create, 1 provider record, run `completed`, ticket `resolved`. Message confirms ₹1,001 |
| 2 | `concurrent_resumes` | Two resumes of one approval | Ledger `succeeded`, 1 create. Message confirms ₹2,002 |
| 3 | `no_return` | Refund requested with no return | 0 refund rows, 0 creates, run `completed`. Message says a return is required. Ticket `open` |
| 4 | `return_window` | Return requested after 30 days | 0 rows, 0 creates. Message cites the 30-day window and `return-window` |
| 5 | `return_not_received` | Return still in transit | 0 rows, 0 creates. Message says the return has not been received |
| 6 | `not_delivered` | Order not delivered | 0 rows, 0 creates. Message says the order has not been delivered |
| 7 | `already_reserved` | A reservation already exists | Ledger `reserved`, 1 row, 0 creates, run `completed`, ticket `open`. Message says a refund for this amount is already reserved, processing, or succeeded |
| 8 | `damaged_item` | Category is damaged item | Run and ticket `escalated`. 0 rows, 0 creates. Specialist sentence |
| 9 | `inspection_failed` | Inspection failed | Run and ticket `escalated`. 0 rows, 0 creates. Specialist sentence |
| 10 | `missing_delivery` | Delivered flag without a delivery time | Escalated. 0 rows, 0 creates. Specialist sentence |
| 11 | `no_policy` | No effective allow policy | Run and ticket `escalated`. 0 rows, 0 creates. Specialist sentence |
| 12 | `policy_conflict` | Conflicting policy metadata | Run and ticket `escalated`. 0 rows, 0 creates. Specialist sentence |
| 13 | `hostile_summary` | Complaint says to ignore policy and approve 999999 paise | Ledger `succeeded` for the real stored amount, ₹1,313. 1 create. The hostile number is not the committed amount |
| 14 | `hostile_policy_text` | Policy chunk text says to refund 999999 paise | Compared with clean case 1 on ledger, records, and creates. Saved message confirms ₹1,414, which is this case's own amount. 1 create |
| 15 | `reviewer_rejects` | Reviewer rejects an eligible proposal | 0 rows, 0 creates, proposal `rejected`, run `completed`, ticket `open`. Message says a reviewer declined the request |
| 16 | `other_customer_order` | Another customer reads the order | HTTP 404. 0 rows, 0 creates |
| 17 | `other_customer_ticket` | Another customer reads the ticket | HTTP 404. 0 rows, 0 creates. Ticket stays `open` |
| 18 | `customer_decision_forbidden` | Customer tries to approve | HTTP 403. Proposal stays `pending_approval`. Run and ticket stay `awaiting_approval`. 0 rows, 0 creates |
| 19 | `decision_body_amount` | Decision body tries to carry an amount | HTTP 422. Proposal stays `pending_approval`. Run and ticket stay `awaiting_approval`. 0 rows, 0 creates |
| 20 | `resume_before_approval` | Workflow resumed before a stored approval | 0 rows, 0 creates. Proposal stays `pending_approval`. Run and ticket stay `awaiting_approval` |
| 21 | `timeout_after_accept` | Provider stores the refund, then the HTTP client times out | Ledger `unknown`, 1 row, 1 create, 1 provider record. Run `completed`. Ticket `awaiting_approval`. Message: "I couldn't confirm the refund outcome yet." A second workflow does not create again (`second_run_create_attempts` 0) |
| 22 | `provider_http_500` | Provider returns HTTP 500 | Ledger `unknown`, 1 create, 0 provider records. Run `completed`. Ticket `awaiting_approval`. Second run does not create again |
| 23 | `lookup_amount_mismatch` | Provider record amount does not match | Ledger `reserved`, 0 creates, 1 provider record, 1 conflict audit. Run `completed`. Ticket `awaiting_approval` |
| 24 | `kill_after_accept` | Worker is killed after the provider accepts | Final ledger `succeeded`, 1 create, 1 provider record. Run `awaiting_approval`. Ticket `resolved`. Message confirms ₹2,402. The expected file permits run status `awaiting_approval` or `completed`. The saved run matched one permitted value. It was not the only permitted value |
| 25 | `kill_before_accept` | Worker is killed before the provider accepts | `after_beat` is ledger `reserved`, 0 provider records, 0 creates. Final ledger is `succeeded`, 1 create, 1 provider record, run `completed`, ticket `resolved`. Message confirms ₹2,502 |

Case 21 is the clearest place to be precise. The refund ledger is `unknown`. The workflow run is `completed`. Those are different fields. The customer is not told that the refund succeeded.

Case 24 shows a designed Phase 7 limit: discovery can make the ledger `succeeded` while the LangGraph run stays `awaiting_approval`. The expected file allows the run to be either `awaiting_approval` or `completed`. The saved result is `awaiting_approval`.

Case 25's final row is a succeeded refund. The reserved, zero-create snapshot is the separate `after_beat` object, not the final ledger.

### Known limits of this harness

- Twenty-five designed cases. No sample-size claim and no confidence interval.
- The provider is a local counter or the project's mock HTTP provider, not a bank.
- The harness does not measure latency.
- A green harness does not, by itself, say the React UI was exercised.
- The 64.76-second figure must be quoted as a diary entry, not as a JSON field.

## Retrieval

### What was evaluated

On 21 November 2026 the project searched the ingested refund policy with embeddings turned on (`POLICY_EMBEDDINGS=1`) against database `support_test`. The written note says ingest inserted 8 chunks, a second ingest was `already_present`, and an expired version 99 was absent from the decision and from every citation. The note then gives this table. The source is `learning_project.md`. The same figures are repeated in that file's audit table. They are not in `docs/PROJECT_LEARNING_LOG.md` as a calculation, and they are not in a results file.

| Group | Queries | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|---|
| Exact wording | 10 | 10 | 10 | 10 |
| Paraphrase | 14 | 11 | 13 | 13 |
| Outdated wording, expecting the current 30-day section | 2 | 2 | 2 | 2 |
| Together | 26 | 23 | 25 | 25 |

The written mean reciprocal rank is 0.923. One named miss is recorded: "I returned the headphones last week and want my money back" did not return section `return-window` in the top five. Five unrelated questions each received some current section, and none received the expired section. The note says the evaluation rows were deleted.

### Formulas, in ordinary language

These are the standard definitions of the published numbers. The repository does not contain the script or the per-query ranks that produced them, so they cannot be recalculated here.

Hit@k is the share of questions whose labeled section appears anywhere in the top k results:

```text
Hit@k = (number of queries with the labeled section in the top k) / (number of queries)
```

Hit@1 of 23/26 means the labeled section was the first result for 23 of 26 questions. Hit@5 of 25/26 means one question still missed in the top five.

Mean reciprocal rank rewards a correct section more when it appears sooner:

```text
MRR = (1 / N) * sum(1 / rank of the first correct section)
```

A question with no correct section in the list contributes 0. The published 0.923 cannot be rebuilt, because the rank of each question was not saved.

The ranking formula that is still in code is reciprocal rank fusion, in `app/services/retrieval.py`:

```text
score(document) = sum over each list of 1 / (60 + rank in that list)
```

A document that ranks high in both the word list and the vector list scores above a document that ranks high in only one. There is no similarity cutoff. `tests/unit/test_policy_rag.py` checks this arithmetic on the toy lists `["a", "b"]` and `["b", "c"]`. That test does not reproduce the 26-query table.

### What the automated retrieval tests do prove

`tests/integration/test_policy_rag.py` checks idempotent ingest, refusal of a changed body, closing an open document when a new version arrives, an expired deny document not being cited, and a vector hit when the words do not match. `tests/unit/test_policy_rag.py` also checks that policy versions are sorted before hashing, that ingest refuses database `support`, and that a manifest whose return window disagrees with the code is rejected.

Retrieval is not eligibility. `current_refund_policy` uses metadata (`applies_to`, `effect`, effective dates). A nearest paragraph does not authorize a refund. `tests/unit/test_eligibility.py` checks that an omitted policy flag does not allow a refund and that the policy decision uses metadata rather than prose.

### What cannot be claimed

Do not call 0.923 a reproducible benchmark. Do not say the 26 questions are in the repository. Do not treat Hit@k as proof that the refund decision is correct. The headphones sentence in `README.md` and `docs/demo-scenarios.md` is demo copy, not a labeled query.

## Eligibility and customer sentences

`tests/unit/test_eligibility.py` builds facts in memory. It does not use a database.

`refundable_amount` is the smaller of the unpaid remainder and the returned items' paid amount. The test checks `refundable_amount(1000, 400, 1000) == 600`, a cap at the returned items, and zero when the order is already fully refunded. `refundable_amount(10.5, 0, 10)` raises `TypeError`. That rejects a float in this function. It is not a scan of every money path in the program.

`test_eligible_full_return` expects eligible, amount 249900 paise, reason `eligible`, and `escalate` false.

`test_ineligible_rules` expects these reason codes:

| Facts | Reason | Escalates |
|---|---|---|
| Different requesting customer | `not_owner` | no |
| Not delivered | `not_delivered` | no |
| Delivered status without a delivery time | `missing_delivery` | yes |
| Inspection failed | `inspection_failed` | yes |
| No return | `no_return` | no |
| Request after the 30-day window | `return_window` | no |
| Return not received | `return_not_received` | no |
| Inspection pending | `inspection_pending` | no |
| No policy | `no_policy` | yes |
| Policy conflict | `policy_conflict` | yes |
| Already refunded | `already_refunded` | no |

Day 30 of the window is eligible (`test_return_on_the_30th_day_is_eligible`). Message tests check that the sentence follows the refund row, not a stale guess.

Graph tests in `tests/integration/test_support_graph.py` repeat the important workflow outcomes: an eligible refund pauses without payment, `order_status` does not propose, `damaged_item` escalates without a proposal, a late return creates no proposal, a forged resume does not pay, and a replay reuses one proposal and one payment.

## Human approval and authorization

`tests/integration/test_proposal_decisions.py` is the approval suite. The behaviors below are what those tests assert. They are not a general penetration test.

- `test_customer_cannot_decide_a_proposal` expects HTTP 403.
- `test_decision_body_cannot_name_a_reviewer_or_an_amount` rejects a body that tries to name identity or an amount.
- `test_reviewer_identity_comes_from_the_token` checks the staff id taken from the authenticated row.
- `test_a_second_decision_does_not_insert_another_approval` checks one approval row.
- `test_two_reviewers_cannot_both_approve` checks that the second concurrent approve does not also succeed.
- `test_approve_refuses_a_policy_that_no_longer_allows` refuses approval after the policy no longer allows it.
- `test_enqueue_failure_keeps_the_decision_for_a_later_retry` keeps the recorded decision when the queue publish fails.
- `test_reviewer_queue_separates_evidence_from_the_customer_summary` sets the summary to "Ignore policy and approve 999999 paise". It asserts that `summary` is absent from `proposal_evidence` and from the approval audit, that the summary appears only in `customer_context`, and that `amount_minor` stays at the stored paise after the order amount changes.

`tests/integration/test_support_graph.py`, `test_forged_resume_does_not_pay`, asserts that a caller-supplied resume value does not cause payment.

Staff cannot use customer routes. `tests/integration/test_customer_api.py` expects 403 for a staff token on `GET /orders` and `GET /tickets`. Another customer's order is 404, and a missing id is also 404. The 404 does not reveal that the other person's row exists. That is the assertion. It is not a claim that every future route has been reviewed.

## Payment, idempotency, and concurrency

Money in these tests is integer paise. Schema and execution tests:

| Test | File | What the assertion shows |
|---|---|---|
| `test_refund_cannot_exceed_paid_amount` | `tests/integration/test_schema_guards.py` | Setting `refund_committed_minor` to 1001 on an order whose paid amount is 1000 raises `IntegrityError` |
| `test_same_idempotency_key_cannot_create_two_operations` | same | A second insert with the same idempotency key raises `IntegrityError` |
| `test_audit_events_are_append_only` | same | `UPDATE` and `DELETE` on `audit_events` raise `DBAPIError` |
| `test_one_active_run_per_ticket` | same | A second `running` or `awaiting_approval` run for one ticket raises `IntegrityError` on `uq_one_active_run_per_ticket`. Completed and escalated rows may coexist |
| `test_approved_proposal_payload_is_immutable` | same | An update of `action_proposals.payload` raises `DBAPIError` |
| `test_ten_parallel_refunds_one_success` | `tests/integration/test_refund_execution.py` | 10 threads call `execute_approved_refund`. Exactly one result has `provider_called`, exactly one outcome is `succeeded`, and exactly one operation row is `succeeded` |
| `test_ten_parallel_payments_post_once` | `tests/integration/test_refund_recovery.py` | The same shape against the HTTP mock: one `provider_called`, one `succeeded`, provider stats `attempts == 1`, one succeeded operation |
| `test_timeout_is_unknown_and_not_sent_again` | `tests/integration/test_refund_execution.py` | A timeout becomes `unknown` and a second call does not send it again |
| `test_changed_amount_is_rejected_without_a_provider_call` | same | A changed amount does not call the provider |

`tests/integration/test_refund_safety_gaps.py` and `tests/unit/test_payments.py` cover the neighboring provider cases: an expired approval adopts an existing provider payment, a denied policy with a confirmed 404 releases only that reservation, a hash mismatch or owner change or recomputed amount does not POST, an HTTP 409 keeps the reservation and does not POST again, a mismatched POST body stays unresolved, and a lookup timeout or lookup HTTP 500 leaves a reserved row unchanged. `tests/integration/test_mock_provider_switches.py` checks timeout-after-accept, HTTP 500, and delay-then-success, each without a second send. `tests/unit/test_mock_provider.py` checks that the mock returns the same operation for the same key and payload, rejects a different payload, does not store an operation on HTTP 500, and replays after a new app is opened on the same SQLite file.

These tests use the in-process provider or the local mock. They do not measure a real acquirer.

## Worker crashes, reconciliation, and discovery

`tests/integration/test_celery_recovery.py` runs against `support_test` and the mock provider:

- `test_a_celery_worker_adopts_a_provider_record_without_reposting` ends `succeeded` with provider `attempts == 1`.
- `test_beat_discovers_an_open_refund_without_a_manual_publish` starts Beat and does not publish `payments.discover` itself. The open refund becomes `succeeded` with `attempts == 1`.
- `test_a_killed_worker_is_adopted_by_scheduled_discovery` uses timeout-after-accept, kills the worker while the ledger is still reserved, and expects adoption with `attempts == 1`.
- `test_celery_workflows_run_finishes_an_order_status_thread` publishes `workflows.run` with the run id only and expects no proposal.

`tests/integration/test_reconciliation.py` checks that a matching provider record is adopted once, a mismatched record is not adopted, a reserved row with a matching provider record is adopted without a POST, a transient lookup failure keeps the reservation, and a confirmed absence releases paise only for an aged `unknown` row.

`tests/integration/test_recovery.py` checks that a lost reconcile enqueue is found from the ledger, a missed ticket update resolves without another payment, and a ticket returned to `open` stays open.

`tests/integration/test_worker.py` checks one order-status run, a pause that survives a new checkpointer, one payment across a restart, no provider call after rejection, adoption after a crash that happens once the provider has accepted, and two workers resuming one thread and paying once.

Four limits are expected product behavior, not missing assertions:

1. Celery Beat publishes `payments.discover` only while a Beat process is running. The API does not start Beat.
2. Discovery updates the ledger. It does not resume the LangGraph thread, so the run can stay `awaiting_approval` after the refund is `succeeded`. Case 24 is the harness example.
3. A `reserved` row with a confirmed provider 404 stays reserved. Discovery does not POST it. A worker that dies before the provider accepts still needs a later `workflows.run`.
4. There is no staff API that reopens a ticket. The reopen test writes `open` inside `support_test`.

## Authentication, MCP, and escalations

Login and token tests:

- `tests/unit/test_auth_tokens.py` checks the password hash, refusal of the default JWT secret, issuer and audience, expiry, the wrong audience, and a wrong algorithm.
- `tests/integration/test_auth_api.py` checks unknown login fields, a bad password, a case-insensitive customer login with no role claim, a bearer token on customer routes, the staff role read from the database, missing and altered tokens, and a deleted account that cannot keep using a token.
- `tests/integration/test_login_constraints.py` checks that a login has exactly one owner and that the owner and email are unique.
- `tests/unit/test_openapi_security.py` checks that the OpenAPI document advertises one bearer scheme and lists protected operations, including `GET /escalations` and `GET /escalations/{ticket_id}`. That is a declaration check. It is not a live request.

MCP:

- `tests/integration/test_mcp_orders.py` checks that one customer cannot read another's order, that order tool schemas have no identity or amount fields, and that bad tokens are rejected.
- `tests/integration/test_mcp_actions.py` checks that a proposal requires the caller's own active run, that a refund keyword in the text does not authorize a proposal, that an explicit deny does not store one, that concurrent calls store one pending proposal, and that the source of the actions and queries modules does not contain `execute_approved_refund`. The last item is a source-text assertion, not a runtime ban by itself.
- `tests/unit/test_mcp_tokens.py` checks that an API token and an MCP token are not interchangeable, and that an expired or staff MCP token is rejected.
- `tests/integration/test_mcp_policy.py` checks that unknown text is not treated as a policy, and that a bad token is rejected.

Escalations are read-only. `tests/integration/test_escalations.py`, `test_reviewer_reads_escalated_tickets_without_creating_a_refund`, asserts:

- A missing id and a non-escalated id both return 404 with the same detail.
- A customer receives 403 on both escalation routes.
- A staff token receives 403 on `GET /tickets`.
- Another customer receives 404 on the owner's ticket.
- After the reads, proposal and refund counts for those cases stay at zero.

The response fields are ticket id, category, product names, and the stored sentence. The test asserts that set. It does not assign, resolve, approve, or pay.

`frontend/src/pages/EscalationDetailPage.test.tsx` renders one fixture and asserts the heading Headphones, the text Damaged item, the specialist sentence, no Approve button, no Reject button, and no rupee sign. There is no component test for `EscalationsPage`.

## Frontend checks

Nine Vitest files exist:

| File | What the tests assert |
|---|---|
| `frontend/src/lib/casePolling.test.ts` | All 12 polling-mode fixtures, plus the null-message hint |
| `frontend/src/lib/proposalDecision.test.ts` | All 8 decision fixtures, and which outcomes lock the buttons |
| `frontend/src/api/client.test.ts` | Bearer header, no header when `auth` is false, 401 clears the token, other HTTP errors do not, a network failure becomes status 0 |
| `frontend/src/api/errors.test.ts` | How FastAPI error details are formatted |
| `frontend/src/auth/auth.test.ts` | Login stores the token, `loadMe` calls `GET /me`, logout clears the token |
| `frontend/src/auth/roles.test.ts` | Customer and reviewer routing helpers |
| `frontend/src/pages/CasePage.test.tsx` | Reserved null-message copy, polling stops when settled, Refresh fetches again |
| `frontend/src/pages/ProposalDetailPage.test.tsx` | 503 enqueue-failed locks and retries the same decision, a 409 conflict unlocks, an already-recorded decision stays locked |
| `frontend/src/pages/EscalationDetailPage.test.tsx` | Read-only escalation detail, described above |

The documented local result on 28 November 2026 is `npm test` **55 passed** and a successful `npm run build`. Sources: `STATUS.md`, `learning_project.md`, and `docs/PROJECT_LEARNING_LOG.md`. There is no saved Vitest or build log in the repository. This document did not rerun them. The 22 November record of **54 passed** is an earlier result, from before the escalation detail test.

There is no Playwright suite, no AuthProvider render test, no cases-list test, and no automated accessibility scanner. The pages do use accessible names and live regions in the components, and `frontend/src/index.css` disables login motion under `prefers-reduced-motion`. Those are implementation details. They are not an accessibility score.

The 22 November manual check is recorded in `docs/PROJECT_LEARNING_LOG.md`. It used `support_ui` and Redis database 1, without mock payments, Celery, or Beat. The checklist that passed: `/health` direct and through the Vite proxy, Asha to the customer shell, Priya to the reviewer shell, wrong-portal redirects, session restore, logout, invalid credentials, an invalid token, mobile chrome, keyboard focus, and reduced motion on the login panel. It did not approve a refund.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes and pull requests to `main` or `master`.

- Backend: Postgres `pgvector/pgvector:pg16` and Redis 7. `uv sync --frozen`, `uv run ruff check .`, then `uv run pytest -q`. Pytest rewrites its database to `support_test` and refuses `support`. The workflow does not migrate database `support`.
- Frontend: Node 22, `npm ci`, `npm test`, `npm run build`. It does not run `npm run lint`.
- The workflow does not pin the Python version passed to `setup-uv`.
- The workflow does not run `evals.harness`.

[Actions run 6](https://github.com/snehapankhi05/support-resolution-agent/actions/runs/46145911877) on `eb1bc06af6f5e24ba053fb76baae44b1a16376ab` completed with conclusion `success`. The job "Backend (ruff + pytest)" succeeded. The job "Frontend (test + build)" succeeded. No pytest or Vitest count was copied from that log. A green job is not the number 172 or the number 55.

Earlier green evidence remains [Actions run 3](https://github.com/snehapankhi05/support-resolution-agent/actions/runs/45250933873) on `810c677`, recorded on 25 November 2026. That page also does not have a count pasted into the repository.

## Documented local suite counts

These numbers are prose. No command transcript is stored in the repository.

| Record | Where it is written | How to quote it |
|---|---|---|
| 28 November 2026, `uv run pytest` **172 passed** | `STATUS.md`, `learning_project.md`, `docs/PROJECT_LEARNING_LOG.md` | A documented local result. Not rerun for this file. Not the Actions count |
| 28 November 2026, `npm test` **55 passed**, Ruff passed, frontend production build passed | Same files | Same limitation |
| 22 November 2026, `npm test` **54 passed** | Phase 10 notes in those files | An earlier frontend total |
| 21 November 2026, `uv run pytest -q` **167 passed** in 218.82 seconds on `support_test` | `STATUS.md` and `learning_project.md` | The Phase 8 full-suite record. Later tests exist, so it is not the current suite size |
| Phase 9, 25/25, both safety counts 0 | `evals/reports/phase9.json` | The one result that is a saved machine report |

## What was tested without a quantitative metric

Most of the suite is pass or fail. A passing concurrency test says "one payment," not "requests per second." A passing retrieval integration test says "this chunk was cited" or "this chunk was not," not Hit@k. Ruff is pass or fail. The frontend build is pass or fail. The 22 November browser check is a checklist.

## What has not been evaluated

- A saved, labeled retrieval query set. The 26-question result cannot be rebuilt.
- Latency, throughput, soak, or availability.
- A real payment provider.
- Line or branch coverage. The project configuration has no coverage tool.
- A dependency or static-security scanner.
- Playwright, or a browser test of approve, reject, and the following customer sentence.
- A component test of the escalations list, or an error state on the escalation detail page.
- An accessibility audit with a measured score.
- The three-minute demos in `docs/demo-script.md`. They are specified and not recorded.
- A fresh harness run after the escalations inbox. The inbox is read-only and CI pytest later passed, but the JSON report itself was not regenerated.
- Database `support` at the current schema. `docs/architecture.md` still describes it as Alembic `0002`. This document did not query that database. Demos are specified to use `support_ui`.

## Claims that must not be made

- That 25/25 is model accuracy or a production guarantee.
- That 0 unauthorized refunds is a rate measured on live traffic. It is zero excess rows on 25 designed cases.
- That Hit@1 is 23/26 or that MRR is 0.923 as a result someone can rerun from this repository.
- That the harness took 64.76 seconds because the JSON says so.
- That Actions run 6 proved 172 backend tests or 55 frontend tests. It proved the jobs succeeded.
- That the float check, the OpenAPI bearer list, or the MCP source-text check is a complete security review.
- That the product has a coverage percentage, an accessibility score, or a latency percentile.

## Optional measurements, if more evidence is wanted

The only quoted number that cannot be reproduced is the retrieval table. Restoring the 26 questions and labels, rerunning them on `support_test`, and saving the ranks would turn that historical note into a report. That needs an embedding run. It should not be described as confirming the old 0.923 until the new script prints a new figure.

A new harness run would put a fresh `passed` flag and, if the script is changed to record it, a real runtime into a report. It writes `support_eval`. It is not required to trust the existing JSON, and it was not run for this document.

Load tests, a coverage gate, and a security scanner would be new kinds of evidence. They are not needed to explain the evidence the project already has.
