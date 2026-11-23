# Learning project

This is the standing record of the project, what the code actually does, and the remaining roadmap. The chronological notes stay in `docs/PROJECT_LEARNING_LOG.md`. Early sections of that log describe the project as it was at the time. Where they disagree with this file, this file is the later review.

Rewritten against the code on 21 November 2026. No application code, tests, migrations, or databases were changed for this rewrite.

Learning step numbers and implementation phase numbers are different series. Steps 0–6 are the original learning steps and are complete. Steps 7 and 8 in the original plan were the workflow and the human approval. Those were built as implementation Phases 0–6, together with the payment-response check, `POST /runs`, checkpoints, and safe resume. Original Steps 9–12 map to Phases 7–12 below. Phases 7, 8, and 9 are complete. Phase 7 still has four operational limitations. Phase 9 scored 25/25 on database `support_eval`.

## What this project is

An approval-gated support workflow for one fictional electronics store. A customer opens a ticket. A deterministic workflow reads the order, the return, and the refund state. Python decides eligibility and the amount in integer paise. The workflow may store one refund proposal. A human reviewer approves or rejects that proposal. Ordinary Python then reserves the paise, looks up `refund-{proposal_id}`, and posts only when the provider has no record and the stored approval still matches. The customer is told the verified status.

The financial ledger is Postgres: `orders.refund_committed_minor` and `refund_operations`. LangGraph checkpoints live in schema `langgraph` and store only where the workflow paused. Policy citations do not authorize a refund. The three MCP servers can read orders and returns, search policy, and store a proposal. They cannot approve or pay. `execute_approved_refund` is not an HTTP route and not an MCP tool. The payment provider is a local mock.

There is one graph and no model on the payment path. ADR 0003 still says an LLM classifies and drafts. That sentence is older than the approved design. The running workflow does not classify, choose an amount, approve, or pay.

## How the pieces connect

- **Postgres.** Business tables, the refund ledger, audit events, and policy chunks. `policy_chunks.embedding` is `vector(384)`. Migrations: `0001` schema, `0002` login accounts, `0003` one pending proposal per run and action, `0004` one active run per ticket, `0005` nullable `tickets.resolution_message`. Pytest applies them only to `support_test` and refuses database `support`. A read during the Phase 8 implementation showed `support_test` on Alembic `0005`. The earlier same-day read showed `support` on Alembic `0002` with no checkpoint tables. This Phase 8 record did not query either database.
- **Eligibility.** `evaluate_refund_eligibility` enforces delivery, ownership, the 30-day window, a received and passed return, and the refundable balance. It does not read the database.
- **Policy permission.** `current_refund_policy` uses metadata from documents effective today. Only `applies_to=refund` and `effect=allow` permits a proposal. Chunk text is not an input. `search_policy` returns citations and does not set permission.
- **Workflow.** `app/workflows/support_graph.py` is one `StateGraph`. The ticket category selects the branch. `order_status` explains and does not propose. `damaged_item` escalates. `refund_request` calls `create_refund_proposal` in-process. The graph does not call the MCP servers and does not store citations on the proposal. `interrupt()` pauses. The value passed to `Command(resume=...)` is discarded.
- **Human in the loop.** A person decides only a pending refund proposal: `POST /proposals/{id}/decision` is `approve` or `reject`, then code calls `execute_approved_refund` after the stored approval. An escalated case is a finished customer outcome. `damaged_item`, failed inspection, missing delivery, and missing or conflicting policy set the ticket and the run to `escalated`, store “A support specialist will review your case.”, and create no proposal. There is no staff route that lists those tickets. Priya cannot open `GET /tickets` (403). The reviewer queue stays empty because it lists only `pending_approval` refund proposals. Ineligible cases that do not escalate (not delivered, return not received, outside the window, already refunded) also create no proposal and no staff task. Do not put escalated tickets on the refund queue: they have no approved amount.
- **Runs.** `POST /runs` stores one `running` row for the caller's ticket and enqueues Celery task `workflows.run` with the run id. A second call returns the active run.
- **Approval.** `POST /proposals/{id}/decision` accepts only `approve` or `reject`. The staff id comes from the token's database row. After the commit, the same run id is queued. A duplicate decision is 409. While the run is still `awaiting_approval`, that 409 queues the run again. The worker resumes only when the proposal and `approvals` rows agree.
- **Execution.** Reserve with `SELECT … FOR UPDATE`, commit, look up the idempotency key, then POST. A provider POST is accepted only when the body repeats the reserved amount and order. A timeout or a mismatched body becomes `unknown` and keeps the reserved paise. Execution does not release `unknown` on a confirmed 404.
- **Reconciliation.** `reconcile_operation` looks up the stored key and never calls `create_refund`. A matching record is copied onto `reserved`, `unknown`, `submitted`, or `processing`. A mismatched amount or order is left unchanged. An `unknown` row becomes `failed` and its reservation is released only after a confirmed 404 and a two-minute age. `reserved`, `submitted`, and `processing` keep their reservation when the provider has no record.
- **Recovery.** Saving an open refund status publishes `payments.reconcile` after the commit. A broker failure writes `refund_reconcile_enqueue_failed` and leaves the row open. `payments.discover` and every worker pass scan `reserved`, `unknown`, `submitted`, and `processing`, then publish those ids again. A matching provider record is copied onto a `reserved` row. A 404 does not release that reservation and does not POST. `retry_failed_ticket_updates` resolves a ticket only when `ticket_update_failed` is newer than any `ticket_update_retried` audit for that proposal. It does not call the provider.
- **Customer text.** A refund sentence comes from the refund row. Ineligible, order-status, rejected, and escalated sentences are the code text stored in `tickets.resolution_message`. `GET /tickets` uses the refund row when one exists, and otherwise uses that stored sentence. The customer summary is not the sentence.
- **Product names.** `product_names` on customer orders, customer tickets, and reviewer proposals is display text loaded from `order_items.product_name`. It is not read by eligibility, not stored in the proposal payload, and not part of the approval hash. The customer summary is not a product name. Seed indexes 0–2 stay Headphones, Smart watch, and Keyboard. Indexes 3–29 use `DISPLAY_NAMES` in `scripts/seed.py` (`sku-3` Desk lamp through `sku-29` Mini projector). On 22 November 2026 those 27 `support_ui` rows were renamed in place. Product ids, prices, orders, cases, and refund rows were not rewritten. Running `scripts.seed` still deletes and recreates seed customers and their business rows.

