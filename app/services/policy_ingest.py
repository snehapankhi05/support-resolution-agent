"""Insert one published policy version. A published version is not rewritten."""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import PolicyChunk, PolicyDocument
from app.services.eligibility import RETURN_WINDOW
from app.services.embeddings import EMBEDDING_DIM, Embedder

SUPPORT_DATABASE = "support"


class PolicyIngestError(Exception):
    """The document or manifest cannot be published."""


@dataclass(frozen=True)
class PolicySection:
    section_ref: str
    content: str


@dataclass(frozen=True)
class PolicyDraft:
    policy_key: str
    version: int
    effective_from: date
    effective_to: date | None
    source_uri: str
    applies_to: str
    effect: str
    sections: tuple[PolicySection, ...]


@dataclass(frozen=True)
class IngestResult:
    policy_key: str
    version: int
    chunks: int
    already_present: bool


def assert_ingest_database(name: str | None) -> None:
    """Published rows must not be written to the development database from this step."""
    if name == SUPPORT_DATABASE:
        raise PolicyIngestError("refusing to ingest into database support")


def load_policy(manifest_path: Path) -> PolicyDraft:
    """Read and validate a manifest and its markdown before any database call."""
    path = manifest_path.resolve()
    if not path.is_file():
        raise PolicyIngestError("manifest file is missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyIngestError("manifest is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise PolicyIngestError("manifest must be a JSON object")
    markdown_path = path.parent / str(raw.get("markdown", ""))
    if not markdown_path.is_file():
        raise PolicyIngestError("policy markdown is missing")
    markdown = markdown_path.read_text(encoding="utf-8")
    draft = _draft_from_mapping(raw, markdown, source_uri=str(raw.get("source_uri", "")))
    return draft


def ingest_policy(session: Session, draft: PolicyDraft, embedder: Embedder) -> IngestResult:
    """Insert a new version, or report that the same version is already stored.

    The caller commits. A different body for an existing version raises and
    leaves the stored rows unchanged.
    """
    _validate_draft(draft)
    name = session.scalar(text("SELECT current_database()"))
    assert_ingest_database(name)
    vectors = embedder.embed([section.content for section in draft.sections])
    if len(vectors) != len(draft.sections) or any(len(vector) != EMBEDDING_DIM for vector in vectors):
        raise PolicyIngestError("embedding dimension must be 384")
    existing = session.scalar(
        select(PolicyDocument).where(
            PolicyDocument.policy_key == draft.policy_key,
            PolicyDocument.version == draft.version,
        )
    )
    if existing is not None:
        _require_same_publication(session, existing, draft)
        return IngestResult(draft.policy_key, draft.version, len(draft.sections), True)
    _close_open_versions(session, draft)
    document = PolicyDocument(
        policy_key=draft.policy_key,
        version=draft.version,
        effective_from=draft.effective_from,
        effective_to=draft.effective_to,
        source_uri=draft.source_uri,
    )
    session.add(document)
    session.flush()
    metadata = {"applies_to": draft.applies_to, "effect": draft.effect}
    for section, vector in zip(draft.sections, vectors, strict=True):
        session.add(
            PolicyChunk(
                document_id=document.id,
                section_ref=section.section_ref,
                content=section.content,
                embedding=vector,
                chunk_metadata=metadata,
            )
        )
    session.flush()
    return IngestResult(draft.policy_key, draft.version, len(draft.sections), False)


def _draft_from_mapping(raw: dict, markdown: str, *, source_uri: str) -> PolicyDraft:
    required = (
        "policy_key",
        "version",
        "effective_from",
        "source_uri",
        "applies_to",
        "effect",
        "return_window_days",
        "markdown",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise PolicyIngestError("manifest is missing required fields")
    if not isinstance(raw["policy_key"], str) or not raw["policy_key"].strip():
        raise PolicyIngestError("policy_key is required")
    if isinstance(raw["version"], bool) or not isinstance(raw["version"], int) or raw["version"] < 1:
        raise PolicyIngestError("version must be a positive integer")
    if raw["applies_to"] != "refund":
        raise PolicyIngestError("applies_to must be refund")
    if raw["effect"] not in {"allow", "deny"}:
        raise PolicyIngestError("effect must be allow or deny")
    if raw["return_window_days"] != RETURN_WINDOW.days:
        raise PolicyIngestError("return_window_days does not match the code rule")
    if not isinstance(source_uri, str) or not source_uri.strip():
        raise PolicyIngestError("source_uri is required")
    effective_from = _parse_date(raw["effective_from"], "effective_from")
    effective_to = None
    if raw.get("effective_to") is not None:
        effective_to = _parse_date(raw["effective_to"], "effective_to")
        if effective_to <= effective_from:
            raise PolicyIngestError("effective_to must be after effective_from")
    sections = tuple(_sections(markdown))
    if not sections:
        raise PolicyIngestError("policy markdown has no sections")
    return PolicyDraft(
        policy_key=raw["policy_key"].strip(),
        version=raw["version"],
        effective_from=effective_from,
        effective_to=effective_to,
        source_uri=source_uri.strip(),
        applies_to=raw["applies_to"],
        effect=raw["effect"],
        sections=sections,
    )


def _validate_draft(draft: PolicyDraft) -> None:
    if draft.applies_to != "refund" or draft.effect not in {"allow", "deny"}:
        raise PolicyIngestError("policy metadata is not a refund decision")
    if draft.version < 1 or not draft.sections:
        raise PolicyIngestError("policy draft has no sections")
    refs = [section.section_ref for section in draft.sections]
    if len(refs) != len(set(refs)):
        raise PolicyIngestError("section refs must be unique")
    if any(not section.content.strip() for section in draft.sections):
        raise PolicyIngestError("section content is empty")


def _parse_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise PolicyIngestError(f"{label} must be a date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyIngestError(f"{label} must be a date") from exc


def _sections(markdown: str) -> list[PolicySection]:
    if not markdown.strip():
        raise PolicyIngestError("policy markdown is empty")
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
    if not matches:
        raise PolicyIngestError("policy markdown has no sections")
    sections: list[PolicySection] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        content = markdown[start:end].strip()
        if not content:
            raise PolicyIngestError("section content is empty")
        ref = _slug(match.group(1))
        if not ref or ref in seen:
            raise PolicyIngestError("section refs must be unique")
        seen.add(ref)
        sections.append(PolicySection(ref, content))
    return sections


def _slug(heading: str) -> str:
    lowered = heading.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug


def _require_same_publication(session: Session, document: PolicyDocument, draft: PolicyDraft) -> None:
    if (
        document.effective_from != draft.effective_from
        or document.effective_to != draft.effective_to
        or document.source_uri != draft.source_uri
    ):
        raise PolicyIngestError("published version already exists with different dates")
    stored = session.scalars(
        select(PolicyChunk)
        .where(PolicyChunk.document_id == document.id)
        .order_by(PolicyChunk.section_ref)
    ).all()
    expected = sorted(draft.sections, key=lambda section: section.section_ref)
    if len(stored) != len(expected):
        raise PolicyIngestError("published version already exists with different sections")
    for chunk, section in zip(stored, expected, strict=True):
        metadata = chunk.chunk_metadata if isinstance(chunk.chunk_metadata, dict) else {}
        if chunk.section_ref != section.section_ref or chunk.content != section.content:
            raise PolicyIngestError("published version already exists with different text")
        if metadata.get("applies_to") != draft.applies_to or metadata.get("effect") != draft.effect:
            raise PolicyIngestError("published version already exists with different metadata")


def _close_open_versions(session: Session, draft: PolicyDraft) -> None:
    open_rows = session.scalars(
        select(PolicyDocument).where(
            PolicyDocument.policy_key == draft.policy_key,
            PolicyDocument.effective_to.is_(None),
        )
    ).all()
    for document in open_rows:
        if draft.effective_from <= document.effective_from:
            raise PolicyIngestError("new version must start after the open version")
        document.effective_to = draft.effective_from
