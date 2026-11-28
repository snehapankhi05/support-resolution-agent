# Project learning log

This file records work that is already implemented. It is not a plan for later steps.
After each completed task, add a new section, or extend an existing section when the
same feature changed. Do not copy secrets, passwords, or API keys into this file.
`docs/notes.md` is the owner's own notes and is not edited from here.

How to read a section:

1. What was implemented and why.
2. How it works internally.
3. Important files, functions, and technologies.
4. Engineering decisions and security considerations.
5. Tests performed and actual results.
6. Interview questions.

## System idea (implemented boundaries)

The product is an approval-gated support agent for a fictional electronics store.
The AI may investigate and draft a refund proposal. Ordinary Python decides eligibility,
and only backend code may move money after a stored human approval. That execution
function is not an agent tool and is not an MCP tool.

Money is stored as integer paise. Customer identity is meant to come from the login
token. Tool schemas must not accept a trusted `customer_id`, reviewer id, approval
flag, or amount. Those rules are written down in `AGENTS.md` and the ADRs. Login and
the customer order and ticket routes exist. The agent workflow does not.

Two Postgres databases exist on the same local server:

| Role | Database name | Who uses it |
|---|---|---|
| Development and seed data | `support` | The app, Alembic from the shell, `python -m scripts.seed` |
| Automated tests | `support_test` | The pytest process only |

The host port is **5433**. A local PostgreSQL 18 service already owns port 5432, so
Compose publishes the project database on 5433. Redis is on port 6379. Connection
passwords stay in `.env` and Compose, not in this log.

---

## Step 0 — Project definition

### 1. What was implemented and why

Step 0 is the written contract for the project: scope, business rules, architecture,
demo cases, and the decisions that later code must not violate. The skeleton
(`app/`, `migrations/`, `tests/`, Compose, Alembic) was already in the repo. The
owner checklist in `STATUS.md` (GitHub, personal notes, pitching out loud) was still
open when later steps started. `docs/notes.md` was left for the owner to write.

### 2. How it works internally

Nothing in Step 0 moves money. The documents define the later code.

Refund rules R1–R8 say when a refund is allowed, that the amount is the paid total of
the returned items and never more than `paid - already committed`, that every refund
needs a reviewer approval which expires after 24 hours, and that a failed inspection
or missing or conflicting policy is escalated. Customer messages must match the real
state. `submitted`, `processing`, `succeeded`, `failed`, and `unknown` stay distinct.

Three architecture decisions are accepted:

- LangGraph checkpoints are workflow memory, not the financial ledger.
- MCP may read orders and policy and create proposals. `execute_approved_refund` stays
  in backend code.
- The model proposes. Deterministic code and database constraints decide.

### 3. Important files and technologies

- `README.md`, `AGENTS.md`, `PROMPTS.md`, `STATUS.md`
- `docs/business-rules.md`, `docs/architecture.md`, `docs/demo-scenarios.md`, `docs/pitch.md`
- `docs/adr/0001-checkpoints-are-not-the-ledger.md`
- `docs/adr/0002-mcp-for-reads-not-execution.md`
- `docs/adr/0003-ai-proposes-code-decides.md`
- `.cursor/rules/` and `.cursor/skills/refund-safety-review/SKILL.md`

Technologies named for later steps: FastAPI, LangGraph, MCP, Postgres with pgvector,
Celery, Redis, React. Only the database, API health check, and refund code exist so far.

### 4. Engineering decisions and security

The AI must not choose the customer, set the amount, approve itself, or call the
payment provider. Other people's records should return 404 so their existence is not
revealed. Retrieved policy text and customer messages are untrusted data. A prompt
hidden in a policy document cannot pay a refund if code recomputes the amount and the
model has no payment tool.

### 5. Tests

No automated tests belong to Step 0. The schema and health tests arrived with the
skeleton and were run in Step 1.

### 6. Interview questions

**Why not give the agent a refund tool with an approval flag?**
Because the model can be tricked into passing `approved=true`. Approval has to be a
row written by the authenticated reviewer, checked again by code.

**Why is a checkpoint not the ledger?**
A checkpoint remembers where a workflow paused. It can be replayed. The ledger is the
`orders` and `refund_operations` tables, with constraints that reject a second payment
even if the workflow runs twice.

**What did the project decide not to build in v1?**
Chargebacks, partial refunds, multiple currencies, real email or SMS, and a real
payment provider. The payment side is a mock. A damaged-item flow is a later stretch
goal.

---

## Step 1 — Database, migration, and health

### 1. What was implemented and why

Step 1 turns the empty skeleton into a running Postgres and Redis, applies the first
migration, and proves `/health` can reach the database. The migration is the safety
foundation: later refund code relies on these constraints instead of hoping the
application never has a bug.

### 2. How it works internally

`docker-compose.yml` starts two containers:

- `sra-postgres`, image `pgvector/pgvector:pg16`, database name `support`, published
  on host port 5433.
- `sra-redis`, image `redis:7-alpine`, port 6379.

The first Compose volume was a half-written Postgres data directory, so Postgres
exited during init. With approval, that volume was removed and created again. That
was empty failed init data, not application data.

`migrations/versions/0001_initial_schema.py` creates the business tables, the
`langgraph` schema (empty, reserved for future checkpoints), the `vector` extension,
checks, unique keys, and two triggers. Alembic revision id is `0001`. There is no ORM
metadata in `migrations/env.py`; the SQL is hand-written so every constraint is
visible. The URL comes from application settings, which read `.env`.

`GET /health` in `app/main.py` runs `SELECT 1`. If the database is down it returns
503. Otherwise it returns `{"status": "ok"}`.

Important constraints:

- `orders.refund_committed_minor` is between 0 and `paid_amount_minor`.
- `refund_operations.idempotency_key` is unique, and so is `proposal_id`.
- Refund status is one of `reserved`, `submitted`, `processing`, `succeeded`,
  `failed`, `unknown`.
- `audit_events` rejects UPDATE and DELETE.
- `action_proposals` rejects changes to `payload`, `payload_hash`, and `action_type`.
  Status may still change.
- An approval that is approved or rejected must have a reviewer and a decision time.

### 3. Important files, functions, and technologies

- `docker-compose.yml`
- `migrations/versions/0001_initial_schema.py`, `migrations/env.py`
- `app/config.py` `Settings`, `app/db/session.py` `engine` and `SessionLocal`
- `app/main.py` `health`
- `tests/conftest.py` (the original rollback fixture; test isolation is a later section)
- `tests/integration/test_schema_guards.py`
- `tests/test_health.py`

Technologies: Docker Compose, PostgreSQL 16, pgvector, Redis 7, Alembic, SQLAlchemy 2,
psycopg, FastAPI, pytest, uv, Python 3.11.

### 4. Engineering decisions and security

Constraints are the last defence. Application code still checks the rules so the user
gets a clear error, but a buggy update cannot commit more refund paise than the order
was paid. Audit rows are append-only so a refund trail cannot be quietly edited.
Proposal payload is immutable so an approval stays bound to the exact proposal that
was reviewed. A changed amount requires a new proposal and a new approval.

The project database is on port 5433 so it does not collide with the local PostgreSQL
18 service on 5432. The application default URL uses that port and the database name
`support`.

### 5. Tests and actual results

Command: `uv run alembic upgrade head`, then `uv run alembic current`, then
`uv run pytest`.

Result: revision `0001 (head)`. **5 passed** (4 schema-guard tests and the health
test) in 6.33 seconds. One existing Starlette warning asked for `httpx2`. It did not
fail the run.

The schema tests insert rows and then roll the transaction back, so those particular
tests do not leave data. They prove:

- raising `refund_committed_minor` above the paid amount raises `IntegrityError`
- the same idempotency key cannot insert two refund operations
- updating or deleting `audit_events` is rejected
- changing an approved proposal payload is rejected

### 6. Interview questions

**Why integer paise instead of a float?**
Binary floats cannot represent every money amount. `0.1 + 0.2` is not exactly `0.3`.
Paise are whole numbers, so `249900` means ₹2,499 exactly.

**What is a migration, and why not edit `0001` after it was applied?**
A migration is a versioned change to the database. Once `0001` has been applied,
editing it would make existing databases disagree with the file. The next change is
a new revision.

**What does `SELECT … FOR UPDATE` do?**
It locks the selected row until the transaction commits or rolls back. Other
transactions that want the same row wait. That is how two refunds cannot both reserve
the same balance.

---

## Step 2 — Models, seed data, eligibility, and refund execution

### 1. What was implemented and why

Step 2 maps the existing tables into SQLAlchemy, loads synthetic shop data, and
implements the refund decision and the payment call. The goal is one safe payment:
recompute the amount, reserve it in a short transaction, commit, then call the
provider, then save the result. Ten parallel attempts must produce one success.

Policy search, login, the reviewer API, LangGraph, and MCP servers are not part of
this step.

### 2. How it works internally

**Models.** `app/db/models.py` maps the tables from migration `0001`. The application
does not call `create_all`. The migration remains the source of the schema. Money
columns are `BigInteger`.

**Seed.** `python -m scripts.seed` writes to the development database through
`SessionLocal`. It is safe to run twice: it deletes previous `seed|` rows and inserts
them again. It does not delete unrelated rows. Audit rows for a replaced seed refund
are removed only when that seed run is cleared. The script inserts:

- 10 customers, including Asha, Rohan, and Meera
- 30 orders:
  - 8 eligible (delivered, return received, inspection passed, nothing refunded yet)
  - 6 delivered 45 days ago with no return
  - 3 returns started after the 30-day window
  - 4 inspection failed
  - 3 returns still in transit
  - 3 not delivered
  - 3 already refunded (`succeeded`, committed amount equals the paid amount)
- Demo-shaped rows: Asha's headphones at 249900 paise, Rohan's watch at 899900 paise
  outside the window, Meera's keyboard at 129900 paise and eligible

Running it twice was checked: still 10 customers, 30 orders, and 3 seed refunds.

**Eligibility.** `evaluate_refund_eligibility` and `refundable_amount` are plain
functions. They do not open a database connection. The caller passes facts.
`refundable_amount` returns the returned-item total capped by
`paid - refund_committed`. Bools and floats are rejected. The decision is eligible
only when the order is delivered and owned by the requesting customer, delivery time
is known, the return started within 30 days, the warehouse received it, inspection
passed, the amount is positive, no blocking refund already covers that amount, and a
single policy was found. Failed inspection, a missing delivery timestamp, no policy,
or conflicting policy sets `escalate`. A clearly late or missing return is ineligible
and is not escalated. `approval_is_current` is separate: the stored approval must be
approved, unexpired, and have a reviewer and a decision time.

**Execution.** `execute_approved_refund(sessions, proposal_id, provider)` does not
take an amount, a customer id, or an approval flag.

1. Open a short transaction. Lock the order with `SELECT … FOR UPDATE`, then lock the
   proposal.
2. If this proposal already has a refund operation and it is not still `reserved`,
   return that status and do not call the provider.
3. Otherwise recompute eligibility from the order, items, and latest return. Compare
   the stored payload hash with a hash of the recomputed amount and order id. A
   mismatch returns `amount_changed` or `payload_hash_mismatch` and does not reserve
   money.
4. Insert `refund_operations` with status `reserved` and a stable idempotency key
   `refund-{proposal_id}`. Add the amount to `orders.refund_committed_minor`. Write
   an audit event. Commit. The row lock ends here, before any network call.
5. Take a Postgres session advisory lock for that operation. Only the caller that
   gets the lock calls the provider. The lock is not a row lock held across the
   network call.
6. Save the provider status in a new transaction. `failed` releases the reserved
   paise. `unknown` (timeout or unusable response) keeps the reservation and is not
   sent again. `succeeded` marks the proposal `executed`, then updates the ticket in
   a separate transaction. If the ticket update fails, the refund row stays
   `succeeded` and an audit event records `ticket_update_failed`.

During execution, `policy_found` is passed as true. Policy retrieval is not built.
Order, return, amount, hash, and approval are still recomputed.

`customer_message` turns a verified state into the sentence from the business rules.
`format_inr` turns paise into rupees with integer division, so 249900 becomes
`₹2,499`.

### 3. Important files, functions, and technologies

- `app/db/models.py` — `Order`, `RefundOperation`, `ActionProposal`, `Approval`,
  `AuditEvent`, and the other tables
- `app/services/eligibility.py` — `refundable_amount`, `evaluate_refund_eligibility`,
  `approval_is_current`
- `app/services/refunds.py` — `refund_payload`, `hash_payload`,
  `refund_idempotency_key`, `execute_approved_refund`
- `app/services/payments.py` — `PaymentProvider`, `HttpPaymentProvider`,
  `ProviderUnavailable`
- `app/services/messages.py` — `format_inr`, `customer_message`
- `scripts/seed.py` — `seed`, `clear_seed`
- `tests/unit/test_eligibility.py`, `tests/unit/test_payments.py`
- `tests/integration/test_refund_execution.py`

Technologies: SQLAlchemy 2 (`mapped_column`, `sessionmaker.begin`), psycopg,
PostgreSQL row locks and advisory locks, httpx for the provider client, pytest,
threading for the concurrency test.

### 4. Engineering decisions and security

The provider is called only after the reservation commits. A crash during the HTTP
call cannot roll back a payment the provider already accepted, and it cannot leave a
database transaction open while the network waits. The idempotency key is derived
from the proposal id, so a retry of the same proposal reuses one key. The unique
constraint on that key and on `proposal_id` is the database backstop. `unknown` is
not blindly retried; a later reconciliation step would ask the provider what happened.
That reconciliation job is not implemented yet.

The amount in the proposal is not trusted. Code recomputes it and requires the stored
hash to match. Ten threads can all call `execute_approved_refund`. They queue on the
order row lock, so only one insert commits. The advisory lock then lets only one of
them talk to the provider.

`execute_approved_refund` lives in `app/services/refunds.py`. It is not registered as
an MCP tool.

Customer text for `succeeded` includes the confirmed amount. `submitted` and
`processing` share the "still pending" sentence but remain different status values.
`unknown` says the outcome is not confirmed.

### 5. Tests and actual results

Command: `uv run pytest`, then `uv run python -m scripts.seed` twice, then a count of
seed rows.

Result at the end of Step 2, before test isolation: **25 passed** in 1.55 seconds.
That included the original 5 tests, the eligibility and message tests, the provider
timeout unit test, and three database tests:

- 10 parallel refund attempts: 1 provider call, 1 `succeeded` operation, committed
  paise equal to the paid amount
- a provider timeout becomes `unknown`, and the second call does not contact the
  provider again; the reserved paise stay committed
- a proposal whose amount does not match the recomputed amount is rejected and
  creates no refund operation