## Step 6 — Policy RAG

Retrieval and permission are different jobs. A nearest paragraph is a citation. It is not evidence that a refund is allowed. The approval is bound to `policy_effect` and to the list of effective policy versions.

`policies/refund_policy_v1.md` explains the rules. `policies/refund_policy_v1.json` sets `policy_key`, version, dates, `applies_to=refund`, `effect=allow`, and `return_window_days=30`. Ingest checks that window against `RETURN_WINDOW`. Documents count only when `effective_from` is today or earlier and `effective_to` is empty or still ahead. The same version is not rewritten. Citation text is not in the hash.

- Model: `BAAI/bge-small-en-v1.5` through `fastembed`, 384 dimensions.
- Tests use `StubEmbedder` and do not download weights.
- Live search is lexical unless `POLICY_EMBEDDINGS=1`.
- Fusion is reciprocal rank fusion, `1 / (60 + rank)`. There is no similarity cutoff, no BM25 extension, and no cross-encoder.

Live evaluation on 21 November 2026, `POLICY_EMBEDDINGS=1`, database `support_test` only. Ingest inserted 8 chunks. The second run was `already_present`. The expired version 99 was absent from the decision and from every citation.

| Group | Queries | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|---|
| Exact wording | 10 | 10 | 10 | 10 |
| Paraphrase | 14 | 11 | 13 | 13 |
| Outdated wording, expecting the current 30-day section | 2 | 2 | 2 | 2 |
| Together | 26 | 23 | 25 | 25 |

That is Hit@1 23/26, Hit@3 25/26, Hit@5 25/26. MRR on those 26 queries was 0.923. “I returned the headphones last week and want my money back” did not return `return-window` in the top five. Five unrelated questions each received some current section. None received the expired section. The evaluation rows were deleted.

The full suite recorded at the end of Step 6 was **119 passed**. After Phase 7 it was **164 passed**. After Phase 8, on 21 November 2026, `uv run pytest -q` recorded **167 passed** in 218.82 seconds on `support_test`. This documentation update did not rerun it.

## Completed learning steps and phases

| Learning step | Implementation | Evidence in the repository |
|---|---|---|
| 0 Scope | Docs, ADRs, agent rules | `docs/`, `AGENTS.md`, `.cursor/rules/` |
| 1 Foundation | Postgres on host port 5433, Redis, Alembic `0001` | `docker-compose.yml`, `migrations/versions/0001_initial_schema.py` |
| 2 Deterministic core | Models, seed, eligibility, reserve-then-call | `app/services/eligibility.py`, `app/services/refunds.py` |
| 3 Mock provider | Lookup before POST, adopt a match, `unknown` keeps the reservation on the execution path | `mock_services/payments/`, `app/services/payments.py` |
| 4 API and login | Argon2, JWT, own orders and tickets, 404 for someone else's rows, staff 403 on customer routes | `app/api/auth.py`, `app/api/customer.py` |
| 5 MCP | Orders, policy, and actions on `127.0.0.1` ports 8101–8103. No `customer_id` in tool schemas. No execute tool | `app/mcp_servers/` |
| 6 Policy RAG | Effective-dated retrieval, RRF. Library ingest refuses `support`. CLI allows `support_test` and `support_ui`, refuses `support` and `support_eval` | `app/services/retrieval.py`, `scripts/ingest_policies.py` |
| 7 and 8, built as Phases 0–6 | See the phase table below | Workflow, approval route, worker, reconciler |

