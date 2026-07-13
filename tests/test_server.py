"""Covers the five MCP tools at their function boundary — the same code path the
MCP host reaches, minus the transport."""
import json

import pytest

import hippocampus.settings as settings
from hippocampus.indexer import build_index, load_index
from hippocampus.server import (
    hippocampus_chain as chain_tool,
    hippocampus_classify as classify_tool,
    hippocampus_list as list_tool,
    hippocampus_log as log_tool,
    hippocampus_query as query_tool,
)

from conftest import write_record


@pytest.fixture(autouse=True)
def point_server_at_tmp_root(root, monkeypatch):
    monkeypatch.setattr(settings, "ROOT", root)
    return root


# --- query ---------------------------------------------------------------

def test_query_on_empty_index_tells_the_agent_to_proceed(root):
    out = query_tool("anything at all")
    assert "No relevant decisions found" in out
    assert "Proceed" in out


def test_query_surfaces_why_and_rejected_alternatives_inline(root):
    write_record(
        root, "0001", "Postgres for the ledger",
        body="## Why\n\nRelational integrity across accounts.\n\n"
             "## Alternatives Skipped\n\n- DynamoDB — no cross-partition transactions\n",
    )
    build_index(root, force=True)

    out = query_tool("Postgres for the ledger")
    assert "DR-0001" in out
    assert "Why: Relational integrity across accounts." in out
    assert "Rejected: DynamoDB — no cross-partition transactions" in out


def test_query_says_so_when_no_alternatives_were_documented(root):
    write_record(root, "0001", "Postgres for the ledger")
    build_index(root, force=True)
    assert "Rejected: (none documented)" in query_tool("Postgres for the ledger")


# --- classify ------------------------------------------------------------

def test_classify_recommends_recording_a_real_decision():
    out = classify_tool("use mTLS for service authentication")
    assert "Weight: heavy" in out
    assert "Category: security" in out
    assert "Record recommended: yes" in out


def test_classify_writes_nothing(root):
    classify_tool("use mTLS for service authentication")
    assert list((root / ".decisions" / "records").iterdir()) == []


# --- log -----------------------------------------------------------------

def test_log_refuses_implementation_details(root):
    out = log_tool("rename the variable name from x to count")
    assert "not worth recording" in out
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_log_phase_one_suggests_but_does_not_write(root):
    write_record(root, "0001", "Redis for shared counters", body="## Why\n\nShared across replicas.\n")
    build_index(root, force=True)

    out = log_tool("Redis for shared counters in the rate limiter")

    assert "Classification:" in out
    assert "confirmed=True" in out
    # Phase 1 must not create a record.
    assert len(list((root / ".decisions" / "records").iterdir())) == 1


def test_log_phase_one_surfaces_related_records_as_link_candidates(root):
    write_record(root, "0001", "Redis for shared counters", body="## Why\n\nShared across replicas.\n")
    build_index(root, force=True)

    out = log_tool("Redis for shared counters")
    assert "Related decisions found" in out
    assert "DR-0001" in out


def test_log_phase_two_writes_the_record_and_reindexes(root):
    out = log_tool(
        "sliding window rate limiter in Redis",
        confirmed=True,
        title="Sliding window rate limiter",
        why="Token bucket bursts at the window edge",
    )
    assert "Record written" in out

    records = list((root / ".decisions" / "records").iterdir())
    assert len(records) == 1

    # The index must be updated in the same call — not left stale.
    assert [e.id for e in load_index(root).entries] == ["DR-0001"]


def test_log_persists_declared_relationships(root):
    write_record(root, "0001", "Redis for shared counters")
    build_index(root, force=True)

    log_tool(
        "sliding window rate limiter",
        confirmed=True,
        relationships=json.dumps([{"type": "depends-on", "target": "DR-0001"}]),
    )

    entry = next(e for e in load_index(root).entries if e.id == "DR-0002")
    assert [(r.type, r.target) for r in entry.relationships] == [("depends-on", "DR-0001")]


def test_log_supersede_marks_the_old_record_and_reports_it(root):
    write_record(root, "0001", "Redis Streams for events")
    build_index(root, force=True)

    out = log_tool(
        "Kafka for events",
        confirmed=True,
        relationships=json.dumps([{"type": "supersedes", "target": "DR-0001"}]),
    )

    assert "superseded by DR-0002" in out
    old = (root / ".decisions" / "records" / "0001-redis-streams-for-events.md").read_text()
    assert "**Status**: superseded by DR-0002" in old


def test_log_rejects_malformed_relationship_json_without_writing(root):
    out = log_tool("some decision", confirmed=True, relationships="{not json}")

    assert "Error parsing relationships JSON" in out
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_log_routes_a_deferral_to_the_deferred_file(root):
    out = log_tool("holding off on sharding until post-MVP", confirmed=True)

    assert "Deferred decision recorded" in out
    assert "sharding" in (root / ".decisions" / "deferred.md").read_text()
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_explicit_weight_and_category_override_the_classifier(root):
    log_tool("some vague thing", weight="heavy", category="domain", confirmed=True)

    entry = load_index(root).entries[0]
    assert entry.weight == "heavy"
    assert entry.category == "domain"


# --- list ----------------------------------------------------------------

def test_list_reports_when_nothing_matches_the_filter(root):
    write_record(root, "0001", "Postgres ledger", category="data")
    build_index(root, force=True)

    assert "No records match" in list_tool(category="security")


def test_list_filters_by_category(root):
    write_record(root, "0001", "Postgres ledger", category="data")
    write_record(root, "0002", "mTLS everywhere", category="security")
    build_index(root, force=True)

    out = list_tool(category="security")
    assert "DR-0002" in out
    assert "DR-0001" not in out


def test_list_filters_by_weight(root):
    write_record(root, "0001", "Postgres ledger", weight="standard")
    write_record(root, "0002", "mTLS everywhere", weight="heavy")
    build_index(root, force=True)

    out = list_tool(weight="heavy")
    assert "DR-0002" in out
    assert "DR-0001" not in out


# --- chain ---------------------------------------------------------------

def test_chain_on_an_empty_index_explains_itself(root):
    assert "Index is empty" in chain_tool("DR-0001")


def test_chain_walks_the_full_transitive_dependency_tree(root):
    write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger", body="## Relationships\n\n- depends-on: DR-0001\n")
    write_record(root, "0003", "Read models", body="## Relationships\n\n- depends-on: DR-0002\n")
    build_index(root, force=True)

    out = chain_tool("DR-0003")

    # All three levels, not just the immediate parent.
    assert "DR-0003" in out
    assert "DR-0002" in out
    assert "DR-0001" in out


def test_chain_accepts_a_lowercase_id(root):
    write_record(root, "0001", "Event sourced core")
    build_index(root, force=True)
    assert "DR-0001" in chain_tool("dr-0001")


def test_chain_survives_a_dependency_cycle(root):
    write_record(root, "0001", "A", body="## Relationships\n\n- depends-on: DR-0002\n")
    write_record(root, "0002", "B", body="## Relationships\n\n- depends-on: DR-0001\n")
    build_index(root, force=True)

    out = chain_tool("DR-0001")  # must terminate, not recurse forever
    assert "DR-0001" in out
    assert "DR-0002" in out


def test_chain_flags_a_dangling_reference(root):
    write_record(root, "0001", "A", body="## Relationships\n\n- depends-on: DR-0099\n")
    build_index(root, force=True)

    assert "not in index" in chain_tool("DR-0001")
