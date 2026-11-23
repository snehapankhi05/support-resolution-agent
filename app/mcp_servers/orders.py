"""Read-only orders MCP server. The customer id comes from the verified token."""

import uuid

from app.mcp_servers.queries import read_order, read_refund_status, read_return
from app.mcp_servers.results import ToolResult
from app.mcp_servers.server import ORDERS_URL, build_server, require_customer

server = build_server("orders", ORDERS_URL)


@server.tool()
async def get_order(order_id: uuid.UUID) -> ToolResult:
    """Return one order owned by the authenticated customer."""
    customer_id, error = require_customer()
    if error is not None:
        return error
    return read_order(customer_id, order_id)


@server.tool()
async def get_return(order_id: uuid.UUID) -> ToolResult:
    """Return the return record for an order owned by the authenticated customer."""
    customer_id, error = require_customer()
    if error is not None:
        return error
    return read_return(customer_id, order_id)


@server.tool()
async def get_refund_status(order_id: uuid.UUID) -> ToolResult:
    """Return the latest ledger refund for an order owned by the authenticated customer."""
    customer_id, error = require_customer()
    if error is not None:
        return error
    return read_refund_status(customer_id, order_id)


def main() -> None:
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8101,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