### Phases 0–6

These are complete. Do not rebuild them.

| Phase | What is in the code | Tests that exercise it |
|---|---|---|
| 0 | `docs/architecture.md` describes the one graph, the ledger, and the reconciler | Documentation only |
| 1 | A provider POST is accepted only when the body repeats the reserved amount and order. A mismatch becomes `unknown` and keeps the reservation | `tests/unit/test_payments.py`, `tests/integration/test_refund_safety_gaps.py` |
| 2 | Deterministic graph. Category comes from the ticket. Resume value is ignored | `tests/integration/test_support_graph.py` |
| 3 | `POST /runs` and migration `0004`, one active run per ticket | `tests/integration/test_runs.py`, `tests/integration/test_schema_guards.py` |
| 4 | Celery task `workflows.run`. `PostgresSaver` uses schema `langgraph` and refuses database `support` | `tests/integration/test_worker.py`, `tests/unit/test_checkpoints.py`. Queue publish is mocked. A live worker was not part of this phase |
| 5 | After a committed decision, the run id is queued. Resume reads `approvals`. Rejection pays nothing. A second task does not pay again | `tests/integration/test_proposal_decisions.py`, `tests/integration/test_worker.py` |
| 6 | `payments.reconcile` looks up the stored key and does not POST. Match is copied. Aged `unknown` plus a confirmed 404 becomes `failed`. `submitted` and `processing` keep a reservation on a 404 | `tests/integration/test_reconciliation.py` |

## Phase 7 — complete, this is original Step 9

Original Step 9 is workers and recovery. The completion criterion is: a timeout followed by a restart produces no second refund. That case is now an automated test on `support_test`.

Completed on 21 November 2026. `uv run pytest -q` recorded **164 passed** in 193.16 seconds. Nothing was written to database `support`.

What this phase added:

- Celery tasks `workflows.run`, `payments.reconcile`, and `payments.discover`. Each argument is an id. The worker reads amounts and approvals from Postgres.
- After an open refund status commits, the code publishes `payments.reconcile`. A broker failure writes `refund_reconcile_enqueue_failed` and leaves the row open. The next ledger scan publishes it again (`tests/integration/test_recovery.py`).
- Celery Beat entry `discover-open-refunds` publishes `payments.discover` every 60 seconds on queue `celery`. `RECONCILE_DISCOVER_SECONDS` must be greater than zero. `RECONCILE_QUEUE` changes the queue. Both are read when the Celery app is imported. The schedule carries no amount and no approval. `test_beat_discovers_an_open_refund_without_a_manual_publish` starts Beat and a worker and does not publish the discover task itself.
- `payments.discover` retries the ticket write, then scans `reserved`, `unknown`, `submitted`, and `processing`. `payments.reconcile` only looks up the stored key. A matching provider record is copied onto those statuses. A confirmed 404 releases paise only for an aged `unknown` row. `reserved`, `submitted`, and `processing` keep the reservation.
- A real worker adopts one seeded provider refund without a second POST (`test_a_celery_worker_adopts_a_provider_record_without_reposting`).
- `test_a_killed_worker_is_adopted_by_scheduled_discovery` runs `workflows.run` with the mock `timeout_after_accept` mode. The provider stores one refund. The worker process is killed while the ledger is still `reserved`. Beat then publishes discovery, and the restarted worker adopts that same row. The provider stays at one create, one `refund_operations` row remains, and the committed paise stay at the reserved amount.
- The same crash inside one process is adopted by the next `execute_workflow` (`tests/integration/test_worker.py`). Two in-process workers resuming one approved thread produce one provider call and one `succeeded` row.
- `test_celery_workflows_run_finishes_an_order_status_thread` publishes `workflows.run` with only the run id. A live worker finishes that order-status thread and creates no proposal.
- `retry_failed_ticket_updates` resolves a ticket only when `ticket_update_failed` is newer than any `ticket_update_retried` audit for that proposal. A second pass does not write another retry. It does not call the provider or change paise. A ticket set back to `open` after that retry stays open (`tests/integration/test_recovery.py`).
- Audit events include `refund_provider_result`, `refund_reconcile_absent`, `refund_reconcile_lookup_failed`, `refund_reconcile_enqueue_failed`, `refund_payload_conflict`, `ticket_update_failed`, and `ticket_update_retried`.

Four operational limitations remain:

