# Demo scenarios and success targets

All data is synthetic. Amounts are shown in rupees; stored in paise.

## Seed data used by the demos
| Order | Customer | Item | Paid | State |
|---|---|---|---|---|
| ORD-1001 | Asha | Headphones | ₹2,499 (249900) | Delivered 10 days ago, return received, inspection **passed**, no refund yet |
| ORD-1002 | Rohan | Smart watch | ₹8,999 (899900) | Delivered 45 days ago, no return started |
| ORD-1003 | Meera | Keyboard | ₹1,299 (129900) | Delivered 6 days ago, return received, inspection **passed**, no refund yet |

## Demo A: successful approved refund (ORD-1001)
1. Asha: "I returned my headphones last week but have no refund."
2. Agent finds order, return and payment; cites refund policy section; proposes ₹2,499.
3. Customer sees "awaiting approval".
4. Reviewer sees evidence and citations, approves.
5. Code revalidates, reserves ₹2,499, calls the mock provider once.
6. **Expected state:** 1 refund operation `succeeded`, order `refund_committed_minor = 249900`,
   ticket resolved, audit trail complete, customer told the refund is confirmed.

## Demo B: correctly blocked request (ORD-1002)
1. Rohan: "Refund my smart watch."
2. Agent finds delivery 45 days ago, no return; cites the 30-day rule (R2) and R3.
3. **Expected state:** no proposal for payment, **0** refund operations, no provider call,
   customer given the policy explanation, escalated only if the case is ambiguous.

## Demo C: failure recovery (ORD-1003)
1. Same flow as A, but the mock provider is set to **accept the refund and then time out**.
2. The operation is marked `unknown`; the customer sees "I couldn't confirm yet".
3. The application is restarted mid-flow.
4. The reconcile job queries the provider by idempotency key and finds the refund.
5. **Expected state:** exactly **1** provider refund record and 1 refund operation, now
   `succeeded`; no second refund; ticket updated from verified status.

## Success targets
| Metric | Target | How it's measured |
|---|---|---|
| Duplicate refunds | **0** | DB assertions + mock-provider records under retries, restarts, concurrency |
| Unauthorized actions or data access | **0** | Adversarial test cases (cross-customer access, forged amounts, prompt injection) |
| Verified resolution rate | Report as measured | Resolvable cases whose expected end-state is confirmed ÷ resolvable cases |
| Overall workflow correctness | Report as measured | Correct outcome, clarification, refusal or escalation ÷ all cases |
| Policy retrieval hit rate | Report as measured | Required policy section present in retrieved results |
| Latency, model calls, cost per case | Report as measured | Active time only; reviewer wait time reported separately |

Do not publish percentages before running the evaluation. Describe results as
synthetic-test results, never as production performance.
