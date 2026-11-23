"""Retrieval and publication rules that do not need Postgres."""

import json
import uuid
from pathlib import Path

import pytest

from app.services.policy_ingest import PolicyIngestError, assert_ingest_database, load_policy
from app.services.refunds import refund_payload
from app.services.retrieval import reciprocal_rank_fusion


def test_rrf_prefers_a_chunk_ranked_by_both_lists():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert fused[0][0] == "b"
    assert fused[0][1] > dict(fused)["a"]


def test_policy_versions_are_sorted_before_hashing():
    payload = refund_payload(
        uuid.uuid4(),
        100,
        policy_effect="allow",
        policy_versions=[
            {"policy_key": "b", "version": 1},
            {"policy_key": "a", "version": 2},
        ],
    )
    assert payload["policy_versions"] == [
        {"policy_key": "a", "version": 2},
        {"policy_key": "b", "version": 1},
    ]


def test_ingest_refuses_the_support_database():
    with pytest.raises(PolicyIngestError, match="support"):
        assert_ingest_database("support")
    assert_ingest_database("support_test")


def test_manifest_window_must_match_the_code_rule(tmp_path: Path):
    markdown = tmp_path / "policy.md"
    markdown.write_text("# Title\n\n## Window\n\nWait 14 days.\n", encoding="utf-8")
    manifest = tmp_path / "policy.json"
    manifest.write_text(
        json.dumps(
            {
                "policy_key": "refund_policy",
                "version": 1,
                "effective_from": "2026-01-01",
                "effective_to": None,
                "source_uri": "memory://bad",
                "applies_to": "refund",
                "effect": "allow",
                "return_window_days": 14,
                "markdown": "policy.md",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PolicyIngestError, match="return_window_days"):
        load_policy(manifest)


def test_manifest_without_sections_is_rejected(tmp_path: Path):
    markdown = tmp_path / "policy.md"
    markdown.write_text("# Title only\n\nNo section headings.\n", encoding="utf-8")
    manifest = tmp_path / "policy.json"
    manifest.write_text(
        json.dumps(
            {
                "policy_key": "refund_policy",
                "version": 1,
                "effective_from": "2026-01-01",
                "source_uri": "memory://empty",
                "applies_to": "refund",
                "effect": "allow",
                "return_window_days": 30,
                "markdown": "policy.md",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PolicyIngestError, match="no sections"):
        load_policy(manifest)