1. Celery Beat publishes `payments.discover` every 60 seconds only while a Beat process is running. The API does not start that process.
2. Discovery adopts the ledger row. It does not resume the LangGraph thread, so the run can stay `awaiting_approval` after the refund is `succeeded`.
3. A `reserved` row with a confirmed 404 stays reserved. Discovery does not POST it. A worker that dies before the provider accepts still needs a later `workflows.run` to submit that reservation.
4. There is no staff API that reopens a ticket. The reopen test writes `open` in `support_test`.

## Numbering

| Original learning step | Implementation phase | State |
|---|---|---|
| 0–6 | Built before the phase sequence | Complete |
| 7 Workflow and 8 Human approval | Phases 0–6 | Complete. Do not rebuild |
| 9 Workers and recovery | Phase 7 | Complete. Four operational limitations are listed in the Phase 7 section |
| Reviewer queue and customer sentences | Phase 8 | Complete. Recorded below |
| 10 Evaluation | Phase 9 | Complete. Recorded below. Evidence in `evals/reports/phase9.json` |
| 12 Frontend | Phase 10 | **Essential complete** 22 Nov 2026. Customer home, orders, cases list, new case, case detail; reviewer overview, queue, proposal detail. Product names are display-only. Vitest last observed at 54 passed. Deferred: AuthProvider RTL, Playwright, order-detail page, selected-product line under the new-case dropdown, P6 analytics |
| 11 Extensions, choose two | Phase 11 | Not started. Optional for a safe core. Langfuse, and a desktop client on the Orders MCP server only. Off the payment path |
| 12 Release work | Phase 12 | **Partial** 22 Nov 2026. README, demo script, ADR 0003 note, and GitHub Actions workflow are in the tree. Git init, remote push, a fresh full pytest, and a green Actions run are still pending. Demo video not recorded |

## Final roadmap

Phases 0–9 are complete. Phase 10 essential UI is complete. Phase 11 is not started. Phase 12 is partial: the release docs and CI workflow are written, and git, a full test run, and a green Actions check are not.

Prioritized next work, after this documentation pass:

1. **Finish Phase 12 verification.** `git init`, push to a remote, rerun `uv run pytest` on `support_test`, and confirm the Actions workflow is green. Then record Demos A/B/C if wanted. Depends on no further product change. Acceptance: the repo has a remote, CI is green, one succeeded refund, one blocked request with zero refunds, and one timeout recovered to a single provider record.
2. **Specialist escalation queue, only with a new approval.** A read-only staff list of `escalated` tickets: category, catalog product name, and the verified sentence. No amount field, no approve or reject, no call to `execute_approved_refund`. Another customer’s ticket stays 404. Depends on leaving the refund queue as pending proposals only. Acceptance: an escalated case is visible to a reviewer and still has zero proposals and zero refund operations.
3. **Optional Phase 10 leftovers.** A confirmation under the new-case dropdown (product name and short order id). AuthProvider and route-guard RTL. Playwright. A dedicated order-detail page. None of these change refund logic.
4. **Optional Phase 11.** Langfuse tracing with no payment authority, and a desktop client attached only to the Orders MCP server. Neither may import `execute_approved_refund` or the decision route.

### Phase 8 — complete, reviewer queue and customer sentences

Completed on 21 November 2026. The purpose is to let a reviewer see a pending proposal without pasting an id, and to let `GET /tickets/{id}` return the sentence the workflow already decided. The React console was not part of Phase 8. It was built in Phase 10.

**Design.** `proposal_evidence` and `customer_context` are separate objects. Evidence is copied from the stored proposal payload: `order_id`, `amount_minor`, `policy_effect`, `policy_versions`, plus status, creation time, and `payload_hash`. The read does not recompute eligibility and does not search policy. `customer_context` is `{ ticket_id, summary }`. The summary is the customer's complaint, returned as plain text. It is not hashed, not passed to eligibility, and not sent into approval or payment. Any reviewer or admin can see every pending proposal. There is no assignment column. Citations stay off this response.

**Database.** Migration `0005` adds nullable `tickets.resolution_message`. Migrations `0001`–`0004` were not edited. Pytest applied `0005` to `support_test`. `support` was not upgraded.

**APIs.** `GET /proposals` lists `pending_approval` refund proposals. `GET /proposals/{id}` reads one. `POST /proposals/{id}/decision` still accepts only `approve` or `reject`. No execute route and no new MCP tool were added.

**Customer messages.** The graph writes the code sentence for an ineligible, order-status, rejected, or escalated finish. It does not write the ticket summary and it does not write a refund sentence into that column. `GET /tickets` returns the refund-row sentence when a refund exists, including a succeeded amount. Otherwise it returns `resolution_message`.

**Authorization and concurrency.** `require_reviewer` runs before a proposal lookup. A customer token receives 403. An unknown proposal id receives 404. Another customer's ticket remains 404 on the customer route. Two concurrent approves meet `uq_approval_per_proposal`: one request succeeds and the other returns 409. The approval row still stores the staff id from the token. Approve still recomputes eligibility and the payload hash. Approvals still expire after 24 hours, checked before a new provider POST.

