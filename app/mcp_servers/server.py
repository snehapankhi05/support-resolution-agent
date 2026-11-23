"""Build one MCP server. Auth is the SDK bearer middleware, not a second checker."""

import uuid

from mcp.server import MCPServer

from app.mcp_servers.auth import CustomerTokenVerifier, current_customer_id, mcp_auth
from app.mcp_servers.queries import customer_uuid
from app.mcp_servers.results import ToolResult, fail

ORDERS_URL = "http://127.0.0.1:8101"
POLICY_URL = "http://127.0.0.1:8102"
ACTIONS_URL = "http://127.0.0.1:8103"


def build_server(name: str, resource_url: str) -> MCPServer:
    return MCPServer(
        name,
        token_verifier=CustomerTokenVerifier(),
        auth=mcp_auth(resource_url),
    )


def require_customer() -> tuple[uuid.UUID, None] | tuple[None, ToolResult]:
    """Return the customer id, or a structured failure when the middleware did not set one."""
    customer_id = customer_uuid(current_customer_id())
    if customer_id is None:
        return None, fail("UNAUTHORIZED", "authentication required")
    return customer_id, None
