# STATUS

## Current step
Phase 10 essential UI is **complete**. Phase 12 is **partial**. Phase 9 remains complete. Phase 11 is not started. P6 analytics stays excluded.

Phase 12 has the README, demo script, ADR 0003 note, and GitHub Actions workflow in the tree. Git setup, a remote push, a fresh full pytest, and a green Actions run are still pending. The demo video is not recorded.

## Phase 9 result (21 November 2026)
- Command: `uv run python -m evals.harness`
- Score: **25/25** cases matched the frozen ledger
- Runtime: **64.76 seconds**
- Database: **`support_eval`** (not `support`, not `support_test`)
- `unauthorized_refunds`: **0**
- `duplicate_payments`: **0**
- Expected-ledger SHA-256: `968e054c87960dfa3df3a8eba3b4decd8cfa7e394360254ef2f86c7b67e2ee4b`
- Report (preserved): [`evals/reports/phase9.json`](evals/reports/phase9.json)
- Harness files: `evals/cases.py`, `evals/expected.json`, `evals/harness.py`
- Application code, migrations, and expected results were not edited to force the pass

## Done
- Step 0 docs: scope, business rules, architecture, demo scenarios, pitch, ADRs
- Cursor setup: AGENTS.md, scoped rules in .cursor/rules, refund-safety-review skill, PROMPTS.md
- Step 1 skeleton: Docker Postgres (host **5433**) + Redis, Alembic `0001`, early pytest
- Step 2: models, seed, eligibility, reserve-then-call refund execution
- Step 3: mock provider, lookup-before-POST, adopt match, `unknown` keeps the reservation on the execution path
- Step 4: JWT auth, customer order/ticket APIs, demo logins on `support` via provision script
- Step 5: MCP orders/policy/actions on 8101–8103; no execute tool
- Step 6: Policy RAG (effective-dated retrieval, RRF). Library ingest refuses `support`. CLI ingest allows `support_test` and `support_ui`; refuses `support` and `support_eval`
- Phases 0–6: deterministic LangGraph workflow, `POST /runs`, Celery `workflows.run`, staff approve/reject, reconciler that never POSTs
- Phase 7 (Step 9): workers, Beat discovery, crash-after-accept adoption; four operational limits remain (see `learning_project.md`)
- Phase 8: reviewer queue (`GET /proposals`), `tickets.resolution_message` (migration `0005`), verified customer sentences
- Phase 9 (Step 10): end-to-end ledger harness on `support_eval`, **25/25**, report above
- Last full pytest recorded after Phase 8: `uv run pytest -q` **167 passed** in 218.82 seconds on `support_test`. That count is stale. Later tests were added, including product-name checks. This documentation pass did not rerun the full suite, Ruff, or `npm test`.
- Phase 10 essential UI (22 Nov 2026): customer home, orders, cases list, new case, case detail with polling and a mount-only outcome fade; reviewer overview, queue, and proposal detail. Last observed `npm test`: **54 passed**. Deferred: AuthProvider/guard RTL, Playwright, order-detail page, selected-product line under the new-case dropdown, P6 analytics.
- Product names are display-only. Customer orders and tickets, and reviewer proposals, load `order_items.product_name`. The name is not in the approval hash and is not an eligibility input. Seed indexes 0–2 stay Headphones, Smart watch, and Keyboard. Indexes 3–29 use `DISPLAY_NAMES` in `scripts/seed.py`. Those 27 `support_ui` rows were renamed in place on 22 Nov 2026. Product ids, prices, orders, cases, and refund rows were not rewritten. `scripts.seed` still deletes and recreates seed data if someone runs it.
- Human in the loop: a reviewer approves or rejects only a pending refund proposal. Code executes the refund after the stored approval. Escalated cases (`damaged_item`, failed inspection, missing delivery, missing or conflicting policy) store “A support specialist will review your case.” and create no proposal. There is no in-app specialist queue. Staff cannot call `GET /tickets`. Ineligible non-escalating cases also create no staff task.
- Phase 10 P0: `frontend/` Vite React TS + Tailwind v4 tokens + proxy to `:8000`
- Phase 10 **P1 CLOSED** (22 Nov 2026): API client, auth module, AuthProvider (`sessionStorage`), router + role guards, polished login, customer/reviewer shells. Live verify on `support_ui`: Asha→customer shell, Priya→reviewer shell, wrong-portal redirects, session restore, logout, invalid credentials, invalid token, responsive shells, keyboard (disabled nav not focusable), reduced-motion, `/health` via Vite proxy. Mock/Celery/Beat not required for P1. No provider URL; no UI path to `execute_approved_refund`.
- Phase 10 **P2.1** (22 Nov 2026): Orders list + live Orders nav (`GET /orders`).
- Phase 10 **P2.2** (22 Nov 2026): New case form (`POST /tickets` → `POST /runs`), Retry-start on run failure, navigate to `/cases/:ticketId` with one-shot `GET /tickets/{id}`.
- Phase 10 **P2.3** (22 Nov 2026): Message-first polling on `CasePage` (`lib/casePolling.ts`: fast 1.5s / slow 10s for `unknown` / stop). Quiet updates; manual Refresh; null-message hints. Truth table 12/12. Worker smoke: order_status + damaged_item.
- Phase 10 **P3** (22 Nov 2026): Reviewer overview/queue/detail. `api/proposals.ts` + `lib/proposalDecision.ts` (detail-gated 409; 503 enqueue-failed = decision saved + Retry resume). Evidence vs `customer_context` separate on detail. Nav: Overview + Queue. `npm run build` green; decision truth table 8/8. API: Priya `GET /proposals` (empty pending OK); Asha → 403. Live approve/reject against a pending row not exercised this pass (seed orders not eligible for a new pending proposal without returns).
- Phase 10 **P4 Steps 1–4** (22 Nov 2026): Honest customer home tiles; deleted `ReviewerHomeStub`; shared `StatusBadge` + `InlineAlert`; case outcome `.case-outcome-enter` fade (mount-only, reduced-motion opt-out).
- Phase 10 **P5 Step 1** (22 Nov 2026): Vitest 3.2.4 (`npm test`); `casePolling.test.ts` (12 fixtures) + `proposalDecision.test.ts` (8 fixtures) import production modules; `.mjs` checks are thin vitest wrappers. No RTL/jsdom/Playwright.
- Phase 10 **P5 Step 2** (22 Nov 2026): `errors.test.ts` + `client.test.ts` (mocked `fetch` + `./token`); 401 clears token; non-401 errors do not; network → status 0. Still no RTL/jsdom/Playwright.
- Phase 10 **P5 Step 3** (22 Nov 2026): `roles.test.ts` + `auth.test.ts` (mocked `apiFetch` + token). AuthProvider / route-guard RTL deferred.
- Phase 10 **P5 Step 4** (22 Nov 2026): RTL + jsdom; `ProposalDetailPage.test.tsx` (503 Retry same decision, 409 unlock, already-recorded lock); `CasePage.test.tsx` (reserved null copy, stop polling, Refresh). `npm test` 54 passed. P6 analytics not started.

