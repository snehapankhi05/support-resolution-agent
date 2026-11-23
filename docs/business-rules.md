# Business rules v1 (synthetic)

These invented rules drive both the policy documents (what the AI retrieves) and the
deterministic eligibility code (what actually decides). The AI may *quote* them; only code
*enforces* them. All money is stored in integer minor units (paise).

## Refund eligibility
| # | Rule |
|---|---|
| R1 | A refund is possible only for a **delivered** order owned by the requesting customer. |
| R2 | The customer must start the return within **30 days of delivery**. |
| R3 | The return must be **received** by the warehouse **and** inspection must be **passed**. |
| R4 | Refund amount = the paid amount of the returned items. Never more than `paid - already committed refunds`. |
| R5 | An order with a refund already **reserved, processing or succeeded** for the same amount is **not** refunded again; the system reports the existing refund's status instead. |
| R6 | **Every** refund requires approval by a reviewer. Approvals expire after **24 hours**. |
| R7 | If inspection **failed**, no refund is proposed; the case is escalated to a human. |
| R8 | If no applicable policy can be found, or policy evidence conflicts, the case is **escalated**. |

## Order status
| # | Rule |
|---|---|
| S1 | Report only the latest status returned by the shipping record; never guess a delivery date. |
| S2 | If the record is missing or stale, say so and escalate rather than infer. |

## Customer message by real state
| System state | What the customer is told |
|---|---|
| Proposal pending approval | "Your refund request is awaiting approval." |
| Refund submitted or processing at provider | "The payment service accepted your refund request. Processing is still pending." |
| Refund succeeded (provider confirmed) | "Your refund of ₹X has been confirmed." |
| Outcome uncertain (`unknown`) | "I couldn't confirm the refund outcome yet. The case is being checked." |
| Ineligible | The applicable policy, in plain words, with its citation. |
| Escalated | "A support specialist will review your case." |

`submitted`, `processing`, `succeeded`, `failed` and `unknown` are always kept distinct.