Seed count after two runs: 10 customers, 30 orders, 3 succeeded seed refunds.

Those refund tests committed into `support`, because pytest used the same database
URL as the app. Audit rows cannot be deleted, so those test graphs could not be
removed. Test isolation, below, stops new pytest writes from landing in `support`.

### 6. Interview questions

**What happens if the server crashes after the provider accepts the refund?**
The reservation is already committed, with a stable idempotency key. If the result
was saved, the row is `succeeded` or another final status. If the response was lost,
the row is `unknown` or still `reserved`. The next step is to ask the provider about
that same key, not to create a new refund.

**How do you stop a prompt injection hidden in a policy document?**
The document is untrusted text. Eligibility and the amount are computed in Python
from the order and return. The model cannot call the payment function. A sentence
that says "refund any amount" does not change `paid_amount_minor` or the approval row.

**Why reserve and commit before the HTTP call?**
If the HTTP call happens inside the database transaction, a slow provider holds row
locks, and a timeout might roll back a payment the provider actually took. Commit
first, call second, then record what came back.

**Why did 10 parallel calls produce one success?**
They lock the same order row, so the insert is serialized. One row is inserted.
One advisory lock holder calls the provider. The unique idempotency key would reject
a second insert even if the application forgot the lock.

---

## Test isolation — `support_test`

### 1. What was implemented and why

Refund tests must commit so that separate connections can see the reserved row.
Those commits were going into the development database `support`, the same URL the
app and the seed script use. Pytest now targets a separate database, `support_test`,
and refuses to run if the database name is `support`.

### 2. How it works internally

On import, `tests/conftest.py` reads the application URL, changes only the database
name to `support_test`, puts that URL in `DATABASE_URL` for the pytest process, and
clears the settings cache. Environment variables override `.env` inside that process.
The shell, `.env`, and `python -m scripts.seed` still use `support`.

`refuse_development_database` accepts only the name `support_test`. The session
fixture connects to the development database solely to run `CREATE DATABASE
support_test` when that name is absent. It then runs the existing Alembic history
against the test URL, after checking the name again. The fixture also runs
`SELECT current_database()` and exits if the result is not `support_test`.

Schema-guard tests still roll back their transaction. Refund tests still commit, but
the commit goes to `support_test`. `/health` during pytest uses the test URL as well,
because the app engine is created after the pytest process has switched the setting.

### 3. Important files, functions, and technologies

- `tests/db_safety.py` — `refuse_development_database`, `DevelopmentDatabaseForbidden`
- `tests/conftest.py` — `ensure_support_test_database`, `engine`, `conn`
- `tests/unit/test_database_guard.py`
- `tests/integration/test_refund_execution.py` `_factory` still calls
  `get_settings()`, which now returns the test URL inside pytest

Technologies: pytest fixtures, SQLAlchemy URL objects, Alembic `command.upgrade`,
Postgres `CREATE DATABASE` in autocommit. Migration `0001` was not edited.

### 4. Engineering decisions and security

Creating `support_test` does not drop, truncate, or rewrite `support`. The only
statement sent to `support` during setup is a catalog read and, once, `CREATE
DATABASE`. There is no `DROP DATABASE`, `DELETE`, or Alembic downgrade.

The guard is intentional duplication: the URL is checked before migrate, and the
live connection is checked before tests use it. A mistake that pointed pytest back
at `support` fails the run instead of writing more test customers.

Existing development rows were left in place, including earlier pytest rows that
landed in `support` before this change. The audit trigger is why those rows were
not deleted.

### 5. Tests and actual results

Command: `uv run pytest -q`.

Result: **27 passed** in 3.64 seconds. The two new tests are
`test_support_database_is_refused` and `test_support_test_database_is_accepted`.
Warnings: the existing Starlette `httpx2` warning, and an Alembic deprecation about
`path_separator` in `alembic.ini`. Neither failed the run.

Read-only counts after that run:

| Database | customers | orders | refund operations | audit events |
|---|---:|---:|---:|---:|
| `support` before pytest | 19 | 39 | 9 | 12 |
| `support` after pytest | 19 | 39 | 9 | 12 |
| `support_test` after pytest | 3 | (not counted) | 2 | (not counted) |

`support` did not change. `support_test` contains the customers and refunds created
by the refund execution tests (the rejected-amount case creates a customer and no
refund operation).

### 6. Interview questions

**Why not wrap the concurrency test in one rolled-back transaction?**
Other threads use other connections. They cannot see uncommitted rows from the first
connection. The reservation has to commit or the test is not a real race. That is
also why those rows need a database of their own.

**Why is a second database safer than deleting test rows afterward?**
`audit_events` cannot be updated or deleted. A committed test refund anchors the
order and customer. A separate database keeps that trail without mixing it into seed
and demo data.

**What would you check before running migrations from pytest?**
The database name on the URL, and `SELECT current_database()` after connecting.
Same host and port are not enough, because `support` and `support_test` share the
server.

---

## Step 3 — Mock payment provider

### 1. What was implemented and why

Refund execution already posts to a payment URL and treats a timeout or an HTTP 500
as `unknown`. Step 3 adds the service on the other side of that call. It is a small
separate FastAPI app. Provider idempotency is stored in SQLite so a process
restart still replays the same key. That file is not the ledger. Postgres still
decides whether money was reserved. The earlier in-memory store was replaced;
see "Payment-safety hardening" below.

The provider remembers a refund by idempotency key. The same key and the same
payload return the original operation. The same key with a different amount or order
id is rejected with HTTP 409. Three switches change how the first attempt behaves:
accept then wait longer than the client timeout, return HTTP 500 without storing,
or wait briefly and then succeed.

### 2. How it works internally

`POST /refunds` receives `idempotency_key`, `amount_minor`, and `order_id`. That is
the body `HttpPaymentProvider.create_refund` already sends.

- If the key is new and the mode is `succeed`, the operation is stored as
  `succeeded` and returned immediately.
- If the mode is `timeout_after_accept`, the operation is stored first, then the
  handler sleeps. The client times out. The stored refund remains and can be read
  with `GET /refunds/{idempotency_key}`.
- If the mode is `error_500`, nothing is stored and the response is HTTP 500.
- If the mode is `delay`, the handler sleeps first and only then stores the
  success. A client timeout longer than the delay receives `succeeded`.

A second request with the same key and payload returns the stored operation and
does not count as another attempt. A different payload for that key returns 409
even if a failure switch is on. `POST /control` sets the mode and the delay.
`GET /stats` reports how many first attempts ran and how many operations are stored.

`execute_approved_refund` looks the key up before any new post. The rules for
that lookup are in the next section. A timeout or HTTP 500 still becomes
`unknown` and keeps the reserved paise.

### 3. Important files, functions, and technologies

- `mock_services/payments/app.py` — `create_app`, `POST /refunds`, `POST /control`,
  `GET /refunds/{idempotency_key}`, `GET /stats`
- `mock_services/payments/__main__.py` — `uv run python -m mock_services.payments`
  listens on port 9001, which matches `payment_provider_url`
- `app/services/payments.py` — `HttpPaymentProvider.create_refund`
- `app/services/refunds.py` — `execute_approved_refund` maps provider failure to
  `unknown`
- `tests/unit/test_mock_provider.py`
- `tests/integration/test_mock_provider_switches.py`

Technologies: FastAPI, httpx, uvicorn, and SQLite in the standard library. The
SQLite file for the running service is `mock_services/payments/data/idempotency.sqlite`
and is gitignored. Tests pass their own temporary file.

### 4. Engineering decisions and security

The failure switch is server state, not a field the refund caller can set. Execution
keeps sending only the key, amount, and order id. A model still cannot choose the
switch.

Timeout-after-accept stores the refund and then waits. The first execution call
records `unknown`. A later call looks the key up, copies `succeeded` and the
provider reference onto the ledger, and does not post a second time.

HTTP 500 stores nothing, but execution still uses `unknown` rather than `failed`.
`failed` would release the reserved paise and invite another charge. `unknown` keeps
the reservation until something verifies the provider.

The delay switch stores only after the wait, so a slow success is different from
accept-then-timeout. Idempotent replay does not sleep and does not apply the 500
switch again.

### 5. Tests and actual results

Command: `uv run pytest -q`.

Result: **33 passed** in 15.23 seconds. The new checks are:

- same key and payload returns one operation and `attempts` stays 1
- same key with a different amount returns 409 and still one stored operation
- HTTP 500 stores nothing
- execution against timeout-after-accept: the first result is `unknown`, the
  second call adopts the stored `succeeded` record, and `attempts` stays 1
- execution against HTTP 500: local status stays `unknown`, the provider has zero
  operations, and the second call does not post
- execution against a 0.2 second delay: local status `succeeded`, the stored
  provider reference matches, one attempt

Warnings that did not fail the run: Starlette's `httpx2` notice, and Alembic's
`path_separator` deprecation.

### 6. Interview questions

**Why must the same idempotency key and payload return the same operation?**
The caller may not know whether the first request arrived. Replaying the exact
request must not create a second refund. A different payload with that key is a
mistake, so it is rejected instead of being treated as a new payment.

**What is the difference between a timeout and a timeout after the provider accepted?**
A timeout only says the caller did not get a response. The provider may already
have taken the money. The safe local state is `unknown`. The next action is to ask
the provider about that key, not to send a new refund.

**Why is an HTTP 500 not stored as `failed`?**
`failed` means the provider definitely refused the refund, so the reserved amount
can be released. A 500 does not prove that. Keeping the reservation avoids paying
twice if the provider actually processed the request. A later lookup that confirms
the provider has no record still leaves `unknown` reserved. Releasing that money
waits for the Step 9 reconciliation worker.

---

## Payment-safety hardening

### 1. What was implemented and why

The Step 3 review found that a restarted mock could forget an accepted payment, that
execution posted again for a `reserved` row without asking the provider, and that
HTTP 409 was saved as `unknown`. This change adds the recovery foundation. The
Celery worker that will call it on a schedule is still Step 9.

### 2. How it works internally

The mock writes each accepted refund to SQLite and commits that row before it
sleeps or returns. A new `ProviderStore` that opens the same file returns the
same `provider_reference` for the same key and payload, and still returns 409
when the payload differs. The test that checks this stays inside one pytest
process. See the read-only audit below.

`HttpPaymentProvider.lookup_refund` calls `GET /refunds/{idempotency_key}`. HTTP 404
means the provider has no record. A timeout or HTTP 500 on that GET raises
`ProviderUnavailable`. HTTP 409 on `POST /refunds` raises `ProviderPayloadConflict`.

`execute_approved_refund` holds the advisory lock across lookup and any post. The
order-row transaction stays closed during those requests. Under the lock:

1. If lookup fails, the local row stays `reserved` or `unknown`. There is no post
   and no release.
2. If lookup finds the same amount and order id, that status and provider reference
   are copied onto the ledger. There is no post. This includes a charge whose
   approval has since expired.
3. If lookup finds a different amount or order id, the result is `conflict`. The
   reservation stays. An audit event `refund_payload_conflict` is written.
4. If lookup is a confirmed 404 and the local status is `unknown`, the row stays
   `unknown` and the paise stay reserved. There is no post.
5. If lookup is a confirmed 404 and the local status is `reserved`, approval
   expiry, reviewer, payload hash, ownership, and the recomputed amount are checked
   again. The check subtracts this reservation from the committed balance so the
   row does not block itself. When the check fails, the operation becomes `failed`
   and the paise are released, with no post. When the check passes, one post uses
   the same idempotency key.

### 3. Important files, functions, and technologies

- `mock_services/payments/app.py` — `ProviderStore`, `create_app`
- `app/services/payments.py` — `lookup_refund`, `ProviderRecord`,
  `ProviderPayloadConflict`
- `app/services/refunds.py` — `_call_provider_and_save`, `_adopt_or_conflict`,
  `_block_new_submission`, `_submission_block_reason`
- `tests/unit/test_mock_provider.py` — restart replay
- `tests/unit/test_payments.py` — HTTP 409 mapping
- `tests/integration/test_refund_recovery.py`
- `tests/integration/test_mock_provider_switches.py` — timeout adoption

No migration was added. The refund status check constraint is unchanged.

### 4. Engineering decisions and security

A 404 is trusted only because the mock commits SQLite before it returns success.
Two callers cannot both see that 404 and both post: the advisory lock covers the
lookup and the post.

`unknown` plus a confirmed missing provider record does not become a new payment
and does not release the reservation. That was an explicit choice. A pure HTTP 500
therefore keeps the money held until Step 9.

A payload conflict does not release money, because the provider may already have
accepted a different body for that key. The local amount is not overwritten with
the provider amount.

### 5. Tests and actual results

Command: `uv run pytest -q`.

Result: **39 passed** in 16.76 seconds. The new checks are:

- a second `create_app` on the same SQLite file, still inside one pytest
  process, replays one operation and returns 409 for a different amount
- timeout-after-accept: first call is `unknown`, second call becomes `succeeded`
  from the stored record, and `attempts` stays 1
- HTTP 500: second call stays `unknown` with reason `provider_has_no_record`,
  committed paise stay reserved, and there is no second post
- a provider record with the same key and a different order id returns `conflict`,
  the local row stays `reserved`, and execution does not post
- an expired approval whose provider returns 404 becomes `failed`, the reservation
  is released, and `attempts` stays 0
- a lookup that fails after `unknown` leaves that row `unknown` and does not post
- 10 parallel calls produce one provider attempt and one `succeeded` operation

Warnings that did not fail the run: Starlette's `httpx2` notice, and Alembic's
`path_separator` deprecation. Pytest used `support_test`. No rows were deleted
from `support`.

### 6. Interview questions

**Why look up the key before posting again?**
The first response may have been lost after the provider accepted the refund.
Posting again is safe only when the provider confirms it has never stored that
key. If the record is there, copy it. If the lookup itself fails, do nothing.

**Why can an expired approval still become `succeeded`?**
Expiry stops a new payment. It does not undo a payment the provider already
accepted. The ledger has to match that fact. A new post is refused when the
provider has no record and the approval is expired.

**What does a restart of the mock change?**
The SQLite file still has the idempotency rows, so the same key cannot create a
second provider reference. The Postgres reservation is a different database and
is unchanged by that restart.

---

## Read-only audit of the payment-safety hardening

This audit read the implementation and the assertions. It did not change
application code, tests, migrations, or database rows. It did not start Step 4.
It did not re-run pytest, because the integration tests commit rows to
`support_test`. The earlier "**39 passed**" result stays a record of that
implementation run. This audit does not repeat it as a new result.

### 1. What was verified

