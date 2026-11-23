"""Bearer check for MCP servers. The customer id comes from the token, not the tool."""

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from app.auth.tokens import TokenError, read_mcp_customer

CUSTOMER_SCOPE = "customer"


class CustomerTokenVerifier:
    """Accept only a support-mcp token whose subject is customer:{uuid}."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            customer_id = read_mcp_customer(token)
        except TokenError:
            return None
        return AccessToken(
            token=token,
            client_id="support-mcp",
            scopes=[CUSTOMER_SCOPE],
            subject=str(customer_id),
        )


def mcp_auth(resource_url: str) -> AuthSettings:
    """Tell the SDK this process is a resource server. Audience is checked in the verifier."""
    return AuthSettings(
        issuer_url="http://127.0.0.1:8000",
        resource_server_url=resource_url,
        validate_token_resource=False,
        required_scopes=[CUSTOMER_SCOPE],
    )


def current_customer_id() -> str | None:
    """The customer primary key stored by the SDK auth middleware, if the request had one."""
    access = get_access_token()
    if access is None or not access.subject:
        return None
    return access.subject
