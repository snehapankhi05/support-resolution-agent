"""Print one short-lived MCP token for a named customer. This is not an HTTP route.

Usage: uv run python -m scripts.issue_mcp_token asha@seed.example

The command runs only when the connected database name is support.
"""

import sys

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.tokens import issue_mcp_token
from app.db.models import LoginAccount
from app.db.session import SessionLocal

DEVELOPMENT_DATABASE = "support"


def token_for_email(session: Session, email: str) -> str:
    """Sign a support-mcp token for the customer who owns this login."""
    account = session.scalar(select(LoginAccount).where(LoginAccount.email == email.strip().lower()))
    if account is None or account.customer_id is None:
        raise SystemExit("customer login not found")
    return issue_mcp_token(account.customer_id)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: python -m scripts.issue_mcp_token EMAIL")
    with SessionLocal() as session:
        name = session.scalar(text("SELECT current_database()"))
        if name != DEVELOPMENT_DATABASE:
            raise SystemExit(
                f"Refusing to issue an MCP token on database {name!r}. "
                "This command only runs against support."
            )
        print(token_for_email(session, args[0]))


if __name__ == "__main__":
    main()