| Check | Verdict | What the code and tests actually show |
| --- | --- | --- |
| SQLite idempotency across a new store | Partial | `ProviderStore.insert` commits before it returns. `timeout_after_accept` inserts, then sleeps. `test_restart_replays_the_same_operation` opens a second `create_app` on the same file and asserts the same JSON, then HTTP 409 for a different amount. Both apps live in one pytest process. An operating-system process kill was not performed. |
| Lookup before a new post | Pass | `_call_provider_and_save` calls `lookup_refund` before `create_refund`. Timeout, HTTP 500, and the fake timeout tests assert the second call has `provider_called is False`. |
| HTTP 404 | Pass | `lookup_refund` returns `None` only for 404. An `unknown` row then stays `unknown` with reason `provider_has_no_record`. `test_http_500_is_unknown_and_not_sent_again` and `test_timeout_is_unknown_and_not_sent_again` assert status `unknown`, committed paise still `50000`, and no second post. |
| HTTP 409 | Partial | The mock returns 409 when the stored amount or order id differs. `test_http_409_is_a_payload_conflict` asserts `HttpPaymentProvider.create_refund` raises `ProviderPayloadConflict`. The execution test seeds a different order id and asserts `outcome == "conflict"`, `provider_called is False`, status still `reserved`, and committed paise unchanged. That is the lookup mismatch. The branch that handles 409 from `POST /refunds` inside `execute_approved_refund` has no test. |
| Timeout | Partial | A POST timeout raises `ProviderUnavailable`. Timeout-after-accept asserts the first outcome is `unknown`, the second adopts `succeeded` with the stored reference, and `attempts` stays 1. A GET timeout is raised as `ProviderUnavailable` in code. No test sends an HTTP timeout to `lookup_refund`. |
| HTTP 500 | Partial | `error_500` stores nothing. The execution test asserts one attempt, zero stored operations, a 404, local `unknown`, and the reserved amount kept. A GET that returns 500 is `ProviderUnavailable` in code and is not asserted. |
| Revalidation before a new post | Partial | `_submission_block_reason` rechecks approval, reviewer, payload hash, customer ownership, and the recomputed amount, after subtracting this reservation. The only resume test is expired approval plus a 404: reason `approval_expired`, no post, status `failed`, committed paise `0`, attempts `0`. Hash, ownership, and a changed recomputed amount on an existing `reserved` row are not asserted. `test_changed_amount_is_rejected_without_a_provider_call` rejects the first reservation and asserts zero refund rows. |
| Adopt after approval expiry | Not verified | If lookup finds the same amount and order id, `_adopt_or_conflict` runs before the approval block. Expiry does not stop that copy. No test expires an approval and then asserts the stored payment is copied without a new post. |
| Unknown is not retried or released | Pass | The three tests above leave `unknown` reserved when the provider has no confirmed record or the lookup fails. `_save_result` subtracts paise only when the new status is `failed`. `test_failed_lookup_leaves_unknown_unchanged` asserts one create call and status `unknown`. It does not assert the committed balance. |
| Ten parallel callers, one attempt | Pass | `test_ten_parallel_refunds_one_success` uses a provider with no store of its own. It asserts one `provider_called`, one `succeeded`, and one entry in `provider.calls`. `test_ten_parallel_payments_post_once` asserts the same shape against the HTTP mock and `attempts == 1`. The lock is `pg_try_advisory_lock`. A caller that does not get it returns `already_recorded` immediately. The tests do not assert that those other nine see `succeeded`. |
| Tests stay on `support_test` and temporary SQLite | Pass | `tests/conftest.py` points `DATABASE_URL` at `support_test` and exits if `current_database()` is anything else. `refuse_development_database` rejects the name `support`. Provider tests pass `tmp_path / "provider.sqlite"`. The service default file is gitignored at `mock_services/payments/data/`. |
| Migrations and the `support` database | Partial | The only revision file is `migrations/versions/0001_initial_schema.py`. Its `downgrade()` drops tables. No application code calls it, and this audit did not. This workspace has no git repository, so an edit to that file cannot be ruled out from history. A read-only count on 20 Nov 2026 matched the pre-hardening `support` counts: 19 customers, 39 orders, 9 refund operations, 12 audit events. Approvals were 12. `support_test` held the committed test rows: 36 customers, 36 orders, 30 refund operations, 60 audit events, 36 approvals. |

Refund-safety checklist for this path:

1. Amounts are integer paise. Pass.
2. The posted amount is `operation.amount_minor`, recomputed before the reservation. Pass.
3. Approval is read from the approval row and checked with `approval_is_current`. Pass on the new-post path. The expiry-adoption path deliberately does not apply that check.
4. Resume revalidation is implemented. Only expiry is tested. Partial.
5. `_reserve` uses `SELECT ... FOR UPDATE` and commits before lookup or post. Pass.
6. The provider calls sit outside `sessions.begin()`. Pass.
7. The key is `refund-{proposal_id}` and is reused. Pass.
8. Timeout becomes `unknown`. The next call looks up before any post. `unknown` plus a confirmed 404 does not post. Pass for the tested cases.
9. A succeeded refund updates the ticket afterwards. A ticket failure writes `ticket_update_failed` and does not repeat the payment. Pass in code. No test in this audit.
10. Provider statuses stay `submitted`, `processing`, `succeeded`, and `failed`. Local `reserved` and `unknown` are separate. Pass.
11. This path does not build a customer sentence. Not part of this change.
12. Reserve, provider result, payload conflict, and ticket failure write audit rows. The conflict payload has ids and amounts, not a card number. Pass in code. The conflict test does not query `audit_events`.
13. Duplicate, concurrent, crash-after-accept, and expired-approval tests exist. The expired test covers "no provider record." The accepted-then-expired case does not. Partial.
14. `execute_approved_refund` appears only in `app/services/refunds.py` and tests. Pass.

### 2. Implementation and security decisions

A 404 is treated as "no record" because the mock commits SQLite before it returns
success. `error_500` increments attempts and inserts nothing, so the following
GET is a real 404.

`unknown` plus that 404 keeps the reservation and does not post. That was the
approved choice. The money stays held until a later lookup finds a real record
or Step 9 decides what a permanent absence means.

A matching provider record is copied onto the ledger even when the approval has
expired. Expiry blocks a new post. It does not erase a payment the provider
already stored.

A different provider amount or order id leaves the local row and the reserved
paise alone. The local amount is not replaced by the provider amount.

The advisory lock is held across lookup, the approval recheck, and the only
post. The order transaction is already closed. Two callers cannot both observe
404 and both post. The lock does not wait. The caller who loses it returns
without paying.

The recheck subtracts this operation's paise from `refund_committed_minor`
before it asks whether the order can still fund the refund. Otherwise the
row would reject itself as already refunded.

### 3. Files and functions

- `mock_services/payments/app.py` — `ProviderStore.insert`, `create_app`, `_existing_or_conflict`, `_store_new`
- `app/services/payments.py` — `HttpPaymentProvider.lookup_refund`, `HttpPaymentProvider.create_refund`
- `app/services/refunds.py` — `execute_approved_refund`, `_reserve`, `_call_provider_and_save`, `_adopt_or_conflict`, `_block_new_submission`, `_submission_block_reason`, `_save_result`
- `app/services/eligibility.py` — `approval_is_current`, `evaluate_refund_eligibility`
- `tests/unit/test_mock_provider.py` — `test_restart_replays_the_same_operation`
- `tests/unit/test_payments.py` — timeout and HTTP 409 mapping
- `tests/integration/test_mock_provider_switches.py` — timeout-after-accept, HTTP 500, short delay
- `tests/integration/test_refund_recovery.py` — conflict, expired approval, failed lookup, ten HTTP callers
- `tests/integration/test_refund_execution.py` — ten callers against `RecordingProvider`, unknown with no provider record
- `tests/conftest.py`, `tests/db_safety.py` — `support_test` guard

`execute_approved_refund` reserves the recomputed paise and commits that
reservation before any network call. It then recovers or posts under the
advisory lock.

`_submission_block_reason` decides whether a `reserved` row with a confirmed
404 may be posted. It reads the stored approval, hash, owner, and recomputed
amount. It does not read an amount from the model.

`_save_result` writes the provider status. It releases reserved paise only when
that status is `failed`.

`lookup_refund` turns HTTP 404 into "no record" and turns a timeout or HTTP 500
into `ProviderUnavailable`. `create_refund` turns HTTP 409 into
`ProviderPayloadConflict`.

### 4. Test coverage and results

Assertions inspected, not re-executed in this audit:

- Same file, second app: response JSON is equal, and the different amount is HTTP 409.
- Timeout-after-accept: first `unknown` and `provider_called`, second `succeeded` and not `provider_called`, one attempt, committed paise unchanged.
- HTTP 500: second reason `provider_has_no_record`, status `unknown`, committed paise `50000`, stats `attempts == 1` and `operations == 0`.
- Different provider order id: `conflict`, status `reserved`, no post, one prior attempt.
- Expired approval and an empty provider: `approval_expired`, status `failed`, committed paise `0`, attempts `0`.
- Lookup exception after `unknown`: reason `lookup_unavailable`, one create, status remains `unknown`.
- Ten threads: one provider call and one `succeeded` row, both with `RecordingProvider` and with the HTTP mock.

The implementation run recorded `uv run pytest -q` as 39 passed in 16.76
seconds, with the Starlette `httpx2` notice and the Alembic `path_separator`
warning. This audit did not run that command.

### 5. Remaining limitations and risks

- No test adopts a stored payment after the approval expires. The code does this, and the suite would stay green if that order of checks were reversed.
- Resume checks for payload hash, ownership, and a recomputed amount difference are untested. A mistake there would not be caught by `test_changed_amount_is_rejected_without_a_provider_call`, because that test never creates a `reserved` row.
- The `POST` 409 path inside execution is untested. Lookup mismatch is tested.
- GET timeout and GET 500 are untested.
- The restart test does not kill a process, and it does not crash the mock during the sleep that follows a commit.
- `delay` stores the row after the sleep. A client timeout during that sleep can record `unknown` before the row exists. The next call sees 404, does not post, and leaves the paise reserved. A later lookup can adopt the row once it appears. That case is not tested. The existing delay test uses a 0.2 second delay and a 2 second client timeout, so the first call succeeds.
- `POST /control` has no authentication. It is local mock state.
- Losing the advisory lock returns `reserved` without waiting, so a caller can miss the final `succeeded` status. It does not create a second payment.
- Lock identity uses 8 bytes of the operation UUID. A collision is unlikely and untested.
- `unknown` plus a confirmed 404 can hold reserved money until Step 9. That is the approved behavior, not a release bug.
- Eligibility still passes `policy_found=True`. Policy search is not built.
- There is no git history here, so "the applied migration was not edited" is not proven. There is still one revision, and this audit did not run its downgrade.

### 6. Beginner explanation

The provider and the ledger are two notebooks. Postgres writes "we have set
this money aside." SQLite writes "the provider accepted this exact key and
amount." A lost reply is not the same fact as a failed payment.

Before sending money again, execution asks SQLite about that key. If the page
is there and matches, copy it. If the page is different, stop and keep the
money set aside. If the question itself fails, do nothing. If the page is
missing and the local row is `unknown`, do not send again and do not give the
money back. If the page is missing and the local row is still only reserved,
check the approval and the amount one more time, then send once.

Ten callers share one lock for that operation. One of them talks to the
provider. The others leave.

### 7. Interview questions

**Why is a second `create_app` on the same file not a full restart test?**
It proves a new SQLite connection can read a committed row. It does not prove
what happens if the process is killed after accepting a payment and before the
client sees the response. The commit-before-sleep order is what makes that
crash survivable, and no test kills the process there.

**Why can an expired approval still be recorded as succeeded?**
The provider already accepted the payment. The ledger has to show that.
Expiry is allowed to block only the case where the provider has no record and
a new post would create one.

**Why does `unknown` plus HTTP 404 keep the reservation?**
A 404 says this lookup saw no row. It does not prove a request that is still
in flight will not store one later, and it does not prove an earlier crash
never paid. Releasing the paise would let a later post pay again. Keeping them
reserved is the approved holding action.

**What proves the ten callers do not depend on SQLite to avoid a double post?**
`RecordingProvider.lookup_refund` returns `None` and remembers every
`create_refund`. The test asserts that list has one key. SQLite is not involved.

### 8. Conclusion

Do not mark Step 3 complete.

The tested paths do the safe thing: one payment for ten callers, no second post
after a timeout or HTTP 500, no release of an `unknown` reservation when the
provider has no record, and no release when the stored provider payload
disagrees. Tests use `support_test` and temporary SQLite files. The live
`support` row counts still match the counts from before this hardening.

The follow-up tests are in the next section. That run recorded an ownership
failure. The section after it records the fix. Step 3 is still not marked
complete.

---

## Follow-up tests for the audit gaps

### 1. What was added

`tests/integration/test_refund_safety_gaps.py` adds the eight cases from the
approved plan. Business logic was not changed. The owner test fails, and the
refund path was left as it is.

Postgres access goes through the existing `engine` fixture, which refuses any
database other than `support_test`. SQLite providers use `tmp_path`. The HTTP
409 case uses a scripted `httpx.MockTransport` because a consistent provider
would return the stored row on GET and would never reach POST.

### 2. Assertions and the run

Command: `uv run ruff check tests/integration/test_refund_safety_gaps.py`, then
`uv run pytest -q`.

Ruff reported `All checks passed!`

Pytest: **46 passed, 1 failed**, in 27.39 seconds. The same two warnings
appeared (Starlette `httpx2`, Alembic `path_separator`). The failure is
`test_reserved_owner_change_releases_without_a_post`.

These assertions passed:

- Expired approval plus a matching stored payment: outcome `succeeded`,
  `provider_called is False`, the ledger reference equals the stored reference,
  committed paise stay 50000, and provider `attempts` stay 1.
- Reserved row whose proposal hash does not match its payload: reason
  `payload_hash_mismatch`, no post, status `failed`, committed paise 0,
  attempts 0. The hash was inserted that way. The immutability trigger still
  forbids changing it later.
- Reserved row whose returned item is lowered from 50000 to 40000 paise: reason
  `amount_changed`, no post, status `failed`, committed paise 0, attempts 0.
- First GET 404 and POST 409: outcome `conflict`, one POST, status stays
  `reserved`, committed paise stay 50000, and one `refund_payload_conflict`
  audit row exists. The next GET returns a different amount. The second
  execution does not POST, stays `reserved`, and keeps the 50000 paise.
- GET timeout and GET 500, each against a pre-reserved row: reason
  `lookup_unavailable`, no POST, status stays `reserved`, committed paise stay
  50000.
- Provider delay 1.5 seconds and client timeout 0.2 seconds: the first call is
  `unknown` and keeps 50000 paise reserved. The second call, while the provider
  is still sleeping, has reason `provider_has_no_record` and does not post or
  release. After the row appears, the third call adopts `succeeded` without a
  new POST. Stats end at one attempt and one operation.