**Files.** `migrations/versions/0005_ticket_resolution_message.py`, `app/db/models.py`, `app/api/proposals.py`, `app/api/customer.py`, `app/workflows/support_graph.py`, `docs/architecture.md`, `tests/integration/test_proposal_decisions.py`, `tests/integration/test_support_graph.py`, `tests/integration/test_customer_api.py`, `tests/integration/test_resolution_messages.py`.

**Tests.** The queue test keeps a hostile summary out of `proposal_evidence` and out of the approval audit, and it keeps the displayed amount at the stored paise after the order amount changes. It also expects `product_names` from the order item, not from the summary. Graph tests store the order-status, late-return, and escalation sentences, and they leave `resolution_message` empty after a succeeded refund. `test_resolution_messages.py` reads those sentences through `GET /tickets` and checks a cross-customer 404. The full suite recorded after Phase 8 was **167 passed** on 21 November 2026. That number is not the current suite size: later tests were added, including the product-name checks, and a full `uv run pytest` has not been recorded since. The six Ruff findings named in the old audit were removed during the Phase 12 cleanup. This documentation pass did not re-run Ruff or the full suite.

**What we learned.** A reviewer cannot use `GET /tickets`, because that route rejects staff with 403. The complaint has to travel on the reviewer response, but not inside the evidence object. Deriving every customer sentence from current status is not enough: an ineligible reason and an order status are not recoverable later unless the code sentence is stored. Payment text must stay on the ledger so an old stored sentence cannot claim a refund the provider did not confirm.

### Phase 9 — complete, original Step 10 evaluation

Completed on 21 November 2026. The purpose is a repeatable ledger harness, not a model-score script. It seeds private data, drives the real customer and reviewer HTTP paths, runs the workflow (in-process for most cases; live Celery for discovery), and compares Postgres and the mock provider with a frozen expected file.

**Result.** `uv run python -m evals.harness` scored **25/25**. Runtime **64.76 seconds** (`elapsed_ms` 64760). Database **`support_eval`**. `unauthorized_refunds=0`. `duplicate_payments=0`. Failures: none. Report: [`evals/reports/phase9.json`](evals/reports/phase9.json). Expected-ledger SHA-256: `968e054c87960dfa3df3a8eba3b4decd8cfa7e394360254ef2f86c7b67e2ee4b`. Application code, migrations, and `evals/expected.json` were not edited to force that pass.

**Design that ran.** Twenty-five distinct cases cover approved refunds, correct refusals, escalations, hostile ticket and policy text, unauthorized access, reviewer rejection, provider timeout and HTTP 500, lookup mismatch, and kill-before / kill-after recovery. There is no statistical holdout. Expected outcomes live only in `evals/expected.json`. The harness never calls `execute_approved_refund` itself. Redis for this run used database 15 so a default worker on database 0 does not consume harness messages. Cases that leave an open refund (7, 21, 22, 23) run after discovery cases 24 and 25. Case 25 waits for a `refund_reconcile_absent` audit before the later `workflows.run` POSTs once. The four Phase 7 recovery limits remain expected outcomes (case 24 may leave the run `awaiting_approval`; case 25 stays `reserved` through discovery; case 21 stays `unknown` after a finished graph).

**Files.** `evals/cases.py`, `evals/expected.json`, `evals/harness.py`, and the preserved report `evals/reports/phase9.json`. Pytest and `support_test` were not the scoring path. Database `support` was not written.

**What we learned.** A finished workflow cannot adopt a timed-out provider row; discovery is a separate actor. Beat must not see leftover open rows from earlier cases. A fixed sleep after a Beat log line is not proof of a 404 lookup. The source guard for `execute_approved_refund(` must not match its own string literal.

### Phase 10 — original Step 12 frontend

**Objective.** A React customer page and a React reviewer console on the APIs from Phase 8.

**P1 closed (22 November 2026).** Vite + React + TypeScript + Tailwind SPA in `frontend/`, proxy to FastAPI (no CORS). Auth: `sessionStorage` token, `POST /auth/token` then `GET /me` for actor/role (never JWT claims). Customer shell and reviewer shell. Live integration on database **`support_ui`**: customer and reviewer login, wrong-portal redirects, session restore, logout, invalid credentials, invalid token, responsive layout, reduced-motion, Vite `/health` proxy. Mock payments and Celery were not required for P1.

