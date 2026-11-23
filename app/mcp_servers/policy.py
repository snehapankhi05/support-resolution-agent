"""Policy search MCP server. Chunks are public policy text, still behind a customer token."""

from app.mcp_servers.queries import search_policy
from app.mcp_servers.results import ToolResult
from app.mcp_servers.server import POLICY_URL, build_server, require_customer

server = build_server("policy", POLICY_URL)


@server.tool(name="search_policy")
async def search_policy_text(query: str) -> ToolResult:
    """Search current policy text and return citations. A match is not permission to refund."""
    _customer_id, error = require_customer()
    if error is not None:
        return error
    return search_policy(query)


def main() -> None:
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8102,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