### 3. The ownership failure

`_reserve` compares `order.customer_id` with `ticket.customer_id` before it
loads an existing refund operation. For a reserved refund whose ticket now
belongs to another customer, execution returns `rejected` / `not_owner` and
`provider_called is False`. It never reaches `_submission_block_reason` or
`_save_result`.

The test then sees status `reserved` and the 50000 paise still committed. The
expected result, from the approved plan, was status `failed` and committed
paise 0. The new POST is blocked. The reservation is not released.

That is a defect in the resume path. The refund logic was not edited.

### 4. What this does not claim

Step 3 is not complete. Step 4 was not started. The passing tests in this run
show that adoption after expiry, hash and amount revalidation, POST 409,
lookup failure, and a slow delay behave as planned. The ownership failure is
fixed in the next section.

---

## Ownership release on resume

### 1. What changed and why

A reserved refund whose ticket later pointed at another customer never reached
revalidation. `_reserve` returned `not_owner` before loading that proposal's
refund row, so the paise stayed committed. The fix loads the existing operation
first. A `reserved` or `unknown` row still goes through lookup before any
release. `not_owner` is returned before a new reservation only when this
proposal has no refund row.

`_save_result` now refuses to change status or subtract paise unless the locked
row's `proposal_id`, `order_id`, `amount_minor`, and `idempotency_key` equal
the plan. The status change and the subtraction stay in one transaction. The
row must be `reserved` for a validation failure to mark it `failed`, so a
second call does not subtract again.

### 2. How the money path behaves

`_reserve` identifies this proposal's refund row before it can reject a
mismatched ticket. It still will not create a reservation when the ticket
customer and the order customer differ.

`_save_result` writes the outcome and releases reserved paise only when the new
status is `failed`. The identity check binds that release to the locked
operation named by the plan.

Lookup remains first. A matching provider payment is copied even when the
ticket customer has changed. A confirmed 404 plus `not_owner` marks this
proposal's operation `failed`, subtracts its paise once, and writes one
`refund_provider_result` audit event. No POST is made. Another proposal does
not load or update this row.

The advisory lock, the idempotency key, and the database checks are unchanged.

### 3. Files

- `app/services/refunds.py` — `_reserve`, `_save_result`
- `tests/integration/test_refund_safety_gaps.py` — owner release, adopt after
  an owner change, a second proposal, and a first reservation for the wrong
  customer

### 4. Tests and actual results

Command: `uv run ruff check app/services/refunds.py tests/integration/test_refund_safety_gaps.py`,
then `uv run pytest -q`.

Ruff reported `All checks passed!`

Pytest: **50 passed**, 2 warnings, in 26.14 seconds. The warnings are
Starlette's `httpx2` notice and Alembic's `path_separator` deprecation.
Pytest used `support_test`. Provider files were temporary SQLite databases or
a scripted HTTP transport. No migration was edited and no rows were deleted.

Assertions that passed:

- Owner change on a reserved refund with an empty provider: `not_owner`, no
  POST, status `failed`, committed paise 0, one `refund_provider_result` audit
  event with status `failed`. The second call does not post, stays `failed`,
  keeps committed paise at 0, and does not write a second audit event.
- The same owner change when the provider already stored the matching payment:
  `succeeded`, no second POST, committed paise stay 50000, attempts stay 1.
- Another customer's proposal whose ticket points at the reserved order:
  `not_owner`, no refund row for that proposal, the original operation stays
  `reserved`, and its 50000 paise stay committed.
- A first attempt whose ticket customer already differs from the order
  customer: `not_owner`, no refund row, committed paise stay 0.

### 5. What remained at this point

This section records the state before the completion decision. `unknown` plus a
confirmed 404 still keeps its reservation. A delay longer than the client
timeout can still record `unknown` before the provider inserts the row. This
function still does not receive a customer identity from an authenticated
token. One proposal can release only the reservation stored for that same
proposal, order, amount, and idempotency key. The completion record is the
next section.

---

## Step 3 complete — final read-only audit

### 1. Decision

Step 3 — Deterministic Refund Execution and Payment Safety — is **COMPLETE**.
Step 4 was not started. This section is the completion record. Earlier sections
that say the step was not complete describe the reviews before this decision.

The final audit read the implementation and the test assertions. It did not
modify application code, tests, migrations, databases, or `docs/notes.md`.
It did **not** rerun pytest or Ruff. The last verified execution result, from
the ownership-fix run, stands as the test evidence:

- `uv run ruff check app/services/refunds.py tests/integration/test_refund_safety_gaps.py`
  reported `All checks passed!`
- `uv run pytest -q` reported **50 passed**, 2 warnings, in 26.14 seconds.
- The warnings were Starlette's `httpx2` notice and Alembic's `path_separator`
  deprecation.
- Pytest used `support_test`. Provider tests used temporary SQLite files or a
  scripted HTTP transport.

During the audit, a read-only count still matched the pre-hardening `support`
totals: 19 customers, 39 orders, 9 refund operations, 12 audit events, and 12
approvals. `support_test` held the committed test rows. The only migration file
is `migrations/versions/0001_initial_schema.py`. Its downgrade drops tables.
Application code does not call that downgrade.

### 2. Architecture

Money moves through two stores with different jobs.

Postgres is the ledger. `orders.refund_committed_minor` is the reserved or paid
total, in integer paise. `refund_operations` is one row per proposal, with a
unique idempotency key. Status values stay distinct: `reserved`, `submitted`,
`processing`, `succeeded`, `failed`, and `unknown`. `audit_events` is
append-only. LangGraph checkpoints are not this ledger.

The mock provider is a separate FastAPI app on port 9001. Its memory is a
SQLite file, `mock_services/payments/data/idempotency.sqlite` for the running
service, and a `tmp_path` file in tests. A row is committed before the handler
sleeps or returns success. The same key and payload replay one operation. A
different amount or order id returns HTTP 409. SQLite is not the ledger.

`execute_approved_refund` is ordinary backend code. It is not an MCP tool and
not an agent tool. The call takes a proposal id. The amount and the customer
come from database rows.

The safe order is: lock the order, recompute the amount, insert `reserved`,
commit, then talk to the provider. Lookup comes before any new POST. The
advisory lock covers that lookup and the possible POST. The order-row
transaction is already closed.

### 3. Payment-safety guarantees

- The posted amount is the recomputed paise stored on the operation. A proposal
  whose hash does not match that amount is not paid.
- The idempotency key is `refund-{proposal_id}` and is reused.
- A matching provider record is copied onto the ledger, including after the
  approval expires or the ticket customer changes. That copy does not POST.
- A different provider amount or order id leaves the local row and the reserved
  paise unchanged.
- HTTP 409 on POST leaves the row `reserved`, writes `refund_payload_conflict`,
  and the next call looks up instead of posting again.
- Timeout and HTTP 500 become `unknown` or `lookup_unavailable`. They do not
  release paise and do not POST again while the outcome is uncertain.
- `unknown` plus a confirmed 404 does not POST and does not release.
- A `reserved` row plus a confirmed 404 is rechecked. Failed approval, hash,
  ownership, or amount marks that operation `failed` and releases its paise
  once. The release and the status change are one transaction.
- `_save_result` changes the locked row only when its proposal, order, amount,
  and idempotency key match the plan.
- A proposal with no refund row cannot create one when the ticket customer and
  the order customer differ. Another proposal cannot load or release this row.
- Ten parallel callers produce one provider attempt. The proof that does not
  depend on SQLite is `RecordingProvider`, which records every `create_refund`
  and returns no lookup record.
- Audit rows cannot be updated or deleted. The schema test asserts that, inside
  a transaction that rolls back.

### 4. Important functions

`execute_approved_refund` reserves a recomputed amount and then recovers or
posts under the advisory lock. It never reads an amount or a customer id from
a model tool argument.

`_reserve` creates the reservation in a short `SELECT … FOR UPDATE` transaction,
or returns the existing operation before it can reject a changed ticket owner.
It commits before any provider call.

`_call_provider_and_save` looks the idempotency key up before the only new POST.
A lost lock returns without paying. A lookup failure leaves the row as it is.

`_adopt_or_conflict` copies a matching provider status. A mismatched provider
payload does not change the reservation.

`_submission_block_reason` rechecks the stored approval, reviewer presence,
payload hash, ownership, and recomputed amount, after subtracting this
reservation so the row does not block itself.

`_save_result` writes the outcome and subtracts reserved paise only when the
new status is `failed` and the locked identity matches. A second call sees
`failed` and does not subtract again.

`HttpPaymentProvider.lookup_refund` turns HTTP 404 into "no record" and a
timeout or HTTP 500 into `ProviderUnavailable`. `create_refund` turns HTTP 409
into `ProviderPayloadConflict`.

`approval_is_current` and `evaluate_refund_eligibility` are pure checks. They
do not call the provider.

### 5. Test coverage

The 50 tests are the last verified run. They were not executed again during the
final audit. The payment tests assert ledger status, committed paise, and
whether a POST happened. Two of them also assert the audit row.

Covered payment behavior:

- Same SQLite file, second app: identical replay, then HTTP 409.
- Timeout-after-accept: first `unknown`, second adopts `succeeded`, one attempt.
- HTTP 500: no stored provider row, local `unknown`, reserved paise kept, no
  second POST.
- Lookup GET timeout and GET 500: no POST, `reserved` row and paise unchanged.
- POST 409: one conflict audit, reservation unchanged, no second POST.
- Delay of 1.5 seconds with a client timeout of 0.2 seconds: no release while
  the result is uncertain, then one adopted payment.
- Expired approval with no provider row: `failed`, paise released, no POST.
- Expired approval, or a changed ticket customer, with a matching stored
  payment: adopted, no second POST, paise stay reserved.
- Hash mismatch and a recomputed amount change on a real `reserved` row:
  `failed`, paise released, no POST.
- Owner change on a `reserved` row with no provider record: one release, one
  audit, and a second call does not release again.
- Another customer's proposal cannot release the original reservation.
- Ten threads: one provider call and one `succeeded` row, both with
  `RecordingProvider` and with the HTTP mock.

The other tests cover eligibility and customer wording, four schema
constraints, the `support_test` name guard, and `/health`. Their assertions
match those jobs. They are not extra proof of the recovery path.

### 6. Beginner explanation

Think of two notebooks. Postgres writes "this money is set aside for this
proposal." SQLite writes "the provider accepted this exact key and amount."
A lost reply is not the same fact as a refused payment.

Before sending the payment again, the code asks the provider about that key.
If the page is there and matches, copy it. If the page is different, stop and
keep the money set aside. If the question fails, do nothing. If the page is
missing and the local row is already `unknown`, do not send again and do not
give the money back. If the page is missing and the row is only `reserved`,
check the human approval and the recomputed amount one more time. Only then
send once, or release the reservation because the approval is no longer valid.

Ten callers share one lock for that operation. One of them may talk to the
provider. The others leave. The human approval is a stored row with an expiry
and a hash. The model does not get to choose the paise or the customer.

### 7. Remaining limitations

These limits are real. They were part of the final audit.

- **Authentication.** `execute_approved_refund` does not receive a customer or
  reviewer from an authenticated token. A caller who can pass a proposal id can
  drive that proposal. The HTTP API that will bind the token is not part of
  this step.
- **Reviewer validation.** `approval_is_current` requires the approval row to
  have a `reviewer_id`, a decision time, and status `approved`, and it rejects
  an expired row. It does not check that the current caller is that reviewer.
  No test clears `reviewer_id` on an already reserved refund.
- **Provider response amount.** Lookup compares the stored amount and order id.
  `create_refund` accepts a successful POST from its status field and does not
  compare a response amount with the amount that was sent. The mock returns
  409 when the same key is reused with a different payload.
- **Ticket update recovery.** After `succeeded`, a ticket update that raises is
  recorded as `ticket_update_failed`. The next execution sees `succeeded` and
  returns `already_recorded`. The payment is not sent again. The ticket update
  is not retried either, so the ticket can stay unresolved.
- **Unknown reconciliation.** `unknown`, including `unknown` plus a confirmed
  404, keeps the reserved paise. Nothing in this step polls those rows on a
  schedule. A provider delay longer than the client timeout can record
  `unknown` before the provider inserts the row. A later lookup can adopt it.
  The scheduled worker is Step 9.
- **Policy integration.** Eligibility is called with `policy_found=True`.
  Policy documents are not searched, and a policy conflict cannot yet come
  from retrieval.

Also still true, and smaller: the restart test opens a second app in one
pytest process rather than killing a process; the advisory lock does not wait,
so a losing caller can be told the row is still `reserved`; the lock id uses 8
bytes of the operation UUID; `POST /control` on the local mock has no
authentication; a second reservation on the same order is not covered by a
release test; and the identity check inside `_save_result` has no test that
deliberately passes a mismatched plan.

### 8. Why these limits do not reopen Step 3

Step 3's job is deterministic refund execution and payment safety inside the
backend path. That job is: recompute the paise, reserve them, commit, ask the
provider about the same key before paying again, copy a confirmed result, and
release a reservation only when this proposal's recheck fails after a
confirmed 404. The tests assert those ledger results.

The missing pieces are the next boundaries around that path. Token
authentication is the API layer. Reviewer identity of the live caller belongs
with that layer. Policy search is a later retrieval step. A scheduled pass
over `unknown` rows is the reconciliation worker. Retrying a ticket write
after the payment is already `succeeded` is bookkeeping after the money move,
and the current code refuses to move the money again.

None of those gaps lets the model choose an amount, posts a second payment for
one proposal, or releases a reservation because a reply was lost. That is the
scope marked complete.

### 9. Interview questions

**Why can the same idempotency key be sent again?**
The first response may have been lost after the provider accepted the refund.
Replaying the same key and the same amount must return the original operation.
A different amount or order id for that key is a conflict, not a new payment.

**What does the advisory lock protect?**
It makes lookup and the possible POST one turn for that operation. Two callers
cannot both see "no record" and both pay. The order-row transaction is already
committed, so the database is not held open during the network call. A caller
that does not get the lock does not pay.

**When is reserved money released?**
Only when this operation changes to `failed`, in the same transaction as that
status change, and only if the locked row is the same proposal, order, amount,
and idempotency key. `unknown` keeps the paise. A copied `succeeded` result
keeps them too, because the provider has the payment. A second call sees
`failed` or `succeeded` and does not subtract again.

**What is the difference between a timeout and a confirmed missing payment?**
A timeout means the caller does not know whether the provider stored the
refund. The local status becomes `unknown`, and the next action is a lookup.
A confirmed HTTP 404 on a still-`reserved` row means this lookup saw no record,
so the approval can be checked again and either one POST or a release can
happen. A 404 on an `unknown` row does not start a new payment.