**Shipped screens (22 November 2026).** Customer: `/` home counts, `/orders`, `/cases`, `/cases/new`, `/cases/:ticketId`. Reviewer: `/review`, `/review/queue`, `/review/proposals/:id`. The cases list is live. It uses `GET /tickets`, highlights `escalated`, and does not show `created_at` because `TicketResponse` has no such field. Case detail polls `GET /tickets/{id}`: fast ~1.5s while reserved/submitted/processing, `awaiting_approval`, or open with a null message; slow ~10s while `unknown`; stop on succeeded/failed refund, resolved/escalated ticket, or a non-null message with no refund. Outcome fade is mount-only. Reviewer stats and rows come only from `GET /proposals`. Detail keeps evidence separate from the customer summary. The decision body is `approve` or `reject`. The UI classifies 409 by `detail` and treats 503 `decision recorded; workflow resume was not queued` as saved-with-retry.

**Product names.** Orders, the new-case options, the cases list, case detail, the reviewer overview, the queue, and proposal detail show `product_names` from the API. A missing list falls back to the short order id, or “—” on the case-detail Product field, so an old response cannot blank the page. The closed new-case dropdown includes the name; a separate confirmation under that dropdown was assessed and not built.

**Tests.** Vitest 3.2.4. Production modules covered: `casePolling` (12 fixtures), `proposalDecision` (8 fixtures), API client and errors, login/loadMe/logout and role helpers. RTL: `ProposalDetailPage` (503 Retry, 409 unlock, already-recorded lock) and `CasePage` (reserved null copy, stop polling, Refresh). The last observed `npm test` on 22 November 2026 was **54 passed**. This documentation pass did not re-run it. There is no AuthProvider or route-guard RTL, no cases-list test, and no Playwright run.

**Deferred.** AuthProvider and guard RTL. Playwright. A dedicated order-detail page. The new-case selected-product confirmation line. P6 analytics. Live approve and reject were used manually on `support_ui`; they are not a saved browser test.

**Customer page.** Create a ticket and read its status. The status text comes from the API. The page does not decide eligibility or amount.

**Reviewer console.** Show the pending queue, the stored evidence, the catalog product name, and approve or reject. The request body is the decision word only. Escalated cases are not in this queue.

**Depends on.** Phase 8 (API) and Phase 9 (ledger harness green). Runtime demos use `support_ui` + Stage 5 worker when runs/refunds are exercised.

**Safety.** A direct API call that adds `amount_minor` or `reviewer_id` is still rejected. The UI has no provider URL and no path to `execute_approved_refund`.

**Acceptance.** Essential Phase 10 is met in code: a customer can list orders and cases, open a case, and read the verified sentence; a reviewer can list pending proposals and post approve or reject. P1 auth and shells were checked live on `support_ui`. A full browser recording of approve, reject, and the following customer sentence is still a Phase 12 demo, not a checked-in test. Desktop and a narrow layout are both implemented.

**Effort.** About four to six days total for Phase 10; P1 consumed the auth/shell slice.

### Phase 11 — original Step 11, choose two

The original list has three extensions. Two are selected. The third stays written down so it is not dropped, and it is not the plan.

**Selected: Langfuse tracing.** Observe workflow spans, tool reads, and approval waits. Tracing has no authority. It must not receive card data, raw tokens, or a way to call the provider. Effort about one to two days after Phase 9, so the traces are of the harness path.

**Selected: Claude Desktop or Claude Code connected only to the Orders MCP server.** That server is read-only: `get_order`, `get_return`, `get_refund_status`. The token is a customer MCP token. This matches ADR 0002. Do not attach the actions server and do not add an execute or approve tool for the session. Effort about one to two days. It needs a running orders server and a short-lived token from `scripts/issue_mcp_token`, which refuses every database except `support`. Use `support_test` only if that command is given a test-safe path. Do not point the desktop client at `support` until that database is deliberately upgraded.

**Not selected: three specialist agents compared with a single-agent baseline.** The core is one deterministic graph with no model. A second agent that classifies, chooses an amount, or calls payment would break the approval boundary. A comparison is allowed only as an isolated experiment whose tools cannot reach `execute_approved_refund`, the decision route, or the provider. It is not required for the product to be safe, and it is the option that fails the safety check if it is wired into the real workflow. Leave it unwired.

**Depends on.** A green Phase 9, so the extension is compared with a known ledger outcome.

**Acceptance.** Tracing shows a paused approval and a single payment span for Demo A. The desktop client can read an order for the token's customer and cannot create a refund. The specialist-agent code, if someone prototypes it later, is not imported by `app/workflows/support_graph.py` or `app/services/refunds.py`.

### Phase 12 — original Step 12 release

**Objective.** A stranger can set the project up, run the tests, and watch the three demos.

**Done in the tree.** `README.md` points at the architecture, the ADRs, and the Phase 9 report, and it states the deterministic graph. `docs/demo-script.md` covers Demos A, B, and C. ADR 0003 says the shipped graph does not require an LLM. `.github/workflows/ci.yml` runs Ruff, pytest on `support_test` with Postgres and Redis services, and frontend `npm test` plus `npm run build`. The workflow’s pytest step rewrites away from a casual migrate of database `support` via the test guard. The Phase 12 Ruff edits removed the old `SIM117` findings in `tests/integration/test_schema_guards.py`, the unused pytest import in `tests/test_health.py`, and two nested-if findings in `evals/harness.py`.

