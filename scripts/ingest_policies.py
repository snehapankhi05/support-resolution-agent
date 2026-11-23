"""Publish the refund policy into an allowed database.

Allowed: support_test (pytest) and support_ui (Phase 10 local UI).
Refused: support (development default) and support_eval (Phase 9 evidence).
The library still refuses support on every ingest_policy call.
This command does not change an existing published version.

Usage: uv run python -m scripts.ingest_policies
"""

from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.embeddings import FastEmbedder
from app.services.policy_ingest import PolicyIngestError, ingest_policy, load_policy

ALLOWED_DATABASES = frozenset({"support_test", "support_ui"})
REFUSED_DATABASES = frozenset({"support", "support_eval"})
MANIFEST = Path(__file__).resolve().parents[1] / "policies" / "refund_policy_v1.json"


def assert_ingest_cli_database(name: str | None) -> str:
    """Return the database name when the CLI may write policy rows there."""
    if name in REFUSED_DATABASES:
        raise SystemExit(f"ingest command refuses database {name!r}")
    if name not in ALLOWED_DATABASES:
        raise SystemExit(
            f"ingest command only runs against support_test or support_ui, not {name!r}"
        )
    return name


def main() -> None:
    draft = load_policy(MANIFEST)
    embedder = FastEmbedder()
    with SessionLocal() as session:
        name = assert_ingest_cli_database(session.scalar(text("SELECT current_database()")))
        try:
            result = ingest_policy(session, draft, embedder)
            session.commit()
        except PolicyIngestError as exc:
            session.rollback()
            raise SystemExit(str(exc)) from exc
    state = "already_present" if result.already_present else "inserted"
    print(
        f"database {name} policy {result.policy_key} v{result.version} "
        f"{state} chunks {result.chunks}"
    )


if __name__ == "__main__":
    main()