**Why is human approval still required after the model proposes a refund?**
The model can suggest an action. The paise, the customer, and the approval are
read from stored rows. The approval is bound to the proposal hash and an
expiry. If the order amount or the owner no longer matches, the code does not
send a new payment. If the provider already accepted the old payment, the
ledger copies that fact instead of pretending the approval can undo it.

---

## Step 4, stage 1 — login and tokens

### 1. What was implemented and why

Stage 1 adds the login account and the API token. It does not add orders,
tickets, reviewer decisions, workflow runs, or refund execution.

`login_accounts` is a new table in migration `0002`. Each row has one lowercase
email, an Argon2id password hash, and exactly one owner: a customer or a staff
user. The database also makes `customer_id` unique and `staff_user_id` unique,
so one person cannot have two logins. Migration `0001` was not edited.

The JWT `sub` is `customer:{uuid}` or `staff:{uuid}`. It is not
`external_auth_subject`, because that value is unique only inside one table.
The token is HS256 and carries issuer `support-resolution-agent`, audience
`support-api`, and a one-hour expiry. It has no role claim. `GET /me` loads
the row and reads the role from the database.

`POST /auth/token` accepts email and password only. An extra field such as
`customer_id` is rejected. Unknown email and wrong password return the same
401. The app refuses to start when `JWT_SECRET` is still the default or is
shorter than 32 characters. Pytest sets its own secret before importing the app.

The seed script can create login rows for the existing demo people when it is
run later. It deletes login rows only for the `seed|` customers and staff it
already replaces. This stage did not run the seed script.

### 2. Important functions

`hash_password` and `verify_password` store and check Argon2id hashes. A wrong
password returns false. When no account exists, verification still runs against
a dummy hash so the response does not take an obviously shorter path.

`issue_token` signs the subject, issuer, audience, and expiry with HS256.
`read_token` accepts only that algorithm and those claims, then returns the
actor and the primary key.

`login` finds one `login_accounts` row by lowercase email and issues a token
for that row's customer or staff id. `me` loads that person from the database
on every call.

### 3. Files

- `migrations/versions/0002_login_accounts.py`
- `app/db/models.py` — `LoginAccount`
- `app/config.py` — issuer, audience, and token lifetime
- `app/auth/passwords.py`, `app/auth/tokens.py`
- `app/api/auth.py`, `app/api/deps.py`, `app/schemas/auth.py`
- `app/main.py` — secret check and the auth routes
- `scripts/seed.py` — demo login rows for a future seed run
- `tests/conftest.py` — pytest JWT secret
- `tests/unit/test_auth_tokens.py`
- `tests/integration/test_login_constraints.py`
- `tests/integration/test_auth_api.py`
- `pyproject.toml` — PyJWT and argon2-cffi

### 4. Tests and database safety

Commands: `uv run ruff check` on the Stage 1 files, then `uv run pytest -q`.

Ruff reported `All checks passed!`

Pytest: **63 passed** in 38.23 seconds. The warnings were Starlette's `httpx2`
notice, Alembic's `path_separator` deprecation, and PyJWT noting that the
negative HS384 test uses a key shorter than SHA-384 recommends. An earlier run
skipped the integration tests because Postgres was not running. That run is
not the result. Postgres was started with the existing volume, and the suite
above is the one that executed the new tests.

Pytest applied migration `0002` only to `support_test`. A read-only check
afterward:

- `support` is still alembic `0001`, with no `login_accounts` table. Counts
  remain 19 customers, 39 orders, 9 refund operations, 12 audit events, and
  12 approvals.
- `support_test` is alembic `0002` and has `login_accounts`.

No Docker volume was removed. The seed script was not run. `docs/notes.md` was
not edited. Stage 2 has not started.

### 5. What this stage does not do

Customer order and ticket routes are not built. Reviewer decisions, workflow
runs, MCP, LangGraph, and payment execution are not HTTP routes. The
development database `support` does not have `login_accounts` until migration
`0002` is applied there. That upgrade only adds a table. It was left for a
later approval so this stage would not write to `support`.

---

## Step 4, stage 2 — customer orders and tickets

### 1. What was implemented and why

Stage 2 adds the customer read and ticket routes. A customer can list and read
their own orders, create a ticket on an order they own, and list and read their
own tickets. The customer id is the primary key loaded from the JWT and the
`login_accounts` row. Request bodies and query strings cannot choose another
customer.

Staff tokens receive 403 on these routes before any order or ticket lookup, so
a staff caller does not learn whether another person's order exists. A customer
who asks for someone else's order or ticket gets the same 404 as a missing id.

Creating a ticket checks that the order belongs to the token customer, then
inserts a ticket with status `open`. It does not start a workflow, create a
proposal, or call the payment code. `execute_approved_refund` is still not an
HTTP route.

### 2. Important functions

`get_principal` reads the bearer token, accepts only a valid HS256 token for
this issuer and audience, and loads the customer or staff row plus the login
account. A role written into the token is not trusted.

`require_customer` returns that customer principal. A staff principal is 403
with `customer account required` before the route queries orders or tickets.

`list_orders` and `get_order` filter `orders.customer_id` to the principal id.
`_own_order` uses the same filter, so a foreign order and a missing order both
raise 404 `order not found`.

`create_ticket` rejects unexpected fields, including `customer_id`, and rejects
a blank summary. It stores `customer_id` from the principal and `order_id` only
after `_own_order` succeeds, then commits that insert. The request session is
not left open across a network call. There is no payment call here.

`list_tickets` and `get_ticket` filter `tickets.customer_id` the same way.
`_latest_refund` finds the newest `RefundOperation` linked through that
ticket's workflow run and proposal. `verified_customer_message` turns a stored
refund status into the existing customer sentence. `submitted`, `processing`,
`succeeded` (with the integer paise amount), `failed`, and `unknown` stay
distinct. `reserved` and a ticket with no refund sentence return `message:
null`, so an open ticket is not described as a completed refund.

### 3. Files

- `app/api/deps.py` — `Principal`, `get_principal`, `require_customer`
- `app/api/auth.py` — `GET /me` uses `get_principal`
- `app/api/customer.py` — the five customer routes
- `app/schemas/customer.py` — order and ticket bodies; create rejects extra fields
- `app/services/messages.py` — `verified_customer_message`
- `app/main.py` — includes the customer router
- `tests/integration/test_customer_api.py`
- `tests/unit/test_eligibility.py` — message cases for open, reserved, and succeeded

No new migration. `0001` and `0002` were not edited. `docs/notes.md` was not
edited. `STATUS.md` was not changed; Step 4 is not marked complete.

### 4. Tests and database safety

Commands: `uv run ruff check` on the Stage 2 files, then `uv run pytest -q`.
The first Ruff run reported one unused test variable. That name was prefixed
with an underscore. Ruff was run again and reported `All checks passed!`

Pytest after that fix: **69 passed** in 38.03 seconds. The same three warnings
as Stage 1 remain: Starlette's `httpx2` notice, Alembic's `path_separator`
deprecation, and PyJWT's note on the deliberate short key in the HS384
rejection test. An earlier pytest in the same session, before the unused-name
fix, also reported 69 passed. The counting result is the run after Ruff was clean.

A read-only check afterward:

- `support` is still alembic `0001`, with no `login_accounts` table. Counts
  remain 19 customers, 39 orders, 9 refund operations, 12 audit events, and
  12 approvals.
- `support_test` is alembic `0002` and has `login_accounts`. Its row counts
  grew because the tests commit their own customers, orders, and tickets there.

No Docker volume was removed. The seed script was not run. Migration `0002`
was not applied to `support`.

### 5. What this stage does not do

Reviewer decisions, `POST /runs`, MCP, LangGraph, and payment execution are
not part of these routes. A new ticket stays `open` and does not start an
investigation. Posting the same complaint twice creates two tickets; creation
is not idempotent.

`damaged_item` is accepted because the existing ticket category check allows
it. It is only a ticket. It is not a refund flow.

FastAPI drops unknown query parameters, so `GET /orders?customer_id=...` is
not a 422. The handler never reads that parameter. The test checks that the
other customer's id is not used. A `customer_id` field on `POST /tickets` is
422.

`support` still has no `login_accounts` table. Login and these routes fail
against that database until migration `0002` is applied there. The upgrade
only adds a table. It was not run.

### 6. Limits that later stages must keep

The customer sentence follows the refund row when one is linked, even if the
ticket status was left at `open`. That matches the existing case where the
provider succeeded and the ticket update did not. Later code must not invent a
payment sentence from the ticket status alone.

`reserved` keeps its paise and returns `message: null`. Later UI or agent text
must not describe that state as accepted by the payment service.

A later list parameter named `customer_id` would let a caller try to select
another customer. These routes must keep taking the id only from
`require_customer`. Staff queue routes need their own dependency. They must
not reuse `require_customer`, and they must not look up a customer resource
before the 403.

---

## Step 4 complete — authentication and customer API

### 1. Decision

The approved Step 4 API scope is **COMPLETE**. This section is the completion
record. The stage 1 and stage 2 sections above describe the work as it was
built. Sentences there that say Stage 2 has not started, or that `STATUS.md`
does not mark Step 4 complete, describe those earlier moments.

The approved scope is login, `GET /me`, and the customer order and ticket
routes. Reviewer proposal decisions, `POST /runs`, MCP, LangGraph, and payment
execution over HTTP were left out on purpose. The first draft of the plan
included reviewer routes. The revised plan removed them before implementation,
and that revision is what was built.

This completion update changes documentation only. It does not change
application code, migrations, databases, or `docs/notes.md`.

### 2. What was implemented and why

A person logs in with email and password. There is no role picker and no
public signup. After the password matches, every later request loads the
account from the database. The token does not get to choose the customer, the
role, or an amount.

`login_accounts` was added in migration `0002`. `0001` was not edited. Each
login has one email, stored lowercase, an Argon2id password hash, and exactly
one owner. The owner is either `customer_id` or `staff_user_id`. The database
check rejects both and rejects neither. Each owner id is unique, so one person
cannot have two logins. Staff have no email column of their own, and
`external_auth_subject` is unique only inside one table. A shared login table
is what makes one email map to one person.

The JWT `sub` is `customer:{uuid}` or `staff:{uuid}`, using that table's
primary key. A bare `external_auth_subject` is not the subject. The same
string could exist once as a customer and once as a staff user. A `customer:`
prefix also gives a later MCP server a way to refuse a staff token.

The token is HS256. It carries issuer `support-resolution-agent`, audience
`support-api`, and a one-hour expiry. It has no role claim. The app refuses
to start when `JWT_SECRET` is still `change-me-in-real-use` or is shorter than
32 characters. Pytest sets its own secret before importing the app. The
configured `jwt_algorithm` field is not what verification uses. Verification
is fixed to HS256 in code.

Customer routes are `GET /orders`, `GET /orders/{id}`, `POST /tickets`,
`GET /tickets`, and `GET /tickets/{id}`. The customer id is the primary key
loaded from the token. Another customer's order or ticket returns 404 with
the same body as a missing id. A staff token on those routes returns 403
before any order or ticket lookup. A body field such as `customer_id` is
rejected. A query `customer_id` is not a declared parameter, so FastAPI drops
it. The handler never reads it.

`POST /tickets` checks that the order belongs to the token customer, then
inserts a ticket with status `open`. It does not start a workflow.

### 3. How the important functions work

`hash_password` stores an Argon2id hash. The plaintext password is not stored.
`verify_password` returns false for a wrong password or a bad hash.
`verify_password_or_dummy` checks the stored hash, or a dummy hash when no
account exists, so the failure does not take an obviously shorter path.
Unknown email and wrong password both return 401 `invalid email or password`.

`issue_token` signs `sub`, issuer, audience, issued-at, and expiry with HS256.
`read_token` accepts only that algorithm and requires `exp`, `iss`, `aud`, and
`sub`. It returns the actor name and the primary key. A wrong audience, such
as `support-mcp`, is rejected. An HS384 token is rejected.

`login` looks up one `login_accounts` row by lowercase email and issues a
token for that row's customer or staff id. `get_principal` reads the bearer
token and loads the customer or staff row plus the login account on every
request. A role written into the token is ignored. If the login row is gone,
the token is 401 even before it expires. `GET /me` returns that database
role. Changing a staff user from `reviewer` to `admin` is visible on the next
call with the same token.

`require_customer` is the FastAPI dependency for customer routes. A staff
principal gets 403 `customer account required` and the route function does
not run. `get_db` yields one request session and closes it. It does not
commit by itself. `create_ticket` commits only the ticket insert.

`list_orders` and `get_order` filter `orders.customer_id` to the principal.
`_own_order` uses the same filter, so a foreign order and a missing order
both raise 404 `order not found`. `create_ticket` stores `customer_id` from
the principal and `order_id` only after that check. `list_tickets` and
`get_ticket` filter `tickets.customer_id` the same way.

`_latest_refund` reads the newest `RefundOperation` linked through that
ticket's workflow run and proposal. `verified_customer_message` turns that
stored status into the existing customer sentence. `submitted`, `processing`,
`succeeded` (with the integer paise amount), `failed`, and `unknown` stay
distinct. `reserved`, and an open ticket with no refund sentence, return
`message: null`.

### 4. Why the other pieces were deferred

Reviewer decision routes do not belong in this step. Almost nothing creates
proposals except tests and the seed. An approve route now would start the
24-hour approval clock and offer a second path toward payment before the
workflow exists. The reviewer id would have to come from the token, and the
body would not be allowed to supply a reviewer or an amount. That work waits
for the human-approval step.

`POST /runs` does not belong here either. `workflow_runs.thread_id` is the
LangGraph thread. Creating a run now would show a workflow that nothing is
running. Creating a ticket with status `open` is enough.

MCP servers are not built. They are meant to validate their own short-lived
token and to read the customer from `sub`, with no `customer_id` in the tool
schema. This API uses audience `support-api` so a later `support-mcp` audience
can be different. Workers that eventually call `execute_approved_refund` call
Python after a stored approval. They do not need this user JWT. CORS waits
for the React app.

`execute_approved_refund` stays ordinary backend code. No route imports it.
Putting the provider call inside the HTTP request would hold the API open
during the network call and create a second execution path.

### 5. Tests and database safety

Stage 1 verification: Ruff on the Stage 1 files reported `All checks passed!`
`uv run pytest -q` reported **63 passed** in 38.23 seconds.

Stage 2 verification, after renaming one unused test variable: Ruff on the
Stage 2 files reported `All checks passed!` `uv run pytest -q` reported
**69 passed** in 38.03 seconds.

The later read-only review ran the same checks again and did not change code:

