# Support Resolution Agent

An approval-gated customer support agent. It investigates a complaint, retrieves the
applicable policy, **proposes** an action, waits for a human reviewer, and then lets
ordinary code execute it safely and exactly once. The customer only ever hears what has
been verified.

> **Core rule: the AI suggests; code decides and acts; a human approves money.**

Synthetic data and a mock payment provider only. This is a portfolio project, not a
production payment system.

## The problem
A customer writes: *"I returned my headphones last week but still haven't got my refund."*
Support staff must find the order, check the return and payment, apply the right policy,
decide, act, and update the ticket. This project automates the investigation and the
paperwork while keeping a human in control of money.

## Scope (v1)

| In scope | Out of scope (on purpose) |
|---|---|
| Order-status questions | Chargebacks and fraud adjudication |
| Refund requests (full refund of a returned item) | Partial returns and partial refunds |
| One fictional electronics store, one currency (INR) | Multiple stores or currencies |
| One customer issue per workflow | Multi-issue conversations |
| Demo UI only (no real email or SMS) | Real customer messaging |
| Mock payment, shipping and ticket services | Real payment providers |
| Damaged-item escalation path | Analytics dashboards without backend data |

Business rules: [`docs/business-rules.md`](docs/business-rules.md).

## Who is involved

| Actor | Role |
|---|---|
| **Customer** | Submits a complaint, sees an honest status |
| **Reviewer** | Approves or rejects every refund |
| **Deterministic workflow** | Investigates and proposes. Cannot move money |
| **Background workers** | Run workflows, execute approved refunds, reconcile uncertain results |
| **Mock payment provider** | Stands in for Razorpay/Stripe, with switchable failures |

## What the agent path can and cannot do

**Can:** read order, return, payment and ticket data (for the authenticated customer only);
search policy; write a refund *proposal* with evidence and citations.

**Never:**
- choose which customer it acts for (identity comes from the login token),
- execute a refund or call the payment provider,
- approve its own proposal,
- supply reviewer identity, approval status or payment credentials,
- decide the refund amount (code recomputes it).

`execute_approved_refund` is **not** an MCP tool and **not** reachable from the React UI.

## Architecture
Diagrams and limits: [`docs/architecture.md`](docs/architecture.md). Decisions: [`docs/adr/`](docs/adr/).

| Piece | Role |
|---|---|
| **FastAPI** | Auth, tickets, proposals, runs, status |
| **LangGraph** | Deterministic workflow with pause/resume for approval (category from the ticket; resume value ignored) |
| **MCP servers** | Orders / policy / actions — controlled tools; no execute-refund tool |
| **PostgreSQL + pgvector** | Business data, ledger, audit, policy chunks, checkpoints |
| **Celery + Redis** | `workflows.run`, reconcile, Beat discovery |
| **React (Vite)** | Customer portal + reviewer console in `frontend/` |

**Note on ADR 0003:** an earlier proposal said an LLM would classify the request and draft the reply. That proposal is history. The shipped workflow does not call a language model. The stored ticket category selects the branch, and ordinary code decides eligibility, the amount, approval, and payment. Do not assume a live LLM is required for the demos or for pytest.

### Databases (intentional isolation)

| Database | Use |
|---|---|
| `support` | Default `.env` URL. **Do not** casually `alembic upgrade` here |
| `support_test` | Pytest only (auto-created/migrated; refuses `support`) |
| `support_ui` | Live UI / worker demos |
| `support_eval` | Phase 9 ledger harness only |

## Success targets
Defined in [`docs/demo-scenarios.md`](docs/demo-scenarios.md). Headline targets:
**0 duplicate refunds** and **0 unauthorized actions**. Phase 9 scored **25/25** on
`support_eval` — report: [`evals/reports/phase9.json`](evals/reports/phase9.json).

## Quick start (tests)

```powershell
docker compose up -d          # Postgres :5433 (pgvector) + Redis :6379
uv sync
uv run pytest                 # uses support_test only; never migrates support
```

Copying `.env.example` → `.env` is optional for pytest (conftest sets a test JWT and
rewrites `DATABASE_URL` to `support_test`).

**Do not** run `uv run alembic upgrade head` against the default `support` URL as part of
setup. That upgrade needs an explicit, approved target database.

## Run the UI demo stack

Use a dedicated DB such as **`support_ui`** (recommended) and a Redis DB that matches the
worker (example: Redis DB **1**). Set a real JWT secret (≥32 chars, not the example default —
the API refuses to start on `change-me-in-real-use`).

```powershell
docker compose up -d
# Create/migrate support_ui with an explicit DATABASE_URL (approved write to that DB only)
# Seed + provision logins; ingest policies into support_ui when RAG is needed

uv run python -m mock_services.payments          # :9001
# Celery worker (and Beat for reconcile demos) with the same REDIS_URL / DATABASE_URL
uv run uvicorn app.main:app --reload             # :8000
cd frontend; npm install; npm run dev            # :5173, proxies API
```

Demo logins (after seed/provision): customers like `asha@seed.example`, reviewer
`priya@seed.example`, password `demo-password`.

Step-by-step Demo A/B/C: [`docs/demo-script.md`](docs/demo-script.md).

## Frontend checks

```powershell
cd frontend
npm test
npm run build
npm run lint
```

## CI
GitHub Actions (`.github/workflows/ci.yml`): Ruff + pytest on `support_test` (Postgres +
Redis services), and frontend `npm test` + `npm run build`. The job never targets
database `support`.

## Status and roadmap
- Live status: [`STATUS.md`](STATUS.md)
- Phase roadmap / limits: [`learning_project.md`](learning_project.md)
- Phases 0–10 essential scope are complete. Phase 9 is 25/25 on `support_eval` (0 unauthorized refunds, 0 duplicate payments).
- Phase 11 (Langfuse, and an Orders MCP desktop client) is not started.
- Phase 12 is partial: the repo is on GitHub and the latest Actions run on `main` is green. The demo video is not recorded.
- Reviewers and admins have a read-only Escalations inbox, separate from the pending refund queue. It lists escalated tickets and does not assign, resolve, approve, reject, or execute a payment.

## Known limitations
Synthetic data only. Phase 9 is 25 ledger cases, not a statistical holdout. Mock provider
semantics may differ from real providers. No language model decides eligibility, amount,
approval, or payment. Celery Beat must be running for scheduled discovery, and discovery
updates the ledger without resuming a paused graph. See `learning_project.md`. This repo
is not a production deployment guide. The latest recorded CI success is the GitHub Actions run on commit `d41e212`. That run passed. No Actions run number or URL is recorded for it. The earlier success remains
[Actions run 3](https://github.com/snehapankhi05/support-resolution-agent/actions/runs/45250933873)
on commit `810c677`. That page shows the jobs passed. It is not a pasted test count.