**Still pending.**

1. This folder is not a git repository (confirmed 22 November 2026). There is no remote and Actions has never run.
2. A full `uv run pytest` has not been recorded since the 21 November **167 passed** run. Later tests exist, so that count is stale.
3. This documentation pass did not re-run Ruff. The cleanup was reported clean when Phase 12 CI was added.
4. The three-minute video is not recorded.
5. Upgrading database `support` from its last stated Alembic `0002` through `0005`, and ingesting policy there, remains a separate approved write. It is not part of pytest and not part of closing Phase 12 unless that write is explicitly approved. Demo traffic for the UI has been using `support_ui`.

**Depends on.** Phases 7, 8, 9, and 10. Phase 9's report is `evals/reports/phase9.json`. Phase 11 can land before or after the recording. The recording uses the deterministic path.

**Acceptance.** A clean machine follows the README and the three demos match the ledger: one succeeded refund, zero refunds for the blocked request, one provider record after the timeout recovery. CI is green on a pushed remote. `STATUS.md` does not claim LangGraph is unstarted. Those checks are not met yet.

**Effort.** About two to three days, plus the recording.

## Audit of Phases 0–8

Read-only review on 21 November 2026 against `AGENTS.md`, `docs/business-rules.md`, `docs/architecture.md`, and the code. This pass did not rerun pytest, did not query a database, and did not change application code. The **167 passed** figure is the Phase 8 suite run from earlier the same day.

| Requirement | Result | Evidence |
|---|---|---|
| Integer paise, no refund floats | PASS | `amount_minor` is an integer in `refund_payload`, eligibility, and the provider body. |
| Code decides eligibility and amount | PASS | `evaluate_refund_eligibility` uses facts, not the ticket summary. `create_refund_proposal` stores that amount. |
| Human approval before payment | PASS | The graph pauses on `interrupt()`. `read_approval` requires the proposal and approval statuses to match. |
| Decision body is only approve or reject | PASS | `ProposalDecisionBody` forbids extra fields. `test_decision_body_cannot_name_a_reviewer_or_an_amount`. |
| Identity comes from the token's database row | PASS | `get_principal` reloads the customer or staff row. Approve stores `principal.id`. |
| Other customer's order or ticket is 404 | PASS | Customer routes filter on `customer_id`. `test_resolution_messages.py` checks a cross-customer ticket. Coverage is on `support_test`. |
| Staff on customer routes get 403; customers on reviewer routes get 403 | PASS | `require_customer` and `require_reviewer`. Queue test expects 403 for a customer token. |
| Payload hash and expiry bind the approval | PASS | Approve recomputes the hash. `approval_is_current` runs before a new POST. TTL is 24 hours. |
| Reserve, commit, then call the provider | PASS | `execute_approved_refund` reserves with `SELECT … FOR UPDATE`, commits, then looks up and maybe POSTs. |
| One idempotency key, no second payment on replay | PASS | Key is `refund-{proposal_id}`. Unique on `idempotency_key` and `proposal_id`. Graph replay and the killed-worker test expect one create. |
| `execute_approved_refund` is not an HTTP or MCP tool | PASS | The only call found is the graph pay node. MCP tools are `get_order`, `get_return`, `get_refund_status`, `search_policy`, and `create_refund_proposal`. |
| Reconcile looks up and does not POST | PASS | `reconcile_operation` has no `create_refund` call. Aged `unknown` plus a confirmed 404 becomes `failed`. |
| Crash after the provider accepts does not pay twice | PASS | `test_a_killed_worker_is_adopted_by_scheduled_discovery` and the in-process crash test. |
| Ticket retry does not close an intentional reopen | PASS | Retry requires `ticket_update_failed` newer than `ticket_update_retried`. `test_a_reopened_ticket_stays_open`. |
| Reviewer queue and stored evidence | PASS | `GET /proposals` and `GET /proposals/{id}` in `app/api/proposals.py`. Evidence is the stored payload. |
| Customer summary stays out of eligibility, hash, approval, and payment | PASS | Summary is read only to fill `customer_context`. The approval audit test expects no `summary` key. |
| Code sentences for ineligible, order-status, rejected, and escalated outcomes | PASS | `tickets.resolution_message` via migration `0005`. Graph tests and `test_resolution_messages.py`. |
| Succeeded refund text comes from the ledger | PASS | `GET /tickets` prefers the refund row. A stale `resolution_message` still yields the confirmed-amount sentence in `test_customer_api.py`. A paid graph leaves the column empty. |
| One graph, resume value ignored | PASS | `support_graph.py`. `test_forged_resume_does_not_pay`. |
| Policy permission is metadata, not chunk text | PASS | `current_refund_policy` and `refund_policy_decision`. Citation text is not in the hash. |
| Retrieval quality | PARTIAL | 26 live queries on `support_test`: Hit@1 23/26, MRR 0.923. One paraphrase missed `return-window`. No similarity cutoff. This audit did not repeat the search. |
| Worker recovery | PARTIAL | Beat, discovery, the live `workflows.run` order-status task, and crash-after-accept are tested. The four Phase 7 limits below are still true. |
| End-to-end ledger harness | PASS | Phase 9 scored 25/25 on `support_eval`. Report `evals/reports/phase9.json`. Unauthorized 0, duplicate 0. Frozen expected SHA-256 `968e054c87960dfa3df3a8eba3b4decd8cfa7e394360254ef2f86c7b67e2ee4b`. This documentation pass did not rerun the harness. |
| Frontend | PASS for essential Phase 10 | `frontend/src/App.tsx` has customer home, orders, cases, new case, case detail, and reviewer overview, queue, and proposal detail. The 21 Nov audit predated that app. Product names are display-only. |
| Release | PARTIAL | README, demo script, ADR 0003, and `.github/workflows/ci.yml` exist. Git, a fresh full pytest, and a green Actions run are pending. |
| Customer isolation on database `support` | NOT VERIFIED | Automated tests use `support_test`. This audit did not read `support`. The last stated version for `support` is Alembic `0002`. |
| This audit's own suite run | NOT VERIFIED | No pytest was started for this record. The number **167 passed** is carried forward from the Phase 8 run. |

