# The 2-minute explanation (practice out loud)

**Problem.** "Customers write in saying a refund hasn't arrived. Support staff have to find
the order, check the return and payment, read the policy, decide, act and update the ticket."

**What I built.** "An agent that does the investigation and drafts a proposal with evidence
and policy citations. A human reviewer approves it, then ordinary code executes the refund."

**The key rule.** "The AI suggests. Code decides and acts. A human approves money.
The model can't choose which customer it acts for, can't run the refund, and can't approve
itself."

**The hard part.** "Never paying twice. Refunds use idempotency keys, database constraints
and reserve-then-call transactions. If the payment API times out, the system marks the result
unknown and reconciles with the provider before retrying."

**MCP.** "The agent's tools are MCP servers that verify identity themselves from a signed
token. I deliberately kept refund execution out of MCP so no model-reachable path can move
money."

**How I know it works.** "I test business outcomes in the database across N synthetic cases,
including retries, crashes, concurrency and prompt-injection, and I report human-assisted and
fully automated resolution separately."

---

## Questions to prepare answers for
1. Why not give the agent a refund tool with an approval flag?
2. Why is a LangGraph checkpoint not your ledger?
3. What happens if the server crashes right after the provider accepts the refund?
4. Why use MCP at all for one app? What did it cost you? (You have a plain-tools baseline.)
5. How do you stop a prompt injection hidden in a policy document?
6. What did you decide *not* to build, and why?