- `uv run ruff check` on the Step 4 files reported `All checks passed!`
- `uv run ruff check app scripts tests migrations` reported 6 older issues:
  nested `with` statements in `tests/integration/test_schema_guards.py` and an
  unused `pytest` import in `tests/test_health.py`. Those files were not part
  of Step 4.
- `uv run pytest -q` reported **69 passed** in 22.88 seconds. The warnings were
  Starlette's `httpx2` notice, Alembic's `path_separator` deprecation, and
  PyJWT noting that the deliberate HS384 rejection test uses a key shorter
  than SHA-384 recommends.

Pytest used `support_test` only. A read-only check after that 69-pass run:

- `support` is still Alembic `0001`, with no `login_accounts` table. Counts
  remain 19 customers, 39 orders, 9 refund operations, 12 audit events, and
  12 approvals.
- `support_test` is Alembic `0002` and has `login_accounts`. Its counts are
  higher because tests commit their own rows there.

The seed script was not run. Demo login accounts are not provisioned.
`python -m scripts.seed` is not a way to add only those logins. `seed()`
calls `clear_seed()` first and replaces the `seed|` customers, orders,
tickets, proposals, approvals, and refunds. It also deletes login rows for
those people. Non-seed rows are outside that delete. On the current
`support` database, seed-linked audit events are 0, so the append-only audit
trigger would not fire today. That trigger rejects `DELETE` as soon as a seed
run has an audit row, and `clear_seed()` still issues that delete.

### 6. Known limitations and future considerations

Login against `support` fails until migration `0002` is applied there. The
upgrade only adds `login_accounts`. It was left for a separate approval.

There is no logout or token revocation. A token lasts until it expires.
Password changes are not an API. The demo password, once seed logins exist,
is a shared demo secret in `scripts/seed.py`. Authorization is the security
property. The password is not.

A query parameter named `customer_id` is ignored rather than rejected. A
later route must not declare that parameter. Staff queue routes need their
own dependency. They must return 403 before they look up a customer order or
ticket.

The database does not require `tickets.customer_id` to equal the order's
customer. This API sets both from the token. A later writer must keep that
match.

Ticket creation is not idempotent. Two posts create two `open` tickets.
`damaged_item` is a ticket category the existing check allows. It is not a
refund flow. Customer text follows the refund row when one is linked, even
if the ticket status is still `open`. `reserved` returns `message: null`.
Later UI or agent text must not describe that state as accepted by the
payment provider.

The MCP rule still says to read the customer from `sub` without saying that
`sub` is `customer:{uuid}`. A later MCP server must parse that prefix, require
its own audience, and keep `customer_id` out of tool schemas. It must not
treat `sub` as `external_auth_subject`.

Refund execution was not changed. The Step 3 limits remain: the service
function itself has no caller authentication, a successful provider POST body
amount is not compared, a failed ticket update after success is not retried,
`unknown` waits for reconciliation, and policy search is not built.

### 7. Interview questions

**Why is the JWT subject `customer:{uuid}` instead of the external subject?**
`external_auth_subject` is unique only inside `customers` or only inside
`staff_users`. The same string could name both. The primary key plus a prefix
picks one row, and a staff token cannot be read as a customer.

**Why does the API load the role from the database on every request?**
The token is only proof that the password matched. A role or `customer_id`
claim is not authority. Demoting a staff user, or deleting the login row,
takes effect before the token expires.

**Why is another customer's order a 404, while a staff token is a 403?**
404 is for a hidden object: a missing order and someone else's order look the
same, so the response does not reveal that the other order exists. 403 is for
the wrong kind of account. Staff are refused before the query, so they also
do not learn whether the order exists.

**Why can the model not call the refund from these routes?**
These routes identify the person and store an `open` ticket. The amount, the
approval, and the provider call stay in the refund service, behind a stored
approval. The HTTP API does not import that function.

---

## Demo logins on support

### 1. What changed and why

`support` was still on Alembic `0001`, so the Step 4 login route could not run
against the development database. `support_test` was already on `0002`.
`python -m scripts.seed` was not used. That command deletes and recreates the
`seed|` customers and their orders, tickets, proposals, approvals, and refunds,
and it tries to delete audit events. The audit table rejects that delete once
a seed run has an audit row.

`uv run alembic upgrade head` applied the existing migration `0002` to
`support`. That migration only creates `login_accounts`. It does not change
customers, orders, refunds, approvals, the audit trigger, or the `langgraph`
schema. `0001` and `0002` were not edited.

`provision_demo_logins` then inserted one login for each existing `seed|`
customer and for `seed|reviewer`. The email is the person's stored address,
lowercased, except the reviewer, who has no email column and uses
`priya@seed.example`. The password is the existing demo constant, stored only
as an Argon2id hash. A login that already belongs to that person is left
unchanged, including its hash. If a required person is missing, or the email
already belongs to someone else, the function raises and the caller rolls
back. It does not insert customers or orders, and it does not delete anything.
The other nine customers and the other staff users on `support` did not get a
demo password.

The command checks `current_database()` and refuses every name except
`support`. It prints the database name, the Alembic version, and the insert
counts. It does not print the password, the hash, or the connection URL.

### 2. Results

Before the upgrade, `support` was Alembic `0001` with no `login_accounts`
table: 19 customers, 39 orders, 9 refund operations, 12 audit events, 12
approvals, 10 staff users, 10 `seed|` customers, and 1 `seed|reviewer`.

The provision command printed: `database support alembic 0002 inserted 11 already_present 0`.

After provisioning, and again after the full test suite: Alembic `0002`,
19 customers, 39 orders, 9 refund operations, 12 audit events, 12 approvals,
and 11 login accounts. Ten of those logins belong to `seed|` customers, one
belongs to `seed|reviewer`, and none belong to a non-seed customer. No login
has both owners set. All 11 stored hashes match the demo password. The
password and the hashes were not printed.

Ruff on `scripts/provision_logins.py`, `scripts/seed.py`, and
`tests/integration/test_provision_logins.py` reported `All checks passed!`

`uv run pytest -q` reported **73 passed** in 39.70 seconds. The warnings were
the existing Starlette `httpx2` notice, Alembic `path_separator` deprecation,
and PyJWT's note on the deliberate short key in the HS384 rejection test.
Pytest used `support_test` only. The four new tests roll back: they check
that eleven logins can be added, that a second call inserts nothing, that an
email owned by someone else aborts, that a missing seed person aborts, and
that the CLI refuses `support_test`.

`docs/notes.md` was not edited. `python -m scripts.seed` was not run.

---

## Step 4 final verification

This section is the plain-language record of Step 4 after the code, the tests,
the development database, and a manual login check. Earlier Stage 1, Stage 2,
completion, and demo-login sections stay as they were. They describe the work
as it happened. This section is the one to reread later.

### 1. What Stage 1 and Stage 2 built

Stage 1 made it possible to log in. It added the `login_accounts` table,
Argon2id password hashes, JWT issuing and checking, `POST /auth/token`, and
`GET /me`.

Stage 2 let a logged-in customer use their own data. It added `GET /orders`,
`GET /orders/{id}`, `POST /tickets`, `GET /tickets`, and `GET /tickets/{id}`.
Creating a ticket only stores an `open` complaint. It does not start an
investigation and it does not move money.

`GET /health` was already there. It checks that the API process can reach
Postgres. It does not require a token.

### 2. How login and identity work

Think of three separate facts that must not be mixed up.

The password answer is "does this person know the password?" `login_accounts`
stores the email in lowercase and an Argon2id hash. The plaintext password is
not stored. Argon2id is a slow password hash, so guessing is expensive.
`hash_password` creates the hash. `verify_password` returns true only for the
same password. `verify_password_or_dummy` still does that slow check when no
account exists, so a missing email and a wrong password take a similar path
and return the same 401, `invalid email or password`. There is no signup page
and no role picker. One email belongs to one row, and that row points at
either a customer or a staff user, never both. The database enforces that.

The token answer is "did this API issue this token, and is it still in date?"
`issue_token` signs a JWT with HS256. The subject, `sub`, is `customer:{uuid}`
or `staff:{uuid}`. That uuid is the primary key of the customer or staff row,
not `external_auth_subject`. The same external subject string is allowed once
in each table, so it would be a bad identity. The token also has issuer
`support-resolution-agent`, audience `support-api`, and a one-hour expiry. It
has no role claim. `read_token` accepts only HS256 and requires those claims.
A token made for a future MCP audience, `support-mcp`, is rejected here. The
app refuses to start if `JWT_SECRET` is still the default or is shorter than
32 characters.

The person answer is "who is this token, according to the database right now?"
`get_principal` checks the bearer token and then loads the customer or staff
row and the login row. If the login row was deleted, the token stops working
even though it has not expired. `GET /me` returns the role stored in the
database. A staff user changed from `reviewer` to `admin` is seen as `admin`
on the next call with the same token. A role typed into the token is ignored.

### 3. The endpoints

| Method and path | Who | What it does |
|---|---|---|
| `POST /auth/token` | Anyone | Email and password in, bearer token out. Extra fields such as `customer_id` are rejected. |
| `GET /me` | The token's person | Returns the database id, email, name, and role. |
| `GET /orders` | Customer | Lists that customer's orders. A query `customer_id` is ignored. |
| `GET /orders/{id}` | Customer | One of that customer's orders. |
| `POST /tickets` | Customer | Opens a ticket on an order that customer owns. Status stays `open`. |
| `GET /tickets` | Customer | Lists that customer's tickets, including a sentence when a refund row exists. |
| `GET /tickets/{id}` | Customer | One of that customer's tickets. |
| `GET /health` | Anyone | Database liveness. No token. |

Money in these responses is integer paise. The customer sentence comes from
the stored refund row. `succeeded` includes the amount. `submitted`,
`processing`, `failed`, and `unknown` stay distinct. `reserved`, and an open
ticket with no refund, return `message: null`.

### 4. How the FastAPI dependencies fit together

A dependency is a function FastAPI runs before the route. The route does not
read the `Authorization` header itself.

`bearer_scheme` is FastAPI's `HTTPBearer` helper with `auto_error=False` and
`bearerFormat="JWT"`. Its only new job is to tell Swagger that protected
routes need a bearer token, so `/docs` shows an Authorize button. It does not
decide whether the token is valid. `auto_error=False` matters because the
helper's default error text is `Not authenticated`. This API still returns
`authentication required` and `invalid token`.

`get_principal` depends on `bearer_scheme` and `get_db`. It accepts only a
header whose scheme is exactly `Bearer`, then calls `read_token`, then loads
the database row. `require_customer` depends on `get_principal`. If the person
is staff, it returns 403 `customer account required` and the order or ticket
query does not run. `get_db` opens one database session for the request and
closes it. It does not commit. `create_ticket` commits only the new ticket.

`POST /auth/token` and `GET /health` do not use `get_principal`, so Swagger
does not put a lock on them.

### 5. 401, 403, and 404

These three codes answer different questions.

401 means "I do not know who you are." The header is missing, it is not
`Bearer`, the token is expired or altered, or the login row is gone. A
lowercase `bearer` scheme is also 401. That matches the earlier check, which
required the capitalised scheme.

403 means "I know who you are, and you are the wrong kind of account." Priya
is a real reviewer. `GET /orders` is a customer route, so she gets 403 before
any order lookup. She does not learn whether a particular order exists.

404 means "that object is not available to you." A missing order and another
customer's order both return 404 `order not found`, with the same JSON. The
same rule applies to tickets. The API does not use 403 for hidden objects,
because 403 would admit that the object exists.

### 6. What was left for later, and why

Reviewer decision routes were not built. Almost nothing creates proposals
except tests and the seed. An approve button now would start the 24-hour
approval clock and offer a second path toward payment before the workflow
exists. When that step comes, the reviewer id must come from the token, and
the body must not supply a reviewer or an amount.

`POST /runs` was not built. A workflow run's `thread_id` is the LangGraph
thread. Creating one now would show a workflow that nothing is running.
LangGraph checkpoints, when they exist, belong in the `langgraph` schema.
They are not the refund ledger. Refund paise stay in `orders` and
`refund_operations`.

MCP servers were not built. They are supposed to check their own short-lived
token, read the customer from `sub`, and refuse a `customer_id` argument.
This API's tokens use audience `support-api`, so they are not MCP tokens.
The MCP rule file still says "read `sub`" without saying that `sub` is
`customer:{uuid}`. That wording should be fixed when MCP is built, not by
changing these tokens.

`execute_approved_refund` is still ordinary Python. No route imports it.
Workers, when they exist, should call it after a stored approval. They do
not need this user JWT. Putting the payment call inside the HTTP request
would hold the request open during the network call and create a second way
to move money.

### 7. Database and demo logins

Migration `0002` only creates `login_accounts`. `0001` was not edited.
Pytest applies migrations only to `support_test`. The development database
`support` was upgraded from `0001` to `0002` with `uv run alembic upgrade head`.
That did not rewrite customers, orders, refunds, approvals, or audit events.

Demo logins were inserted with `python -m scripts.provision_logins`, not with
`python -m scripts.seed`. The seed command deletes and recreates `seed|`
customers and their orders, tickets, proposals, approvals, and refunds, and
it tries to delete audit events. The audit table is append-only, so that
delete fails once a seed run has an audit row.

Provisioning found the ten existing `seed|` customers and `seed|reviewer`,
and inserted 11 logins. The command printed `database support alembic 0002
inserted 11 already_present 0`. After that, `support` still had 19 customers,
39 orders, 9 refund operations, 12 audit events, and 12 approvals, plus 11
login accounts. Non-seed people did not get a demo password. All 11 hashes
match the demo password stored as `DEMO_PASSWORD` in `scripts/seed.py`. The
password and the hashes were not printed.

### 8. How to run it locally

Start Postgres and Redis with `docker compose up -d` if they are not already
running. Postgres is on port 5433. Then, from the project directory:

```
uv run uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`.

Log in with `POST /auth/token`. The seed customer email is
`asha@seed.example`. The reviewer email is `priya@seed.example`. The password
is the `DEMO_PASSWORD` constant in `scripts/seed.py`. Copy only the
`access_token` value. In Authorize, paste that value into HTTPBearer. Do not
type the word `Bearer` in front of it. Swagger adds that prefix. Then use
`GET /me` or the customer routes. The same token is sent until you click
Authorize again or the hour expires.

`uv run pytest` uses `support_test` and refuses to run against `support`.

### 9. Automated tests and manual checks

These are different kinds of evidence. A passing test is not a manual click,
and a manual click does not replace the suite.

Automated, on `support_test`:

- Ruff on `app/api/deps.py`, `tests/unit/test_openapi_security.py`, and
  `tests/integration/test_auth_api.py` reported `All checks passed!`