Phase 7 limitations, still present in code:

1. Beat publishes `payments.discover` only while a Beat process is running. The API does not start it.
2. Discovery updates the ledger and does not resume the graph, so a run can stay `awaiting_approval` after the refund is `succeeded`.
3. A `reserved` row with a confirmed 404 stays reserved. Discovery does not POST it.
4. There is no staff API that reopens a ticket.

Missing tests called out on 21 November, updated for what exists now: there is still no browser suite and no fresh hand check of database `support`. The Phase 9 harness case file does exist (`evals/cases.py`, `evals/expected.json`). There is still no test that Beat alone finishes a crash that happens before the provider accepts. That last result is the current design, not a missing assertion to force.

The six Ruff findings named in the 21 November audit (five `SIM117` in `tests/integration/test_schema_guards.py` and one unused `pytest` import in `tests/test_health.py`, plus two nested-if findings later found in `evals/harness.py`) were edited during the Phase 12 cleanup. This documentation pass did not re-run `ruff check`.

**Phase 9 is complete. Phase 10 essential UI is complete** on the existing APIs, including the cases list and display-only product names. Phase 12 is partial. Phase 11 stays optional and off the payment path. The four Phase 7 limits remain product behavior. Escalated cases still have no in-app specialist queue. Do not edit `evals/expected.json` to match a later bug; fix product code or setup only after approval.

## Known limitations that remain

- No similarity cutoff. Unrelated questions still receive a nearest citation. The headphones paraphrase can miss `return-window`.
- `support` has no published policy and cannot run the workflow until it is upgraded on purpose.
- The graph does not call MCP, so the ADR 0002 timing comparison is unmeasured.
- `execute_approved_refund` trusts the stored approval and has no caller identity. That stays safe while it is not exposed over HTTP or MCP.
- The advisory-lock loser is told the row is still `reserved` and does not wait. A later pass adopts a provider record that the winner stored.
- Any effective refund-metadata chunk can change permission, not only `policy_key=refund_policy`.
- If any return exists, the refundable total is the sum of the order's item amounts. Partial returns are out of scope.
- The mock provider's `/control` endpoint has no authentication.
- API and MCP tokens share one HMAC secret. They use different audiences and the MCP token is shorter-lived.
- Customer isolation on database `support` was not rechecked by hand. Automated coverage is on `support_test`.
- `docs/notes.md` still has blank answers. The Step 0 checklist in `STATUS.md` is still unchecked. This rewrite did not grade them.
- The old Ruff findings are no longer the current defect list. They were removed in the Phase 12 cleanup. A fresh `ruff check` was not run for this documentation pass.
- Escalated tickets have a customer sentence and no reviewer inbox. The sentence promises a specialist review the app cannot assign.
- Product names are catalog display data. They do not change eligibility, approval, or payment.

## What this record did not verify

- A new `uv run pytest` for this 22 November documentation pass. The **167 passed** figure remains the last full-suite number and is stale.
- A new `npm test` or `npm run build` for this pass. The last observed frontend run on 22 November 2026 was **54 passed**.
- A new `ruff check` for this pass.
- A new read of `support` or `support_test` during this edit.
- Whether the API, the workers, or the MCP servers are running.
- Git history. An earlier check reported that this folder was not a git repository.
