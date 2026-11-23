"""HTTP client for MCP tests. In-process Client() skips the auth middleware."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

FORBIDDEN_FIELDS = {"customer_id", "reviewer_id", "approval_status", "amount_minor"}


@asynccontextmanager
async def connected(app: Any, token: str | None) -> AsyncIterator[ClientSession]:
    """One Streamable HTTP session. The app lifespan can start only once."""
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8101", headers=headers
        ) as http, streamable_http_client("http://127.0.0.1:8101/mcp", http_client=http) as (
            read,
            write,
        ), ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    content = getattr(result, "content", None) or []
    if content and getattr(content[0], "text", None):
        return json.loads(content[0].text)
    raise AssertionError(repr(result))


async def schemas(session: ClientSession) -> list[dict[str, Any]]:
    listed = await session.list_tools()
    return [{"name": tool.name, "schema": tool.input_schema} for tool in listed.tools]


async def http_status(app: Any, headers: dict[str, str], *, path: str = "/mcp") -> int:
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8101") as http:
            response = await http.post(
                path,
                headers={**headers, "content-type": "application/json", "accept": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            return response.status_code


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def schema_fields(schema: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                found.update(str(key) for key in properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found