- `uv run pytest -q` reported **76 passed** in 34.75 seconds. The warnings
  were Starlette's `httpx2` notice, Alembic's `path_separator` deprecation,
  and PyJWT's note on the deliberate short key in the HS384 rejection test.
- The suite includes login, role loaded from the database, expired and altered
  tokens, staff 403 on customer routes, another customer's order and ticket
  returning the same 404 as a missing id, ticket creation refusing another
  customer's order, and the OpenAPI bearer scheme. Protected paths advertise
  `HTTPBearer`. `/health` and `/auth/token` do not.

Manual checks against `support`, rechecked in this session with status codes
only:

- Asha `POST /auth/token`: HTTP 200. `GET /me`: HTTP 200, actor `customer`,
  role `customer`.
- Priya `POST /auth/token`: HTTP 200. `GET /me`: HTTP 200, actor `staff`,
  role `reviewer`.
- Priya `GET /orders`: HTTP 403.

Customer-to-customer order and ticket isolation has not been manually
verified. The automated tests are what currently show that one customer does
not receive another customer's order or ticket. Asha's own `GET /orders`
returned 200 in this session. That only shows that a customer can list orders.
It does not show that a second customer is excluded.

Swagger was checked in the browser at `http://127.0.0.1:8000/docs`. The
Authorize button is visible. The dialog is `HTTPBearer (http, Bearer)`. After
one pasted customer JWT, the protected operations showed a locked
authorization state, and `GET /me` from that page returned HTTP 200 with
actor `customer`. `/auth/token` and `/health` stay unlocked.

### 10. Limits that remain

A query parameter named `customer_id` is ignored, not rejected. A later route
must not declare that parameter. Ticket creation is not idempotent. The
database does not force a ticket's customer to equal the order's customer.
This API sets both from the token. There is no logout. A token lasts until it
expires. Refund execution was not changed: the service function itself has no
caller authentication, a successful provider response amount is not compared,
a failed ticket update after success is not retried, `unknown` waits for
reconciliation, and policy search is not built.

Step 5 was not started.

---

## Step 5 — MCP servers

The installed package is the official `mcp` **2.3.0**. Its server class is
`MCPServer` in `mcp.server.mcpserver`. The older name `FastMCP` is not the
class in this version, and the third-party FastMCP package was not installed.
`pyproject.toml` pins `mcp>=2.3,<3`.

Each server is its own Streamable HTTP app. They are not mounted on the
customer FastAPI app. Orders listens on `127.0.0.1:8101`, policy on
`127.0.0.1:8102`, and actions on `127.0.0.1:8103`. The SDK builds the bearer
middleware when the server is given `CustomerTokenVerifier` and
`AuthSettings`. `validate_token_resource` is false because this project checks
the JWT audience itself. The audience is the string `support-mcp`, not the
server URL.

`issue_mcp_token` signs a 15-minute HS256 token with that audience and a
`customer:{uuid}` subject. `read_mcp_customer` accepts only that shape.
`read_token` still requires audience `support-api`, so an API token and an MCP
token are not interchangeable. A `staff:` subject is rejected even when the
audience is `support-mcp`. The same HMAC secret signs both audiences. A
process that can read `JWT_SECRET` can still forge either token. The audience
split stops a customer API token from being pasted into MCP by accident.

The orders tools are `get_order`, `get_return`, and `get_refund_status`. The
customer id comes from the verified token. A missing order and another
customer's order both return `ORDER_NOT_FOUND` with the same body. Policy
search is `search_policy`. It matches stored chunk text, case-insensitive,
and returns `policy_found: false` with an empty list when nothing matches.
There are no embeddings. The actions tool is `create_refund_proposal` and
takes only `ticket_id`. A missing or foreign ticket is `TICKET_NOT_FOUND`.
A ticket with no `workflow_runs` row is `NO_ACTIVE_RUN`. The tool does not
insert a run. When the order is eligible, it stores one `pending_approval`
proposal whose amount comes from `evaluate_refund_eligibility`. A second call
returns that same proposal. When no stored chunk matches `refund`, the result
is `no_policy` and no proposal row is inserted. No MCP module calls
`execute_approved_refund`.

`python -m scripts.issue_mcp_token EMAIL` prints one token for a customer
login. It is a local command, not an HTTP route. It refuses every database
except `support`. The tests call that refusal against `support_test` and do
not print a token.

Automated checks, each server before the next: orders tests passed, then
policy tests passed, then actions tests passed. Ruff is clean on the MCP
files. The full suite is `uv run pytest -q`: **90 passed** in 61.88 seconds
on `support_test`. MCP Inspector was not run. LangGraph is still not built,
so a real customer ticket still has no workflow run until some later step
inserts one.


---

## Step 5 follow-up — policy decision, one pending proposal, Inspector

`refund_policy_decision` reads stored chunk metadata only. A row counts when
`applies_to` is `refund` and `effect` is `allow` or `deny`. Prose that merely
contains "refund" is `no_policy`. A deny, a mix, or an unknown effect is
`policy_conflict`. No proposal is inserted in those cases. MCP tool schemas
have no `effect`, `applies_to`, or `metadata` argument.

Duplicate pending groups were checked before the new revision. `support` and
`support_test` both had zero. Revision `0003` adds partial unique index
`uq_one_pending_proposal_per_action` and stops if duplicates exist. It does
not change existing proposal rows. Pytest applied it to `support_test`.
`support` remains Alembic `0002`. A unique violation of that named constraint
rolls back and returns the winning pending proposal with `created: false`.
Any other database error is raised.

Two overlapping calls in the test received one proposal id, left one pending
row, and created no refund operation. `uv run pytest -q`: **99 passed** in
24.69 seconds. Ruff is clean on the touched files.

Inspector CLI, Streamable HTTP, bearer header, no OAuth login:

- `127.0.0.1:8101` tools: `get_order`, `get_return`, `get_refund_status`. A
  random order id returned `ORDER_NOT_FOUND`.
- `127.0.0.1:8102` tool: `search_policy`. Query `zz-not-a-policy` returned
  `policy_found: false` and no chunks.
- `127.0.0.1:8103` tool: `create_refund_proposal`, input `ticket_id` only.
  The tool was not called.
- A live POST with no token returned HTTP 401 on all three ports.



---

## Staff approval and hash-bound policy

`POST /proposals/{id}/decision` accepts only `approve` or `reject`. The staff
id is the database principal for the token. A customer token is 403 before
the proposal is loaded. Approve locks the proposal, requires
`pending_approval`, recomputes eligibility, and checks the stored payload
hash. The approval row stores that staff id. A second decision is 409 and
does not insert another approval. Reject records the staff decision even
when the proposal is no longer eligible.

`refund_payload` now includes `policy_effect` (`allow`, `conflict`, or
`none`). Proposal creation and execution both read the effect through
`current_refund_policy`, which calls `refund_policy_decision`. Before a new
provider POST, execution rebuilds the payload from the current metadata. A
deny with a confirmed provider 404 marks the reserved operation failed and
releases the committed paise. A provider record with the same amount and
order is adopted and is not posted again.

No migration. `support` remains Alembic `0002`. `support_test` remains
`0003`. `uv run pytest -q`: **107 passed** in 29.87 seconds. Ruff is clean
on the files changed for this slice. The older Ruff findings in
`tests/integration/test_schema_guards.py` and `tests/test_health.py` were
left as they were.

Still open: `unknown`, `submitted`, and `processing` are not reconciled; a
successful POST body amount is not compared; a failed ticket update is not
retried; `execute_approved_refund` has no caller of its own and trusts the
stored approval; `RefundFacts.policy_found` still defaults to true for a
caller that forgets to pass it.



---

## Step 6 — Policy RAG

Search and permission stay separate. `search_policy` returns `matched` and
citations (`policy_key`, `version`, `section_ref`, `content`, `score`) for
documents that are effective today. Lexical search always runs. Cosine search
runs when an embedder is passed in, or when `POLICY_EMBEDDINGS=1` loads
`fastembed` model `BAAI/bge-small-en-v1.5` (384 dimensions, the existing
column). The two lists are merged with reciprocal rank fusion. Pytest uses a
stub embedder and does not download the model.

`current_refund_policy` still calls `refund_policy_decision` on chunk
metadata. It now ignores documents whose dates are not current. Prose still
cannot allow a refund. The proposal hash includes the sorted
`policy_versions` list. A new effective version fails that hash and does not
create a new provider payment. A provider record that already matches the
amount and order is still adopted. `RefundFacts.policy_found` now defaults
to false, so a caller that forgets the flag gets `no_policy`.

Ingest reads `policies/refund_policy_v1.json` and the markdown beside it,
checks the 30-day window against `RETURN_WINDOW`, and inserts the version
once. The same body again is `already_present`. A different body for that
version is refused. A later version closes the open row. The command refuses
database `support` and was not run there. No migration was added.
`support_test` remains Alembic `0003`.

Later clarification (21 November 2026, before Phase 10): the library guard
`assert_ingest_database` still refuses only `support`. The CLI
`scripts/ingest_policies.py` allowlists `support_test` and `support_ui`, and
explicitly refuses `support` and `support_eval`, so Phase 9 evidence cannot be
overwritten by a casual ingest. The library file was not broadened.

`uv run pytest -q`: **119 passed** in 32.35 seconds. Ruff is clean on the
Step 6 files. The older Ruff findings in `tests/integration/test_schema_guards.py`
and `tests/test_health.py` were left as they were.

Still open after this step: no cross-encoder rerank, no BM25 extension,
live MCP search stays lexical until `POLICY_EMBEDDINGS=1`, and LangGraph is
not started. Reconciliation, the POST response amount, and ticket-update
retry are unchanged.

This paragraph records the end of Step 6. Later phases built the workflow,
the worker, the reconciler, and a partial recovery path. The standing record
is `learning_project.md`. The section below, dated 21 November 2026, is the
log entry for that later state.



---

## 21 November 2026 — roadmap after Phases 0–7

The sections above stay as they were written. Several of their "still open"
lines are no longer the current system. This entry does not repeat those
notes. `learning_project.md` is the roadmap.

What the code has now, beyond Step 6:

- A provider POST is kept only when the body repeats the reserved amount and order.
- One deterministic LangGraph workflow. It calls Python services in-process. It does not call MCP. Resume values are not approvals.
- `POST /runs` and migration `0004` on `support_test`. `support` was still Alembic `0002` at the last read-only check.
- Celery task `workflows.run`, checkpoints in schema `langgraph`, and a staff decision that queues the run id after commit.
- `payments.reconcile` looks up the stored idempotency key and does not POST.
- A lost reconcile publish is recovered from the ledger. A succeeded refund with an unresolved ticket is resolved without another payment. An in-process crash after provider acceptance is adopted on the next run. Two in-process resumes pay once.
- The full suite after that recovery work was **158 passed**. This log entry did not rerun it.

Original Step 9 was not finished when this paragraph was written. That sentence is superseded by the Phase 7 completion entry below.

The remaining learning goals after Phase 7 are the reviewer and customer API, Step 10 evaluation (40–60 cases, one third held out, zero unauthorized actions, zero duplicate refunds), Step 12's React UI, two Step 11 extensions (Langfuse, and Claude connected only to the Orders MCP server), and Step 12's README, CI, and three-minute demo. The specialist-agent comparison is the extension that stays off the payment path. The phase-by-phase tasks, safety rules, and acceptance checks are in `learning_project.md`.

---

## 21 November 2026 — Phase 7 complete

Phase 7 is original Step 9, workers and recovery. The completion criterion was a timeout followed by a restart with no second refund. This entry records the code and tests. It does not start Phase 8. Database `support` was not written.

`uv run pytest -q` recorded **164 passed** in 193.16 seconds on `support_test`. Ruff passed on the Phase 7 files. The six older Ruff findings were left alone and are not part of this phase: five `SIM117` findings in `tests/integration/test_schema_guards.py`, and one unused `pytest` import (`F401`) in `tests/test_health.py`.

What the phase does:

- `workflows.run`, `payments.reconcile`, and `payments.discover` carry ids. Amounts and approvals are read from Postgres.
- Saving `unknown`, `submitted`, or `processing` publishes `payments.reconcile` after the commit. A broker failure writes `refund_reconcile_enqueue_failed` and leaves the row open. `enqueue_open_reconciliations` publishes that row again.
- Celery Beat entry `discover-open-refunds` publishes `payments.discover` every 60 seconds on queue `celery`. `RECONCILE_DISCOVER_SECONDS` must be positive. `RECONCILE_QUEUE` selects another queue. The values are fixed when the Celery app is imported. The schedule has no amount and no approval.
- Discovery retries the ticket write, then scans `reserved`, `unknown`, `submitted`, and `processing`. Reconciliation only looks the key up. A matching provider record is copied onto the row, including a `reserved` row whose worker died after the provider accepted. A confirmed 404 releases paise only for an `unknown` row at least two minutes old. `reserved`, `submitted`, and `processing` keep the reservation. A lookup timeout asks for another lookup in 30 seconds, up to 8 times.
- `retry_failed_ticket_updates` resolves a ticket only when `ticket_update_failed` is newer than any `ticket_update_retried` audit for that proposal. It does not call the provider and does not change paise. A ticket returned to `open` after the retry stays open.

Tests that prove it, all on `support_test`:

- `test_beat_discovers_an_open_refund_without_a_manual_publish` starts Beat and a worker. The test does not publish `payments.discover`. An open refund becomes `succeeded`, and the mock stays at one create.
- `test_a_killed_worker_is_adopted_by_scheduled_discovery` uses `timeout_after_accept`. The provider stores one refund. The worker process is killed while the ledger is still `reserved`. Beat publishes discovery. The restarted worker adopts that same refund. Provider attempts stay 1, operations stay 1, one `refund_operations` row remains, and committed paise stay at the reserved amount.
- `test_a_celery_worker_adopts_a_provider_record_without_reposting` adopts a seeded provider record through `payments.reconcile` with no second POST.
- `test_celery_workflows_run_finishes_an_order_status_thread` publishes `workflows.run` with the run id only. A live worker completes the thread and creates no proposal.
- `test_a_missed_ticket_update_resolves_without_another_payment` and `test_a_reopened_ticket_stays_open` cover the audit condition and the reopen.
- `test_a_reserved_provider_record_is_adopted_without_a_post` adopts a matching `reserved` row and leaves an aged missing `reserved` row reserved, with its paise unchanged.
- The earlier in-process crash and the two-worker resume tests in `tests/integration/test_worker.py` still pass. They were not weakened.

Four operational limitations remain:

