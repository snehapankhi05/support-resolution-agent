# ADR 0002: MCP for reads and proposals; execution stays in backend code

**Status:** accepted

## Context
The agent needs tools. MCP gives a standard, reusable, independently deployable tool layer.
Refund execution is the one action that moves money.

## Decision
- Orders (read), policy (search) and actions (create proposal) are MCP servers over
  streamable HTTP, each validating a short-lived signed token and reading the customer
  from it. Tool schemas have no `customer_id`.
- `execute_approved_refund` is **not** an MCP tool. Only backend code calls it, after a stored
  approval and revalidation.
- A plain-Python-tools baseline is kept to measure what MCP costs and what it gives.

## Consequences
- A bug or prompt injection in the agent cannot reach a money-moving path.
- Extra network hop and more services to run; the baseline quantifies this.
- The same servers can be used from the MCP Inspector and other MCP hosts.
