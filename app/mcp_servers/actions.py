"""Proposal MCP server. It stores a pending proposal and never calls the payment provider."""

import uuid

from app.mcp_servers.queries import create_refund_proposal as store_refund_proposal
from app.mcp_servers.results import ToolResult
from app.mcp_servers.server import ACTIONS_URL, build_server, require_customer

server = build_server("actions", ACTIONS_URL)


@server.tool(name="create_refund_proposal")
async def create_refund_proposal(ticket_id: uuid.UUID) -> ToolResult:
    """Propose a refund for a ticket that already has a workflow run.

    The amount is computed in code. This tool does not approve or pay it.
    """
    customer_id, error = require_customer()
    if error is not None:
        return error
    return store_refund_proposal(customer_id, ticket_id)


def main() -> None:
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8103,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