1. Celery Beat publishes `payments.discover` every 60 seconds only while a Beat process is running. The API does not start that process.
2. Discovery adopts the ledger row. It does not resume the LangGraph thread, so the run can stay `awaiting_approval` after the refund is `succeeded`.
3. A `reserved` row with a confirmed 404 stays reserved. Discovery does not POST it. A worker that dies before the provider accepts still needs a later `workflows.run` to submit that reservation.
4. There is no staff API that reopens a ticket. The reopen test writes `open` in `support_test`.

Phase 8, the reviewer queue and persisted customer sentences, has not started.

---

## 21 November 2026 — Phase 9 complete

Phase 9 is original Step 10, safety evaluation. This entry records the first scored harness run. It does not start Phase 10. Application code, migrations, tests, and `evals/expected.json` were not edited to force a pass. Database `support` was not written. Beat was not pointed at `support` or the shared `support_test` ledger.

Command: `uv run python -m evals.harness`.

Measured result:

- **25/25** cases matched the frozen expected ledger
- Runtime **64.76 seconds** (process `elapsed_ms` 64760)
- Database **`support_eval`**
- `unauthorized_refunds` **0**
- `duplicate_payments` **0**
- Expected-ledger SHA-256 `968e054c87960dfa3df3a8eba3b4decd8cfa7e394360254ef2f86c7b67e2ee4b`
- Report preserved at `evals/reports/phase9.json` (also allow-listed in `.gitignore` so the evidence file is not ignored)

Harness layout: `evals/cases.py` (inputs and execution order), `evals/expected.json` (frozen outcomes), `evals/harness.py` (runner). Redis for the harness used database 15. Open-refund cases 7, 21, 22, and 23 ran after discovery cases 24 and 25. Case 25 waited for a `refund_reconcile_absent` audit before the later pay path. Case 21 stayed `unknown` after a finished graph; case 24 adoption left the run `awaiting_approval`, which is an expected Phase 7 limit.

The first attempt aborted before scoring because `_refuse_payment_call` matched its own string literal. That guard was fixed so the contiguous call text is built at runtime; the safeguard still refuses a real harness call to `execute_approved_refund`. The scored run above is the run after that guard fix.

Standing record and remaining roadmap: `learning_project.md`. Next essential phases: 10 (React) and 12 (release). Phase 11 stays off the payment path.

---

## 22 November 2026 — Phase 10 P1 closed (auth + shells)

Phase 10 P1 covers the React SPA foundation only: Vite proxy, API client, auth module, AuthProvider, router and role guards, polished login, customer and reviewer shells. Application backend, migrations, and seed data were not changed for P1. Database used for live UI verify: **`support_ui`**.

**What shipped (frontend).** `frontend/` — React 19 + TypeScript + Tailwind v4. Tokens and Fraunces/DM Sans per the approved visual blueprint. `sessionStorage` key `sr_access_token`. Login sequences `POST /auth/token` then `GET /me`. Role homes: customer `/`, reviewer `/review` (`actor`/`role` from `/me`). Upcoming nav labels are visible but not links and not focusable.

**Live verification (22 Nov 2026).** FastAPI on `:8000` and Vite on `127.0.0.1:5173` against Stage 5 env (`support_ui`, Redis DB 1). Mock payments, Celery, and Beat were not started. Checks that passed: `/health` direct and via Vite proxy; Asha → customer shell; Priya → reviewer shell; wrong-portal redirects; session restore after reload; logout → `/login`; invalid credentials; invalid token → `/login` with token cleared; mobile shell chrome; shell keyboard focus set excludes coming-soon items; `prefers-reduced-motion` disables login panel animation.

**Not in P1.** Orders, new case, case tracking, reviewer queue/decision UI, Playwright suite.

**Next.** Phase 10 P2 — customer orders → new case → case tracking — after an approved assessment. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P2.2 (new case)

P2.2 adds customer case creation on existing APIs only. No backend, migration, or seed changes.

**What shipped.** `frontend/src/api/tickets.ts`, `runs.ts`; `NewCasePage` at `/cases/new`; minimal `CasePage` at `/cases/:ticketId` (one `GET /tickets/{id}`); New case nav live. At this step the cases list was not built yet. It shipped later on 22 November and is current, as the documentation-alignment entry records. Submit: `POST /tickets` → `POST /runs` → navigate. After a ticket succeeds and a run fails, Retry start reuses `ticket_id` and does not create a second ticket. Optional `?orderId=` preselects an owned order.

**Verify.** `npm run build` succeeded. API smoke on `support_ui` as Asha: ticket + run (`created: true`). Celery was not running for that smoke, so `message` stayed null (expected until a worker or P2.3 polling). Browser click-through of the new form was not re-run in this pass.

**Next.** P2.3 message-first case tracking / polling. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P2.3 (message-first polling)

P2.3 upgrades `/cases/:ticketId` to poll quietly. No backend changes.

**Rules (`frontend/src/lib/casePolling.ts`).** Fast 1.5s: `refund_status` in reserved/submitted/processing, or `status === awaiting_approval` (even when message is non-null), or open with null message. Slow 10s: `refund_status === unknown` (never settled). Stop: succeeded/failed refund, resolved/escalated ticket, or non-null message with null refund (reject / order-status / ineligible). Null-message UI distinguishes reserved vs workflow-starting.

**Verify.** `node scripts/check-case-polling.mjs` → 12/12. `npm run build` green. Celery on Redis DB 1 + API on `support_ui`: Asha `order_status` got delivered sentence; `damaged_item` got escalated. Browser UI and live `unknown` path not exercised in this pass.

**Next.** P3 reviewer queue/decision. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P3 (reviewer queue / decision)

P3 adds the reviewer console on existing Phase 8 APIs. No backend changes.

**What shipped.** `frontend/src/api/proposals.ts`; `lib/proposalDecision.ts` (200 / 409 detail-gated / 503 enqueue-failed); `ReviewOverviewPage`, `ReviewQueuePage`, `ProposalDetailPage`; ReviewerShell Overview + Queue nav. Evidence and customer context rendered in separate panels. Decision body is only `approve`|`reject`. Retry resume after 503 reuses the original decision word and does not treat a later 409 as a new outcome.

**Verify.** `node scripts/check-proposal-decision.mjs` 8/8; `npm run build` green. Priya `GET /proposals` OK (0 pending); Asha gets 403. Creating a fresh pending proposal on seed data failed eligibility (already refunded / no return) — full browser approve/reject not run this pass.

**Next.** P4/P5 polish and tests as needed; Phase 12. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P4 Step 4 (case outcome fade-in)

Subtle mount-only entrance for the verified status message on `CasePage`. No poll logic change.

**What shipped.** `.case-outcome-enter` in `frontend/src/index.css` (opacity + 6px translateY, 200ms); class on the outcome `<p>` only when `ticket.message` is set. Named opt-out under `prefers-reduced-motion` with login/shell. No `key` on message text so quiet polls do not remount/replay.

**Verify.** Code review only this pass; browser fade and reduced-motion not re-checked live.

**Next.** Remaining P4 polish if any / P5 UI tests; Phase 12. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P5 Step 1 (Vitest foundation)

Vitest runs against production `casePolling.ts` and `proposalDecision.ts`. No UI test runner yet.

**What shipped.** `vitest@3.2.4`; `vitest.config.ts` (node env, separate from Vite 8 `vite.config.ts` to avoid plugin type clash); `npm test` / `test:watch`; `src/lib/casePolling.test.ts` (12 POLL_MODE_FIXTURES + nullMessageHint); `src/lib/proposalDecision.test.ts` (8 decision fixtures + decisionLocksActions). `scripts/check-*.mjs` now spawn local `vitest.mjs` — no duplicated classify/mode logic.

**Verify.** `npm test` → 27 passed; `npm run build` green; `npm run lint` same pre-existing oxlint warnings only; both `.mjs` wrappers pass.

**Next.** P5 Step 2 only after approval. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P5 Step 2 (API client unit tests)

Unit tests for `formatApiDetail` / `ApiError` and `apiFetch` with mocked `fetch` and `./token` (no real sessionStorage).

**What shipped.** `src/api/errors.test.ts`; `src/api/client.test.ts` — success + Bearer, `auth: false`, 401 clears token, 409 does not clear, network → status 0, empty 200 → null. Production API modules unchanged.

**Verify.** `npm test` → 39 passed; `npm run build` green; `npm run lint` same pre-existing page warnings.

**Next.** P5 Step 3 only after approval. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P5 Step 3 (auth pure modules)

Unit tests for role routing helpers and `login` / `loadMe` / `logout`. No AuthProvider or guards yet (needs RTL).

**What shipped.** `src/auth/roles.test.ts`; `src/auth/auth.test.ts` with mocked `apiFetch` and token helpers. Production auth modules unchanged.

**Verify.** `npm test` → 48 passed; `npm run build` green; `npm run lint` same pre-existing page warnings.

**Next.** P5 Step 4 (RTL) only after approval. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 10 P5 Step 4 (targeted RTL)

jsdom + Testing Library for the two highest-risk pages. Production pages unchanged.

**Deps.** `jsdom`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`. Setup: `src/test/setup.ts` (jest-dom + explicit `cleanup`). UI files use `@vitest-environment jsdom`.

**What shipped.** `ProposalDetailPage.test.tsx`: 503 → Retry same decision + locked actions; 409 conflict unlocks; already-recorded stays locked without Retry. `CasePage.test.tsx`: reserved null-message copy; settled ticket does not re-poll; Refresh calls `getTicket` again. Mocks: `getProposal`/`decideProposal`/`getTicket` only.

**Verify.** `npm test` → 54 passed; `npm run build` green; `npm run lint` same pre-existing page warnings.

**Phase 10 status at this entry.** Essential P0–P5 closed for the pages that existed here. The cases list was added later the same day and is recorded in the documentation-alignment entry below. Deferred then and still deferred: AuthProvider/guards RTL, Playwright, P6 analytics. Standing record: `learning_project.md`.

---

## 22 November 2026 — Phase 12 start (docs + CI)

Release readiness without upgrading `support` or adding deployment platforms.

**What shipped.** Rewrote `README.md` (DB table, JWT boot rule, UI stack, Phase 9 link). `docs/demo-script.md` for Demos A/B/C. ADR 0003 notes deterministic graph. `.env.example` clarifies secrets/DBs. `.github/workflows/ci.yml` runs ruff, pytest on `support_test` (Postgres+Redis services), and frontend `npm test`/`build`. Small ruff cleanups so CI is not red on known SIM117/F401/SIM102.

**Not verified.** Full local pytest re-run; Actions on a remote (this tree was not a git repo when CI was added). No `support` migration.

**Next at this entry.** `git init`/remote, confirm CI green, optional Loom/OBS. The 25 November 2026 entry below is the current standing record. The 22 November alignment that follows this section was true that day and is superseded by that later entry.

---

## 22 November 2026 — Documentation alignment

Read-only review of the current tree, then documentation only. No application code, tests, database, configuration, migrations, or infrastructure were changed in this pass.

**Phase 10 is essentially complete.** `frontend/src/App.tsx` ships customer home, orders, cases list, new case, and case detail, plus reviewer overview, queue, and proposal detail. The cases list is live. It is not a coming-soon item. Product names on those screens come from `order_items.product_name` through `product_names` on the customer and proposal responses. They are display-only: not an eligibility input and not part of the approval hash. Seed indexes 0–2 remain Headphones, Smart watch, and Keyboard. Indexes 3–29 use `DISPLAY_NAMES` in `scripts/seed.py`. The 27 matching `support_ui` rows were renamed in place earlier the same day. Product ids, prices, orders, cases, and refund rows were not rewritten.

**Human in the loop.** A reviewer approves or rejects only a pending refund proposal. Ordinary code executes the refund after the stored approval. Escalated cases (`damaged_item`, failed inspection, missing delivery, missing or conflicting policy) end with the specialist sentence and no proposal. There is no in-app specialist queue, and staff cannot call `GET /tickets`. Ineligible cases that do not escalate also create no staff task.

**Tests and Ruff.** The last full `uv run pytest` recorded in the project is **167 passed** on 21 November 2026. That count is stale because later tests exist, including product-name assertions. The last observed `npm test` on 22 November 2026 was **54 passed**. The old six-finding Ruff note was cleared in the Phase 12 cleanup. This pass did not re-run pytest, npm test, npm build, or Ruff.

**Phase 12 is partial.** README, `docs/demo-script.md`, the ADR 0003 deterministic-graph note, and `.github/workflows/ci.yml` are in the tree. This folder is not a git repository, so there has been no remote push and Actions has never run. The demo video is not recorded.

**Roadmap, in order.** (1) Finish Phase 12 verification: git, remote, full pytest, green Actions, then an optional recording. (2) A specialist queue only after a new approval: read-only escalated tickets, no amount, no decision, no payment; the case must still have zero proposals and zero refund operations. (3) Optional Phase 10 leftovers: new-case product confirmation, AuthProvider RTL, Playwright, order detail. (4) Optional Phase 11: Langfuse and an Orders-MCP desktop client, both off the payment path.

**Not verified in this pass.** A fresh full pytest, a fresh frontend test or build, a fresh Ruff run, the Phase 9 harness, and whether the API or worker processes are up. Standing record: `learning_project.md` and `STATUS.md`.

---

## 25 November 2026 — Documentation aligned with GitHub

Documentation only. No application code, tests, migrations, configuration, CI workflows, or databases were changed. Nothing was committed or pushed in this pass.

**What changed in the story.** The 22 November alignment said this folder was not a git repository and that Actions had never run. That was true then. It is not true now. `main` tracks `origin/main` at `810c677` on `https://github.com/snehapankhi05/support-resolution-agent.git`. The three commits are `a892215` (Integrated project), `3815aa7` (CI lockfile and Ubuntu cleanup), and `810c677` (immediate kill of Celery test workers).

**CI evidence, not a new test run.** [Actions run 3](https://github.com/snehapankhi05/support-resolution-agent/actions/runs/45250933873) on `810c677` completed with success. The backend job’s Ruff step and pytest step succeeded. The frontend job’s install, test, and build steps succeeded. This entry does not record a pytest or Vitest count from that log. The last written full local pytest count remains **167 passed** on 21 November 2026 and is stale. The last written frontend count remains **54 passed** on 22 November 2026. This pass did not rerun either suite, Ruff, or the Phase 9 harness.

**Phase status after this entry.** Phases 0–10 essential scope are complete. Phase 9 remains 25/25 with zero unauthorized refunds and zero duplicate payments (`evals/reports/phase9.json`). Phase 11 (Langfuse, and an Orders MCP desktop client) is not started. Phase 12 is partial: the remote and a green Actions run are done; the demo video is not recorded. There is still no specialist inbox. Beat is still required for scheduled discovery, and discovery still does not resume a paused graph. No LLM decides eligibility, amount, approval, or payment.

**Files.** `STATUS.md`, `README.md`, `learning_project.md`, `docs/architecture.md`, and this log.