## Phase 12 progress (22 Nov 2026)
- README aligned: DB isolation, JWT rule, UI stack order, Phase 9 report link, ADR 0003 clarification.
- Demo runbook: [`docs/demo-script.md`](docs/demo-script.md) (A/B/C). The video is not recorded.
- CI workflow: [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — ruff + pytest (`support_test`) + frontend test/build. Does not migrate `support`.
- Ruff clean-up for CI: schema-guard `with` combines, unused pytest import, harness importlib side-effects. The older “six Ruff findings remain” note is out of date. This documentation pass did not re-run Ruff.
- **Still pending:** this folder is not a git repository, so there is no remote and Actions has never run. A full `uv run pytest` has not been recorded since 21 Nov.

## Next
1. Finish Phase 12 verification: git init, push, full pytest on `support_test`, confirm Actions green, then optionally record Demos A/B/C.
2. Specialist escalation queue, only after a separate approval: a read-only staff list of escalated tickets, with category, product name, and the verified sentence. No amount, no approve/reject, and no payment call. An escalated case must still have zero proposals and zero refund operations.
3. Optional Phase 10 leftovers: new-case product confirmation, AuthProvider RTL, Playwright, order-detail page.
4. Optional Phase 11: Langfuse, and a desktop client on the Orders MCP server only. Both stay off the payment path.

## Customer cases list (22 Nov 2026)
- Shipped: `CasesPage` at `/cases`, live Cases nav, home tile → `/cases`. Uses `GET /tickets` only. Escalated rows are highlighted. No `created_at` in TicketResponse, so none is shown. This is not future work.
- Phase 11 is not started. If it is approved later: Langfuse, and Orders-MCP desktop only. `scripts/issue_mcp_token` refuses every DB except `support` unless a test-safe path is approved.
- Do not upgrade `support` without explicit approval.
- Do not run `alembic upgrade` against `support` until that write is approved.
- Do not run Beat or the Phase 9 harness against `support` or the shared `support_test` ledger.
- Do not edit `evals/expected.json` to match a product bug.
- Postgres is on localhost:5433; Redis is on localhost:6379. Mock provider port 9001. API port 8000. MCP ports 8101, 8102, and 8103.

## Step 0 checklist (tick when true)
- [ ] Tools installed and verified (git, uv, Python 3.11+, Docker Desktop, Node)
- [ ] Repo on GitHub, 2+ commits, no `.env` visible
- [ ] Cursor opened on this folder; rules visible in Customize > Rules
- [ ] Prompt 0.1 to 0.4 run; findings fixed
- [ ] `docs/notes.md` written in my own words
- [ ] I can give the 2-minute pitch (docs/pitch.md) without reading it

## Open questions
- Which free LLM provider is the project's default (Groq, Gemini)?
- Embedding model: bge-small (384 dims) assumed by the migration

## Working agreement
Plan mode first, one small task per chat, tests are the gate, no weakening tests or
constraints, I explain money/auth code before committing.
