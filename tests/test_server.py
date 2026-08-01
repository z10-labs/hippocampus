"""Covers the five MCP tools at their function boundary — the same code path the
MCP host reaches, minus the transport."""
import json
import os
import re

import pytest

import hippocampus.settings as settings
from hippocampus.indexer import build_index, load_index
from hippocampus.logger import write_deferred_entry
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


def log_confirmed(description, weight=None, category=None, **phase2_kwargs):
    """Drives the real two-phase flow (WP-10): Phase 1 to obtain a token,
    then Phase 2 with confirmed=True and that token. Most tests only care
    about Phase 2's outcome, so this is the standard way to reach it."""
    phase1_out = log_tool(description, weight=weight, category=category)
    token = re.search(r'token="([^"]+)"', phase1_out).group(1)
    return log_tool(
        description, weight=weight, category=category,
        confirmed=True, token=token, **phase2_kwargs,
    )


# --- query ---------------------------------------------------------------

def test_query_says_nothing_recorded_yet_when_no_records_exist(root):
    out = query_tool("anything at all")
    assert "No decision records exist yet in this project" in out


def test_query_flags_records_that_failed_to_parse(root):
    (root / ".decisions" / "records" / "notes.md").write_text("Just some notes, no DR heading.\n")
    out = query_tool("anything at all")
    assert "records found but none could be indexed" in out


def test_query_says_nothing_matched_when_the_index_has_entries_but_none_qualify(root):
    write_record(root, "0001", "Postgres for the ledger")
    build_index(root, force=True)
    # No shared vocabulary with the indexed record — the relevance floor
    # (WP-06) now filters this out on its own merits, no top_n trick needed.
    out = query_tool("favorite pizza toppings for the team lunch")
    assert "No decisions matched this query above the relevance threshold" in out


def test_none_of_the_empty_state_messages_claim_no_constraints_apply(root):
    write_record(root, "0001", "Postgres for the ledger")
    build_index(root, force=True)

    outputs = [
        query_tool("anything", top_n=0),
    ]
    for out in outputs:
        assert "no past constraints apply" not in out.lower()


def test_query_finds_records_without_ever_calling_log_or_build_index(root):
    write_record(root, "0001", "Postgres for the ledger")
    write_record(root, "0002", "Redis for rate limiting")
    # Cold start: no build_index call anywhere in this test. Query each with
    # its own matching text — the two records aren't relevant to each other,
    # and the relevance floor (WP-06) now correctly filters cross-matches.
    assert "DR-0001" in query_tool("Postgres for the ledger")
    assert "DR-0002" in query_tool("Redis for rate limiting")


def test_query_reflects_a_hand_edited_record_without_a_manual_reindex(root):
    path = write_record(root, "0001", "Postgres for the ledger", body="## Why\n\nOriginal reason.\n")
    build_index(root, force=True)
    built_at = load_index(root).built_at

    path.write_text(path.read_text().replace("Original reason.", "Updated reason after review."))
    newer = (built_at / 1000) + 1
    os.utime(path, (newer, newer))

    out = query_tool("Postgres for the ledger")
    assert "Updated reason after review." in out


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


# --- status (WP-04) --------------------------------------------------------

def test_query_flags_a_superseded_record_on_its_own_id_line(root):
    write_record(root, "0001", "Redis Streams for events")
    build_index(root, force=True)

    log_confirmed(
        "Kafka for events",
        relationships=json.dumps([{"type": "supersedes", "target": "DR-0001"}]),
        alternatives=json.dumps(["Redis Streams — no consumer group replay"]),
    )
    # log_tool's own incremental reindex can race apply_supersedes's rewrite
    # of DR-0001 within the same millisecond (mtime truncates to int ms; see
    # WP-02's implementer note), which is a genuine but separate flakiness in
    # build_index's per-file skip check, not something this test should mask
    # or exercise. Force a full rebuild so this test asserts rendering, not
    # incremental-reindex timing.
    build_index(root, force=True)

    out = query_tool("Redis Streams for events")
    dr0001_line = next(line for line in out.splitlines() if line.startswith("DR-0001"))
    assert "SUPERSEDED BY DR-0002" in dr0001_line
    # The still-live superseding record must not carry the marker.
    dr0002_line = next(line for line in out.splitlines() if line.startswith("DR-0002"))
    assert "SUPERSEDED" not in dr0002_line


# --- deferred (WP-05) -------------------------------------------------------

def test_query_flags_deferred_entries_as_not_yet_decided(root):
    write_deferred_entry(root, "Multi-region replication, revisit post-MVP", why="Not enough data yet.")
    build_index(root, force=True)

    out = query_tool("Multi-region replication, revisit post-MVP")
    def0001_line = next(line for line in out.splitlines() if line.startswith("DEF-0001"))
    assert "NOT YET DECIDED" in def0001_line
    assert "(deferred)" in def0001_line


def test_query_does_not_show_a_rejected_line_for_deferred_entries(root):
    write_deferred_entry(root, "Multi-region replication, revisit post-MVP", why="Not enough data yet.")
    build_index(root, force=True)

    out = query_tool("Multi-region replication")
    assert "Rejected" not in out


def test_list_filters_by_weight_deferred(root):
    write_record(root, "0001", "Postgres ledger", weight="standard")
    write_deferred_entry(root, "Multi-region replication, revisit post-MVP")
    build_index(root, force=True)

    out = list_tool(weight="deferred")
    assert "DEF-0001" in out
    assert "DR-0001" not in out


def test_chain_on_a_deferred_id_does_not_crash(root):
    write_deferred_entry(root, "Multi-region replication, revisit post-MVP")
    build_index(root, force=True)

    out = chain_tool("DEF-0001")
    assert "DEF-0001" in out
    assert "NOT YET DECIDED" in out


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


# --- two-phase enforcement (WP-10) ------------------------------------------

def test_confirmed_true_with_no_token_writes_nothing_and_returns_phase_one(root):
    out = log_tool("sliding window rate limiter", confirmed=True)

    assert "token" in out.lower()
    assert "Classification:" in out  # Phase 1 content, regenerated
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_phase_one_then_phase_two_with_the_returned_token_writes_the_record(root):
    phase1_out = log_tool("sliding window rate limiter", weight="standard")
    token = re.search(r'token="([^"]+)"', phase1_out).group(1)

    out = log_tool("sliding window rate limiter", weight="standard", confirmed=True, token=token)

    assert "Record written" in out
    assert len(list((root / ".decisions" / "records").iterdir())) == 1


def test_a_stale_token_for_a_changed_description_is_rejected(root):
    phase1_out = log_tool("sliding window rate limiter", weight="standard")
    token = re.search(r'token="([^"]+)"', phase1_out).group(1)

    # Description edited after Phase 1 — the token was issued for different text.
    out = log_tool("sliding window rate limiter v2", weight="standard", confirmed=True, token=token)

    assert "doesn't match" in out
    assert "Classification:" in out  # regenerated Phase 1 content, not written
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_an_unrecognized_token_is_rejected(root):
    out = log_tool("sliding window rate limiter", weight="standard", confirmed=True, token="not-a-real-token")

    assert "doesn't match" in out
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_a_token_is_single_use(root):
    phase1_out = log_tool("sliding window rate limiter", weight="standard")
    token = re.search(r'token="([^"]+)"', phase1_out).group(1)

    log_tool("sliding window rate limiter", weight="standard", confirmed=True, token=token)
    # Reusing the same token for a second, distinct decision must not work.
    out = log_tool("a completely different decision", weight="standard", confirmed=True, token=token)

    assert "doesn't match" in out or "none was supplied" in out
    assert len(list((root / ".decisions" / "records").iterdir())) == 1


def test_the_deferred_path_still_works_end_to_end_through_the_token_flow(root):
    out = log_confirmed("holding off on sharding until post-MVP")

    assert "Deferred decision recorded" in out
    assert "sharding" in (root / ".decisions" / "deferred.md").read_text()


def test_log_phase_two_writes_the_record_and_reindexes(root):
    out = log_confirmed(
        "sliding window rate limiter in Redis",
        title="Sliding window rate limiter",
        why="Token bucket bursts at the window edge",
        alternatives=json.dumps(["Token bucket — bursts at the window edge"]),
    )
    assert "Record written" in out

    records = list((root / ".decisions" / "records").iterdir())
    assert len(records) == 1

    # The index must be updated in the same call — not left stale.
    assert [e.id for e in load_index(root).entries] == ["DR-0001"]


def test_log_persists_declared_relationships(root):
    write_record(root, "0001", "Redis for shared counters")
    build_index(root, force=True)

    log_confirmed(
        "sliding window rate limiter",
        relationships=json.dumps([{"type": "depends-on", "target": "DR-0001"}]),
        alternatives=json.dumps(["Token bucket — bursts at the window edge"]),
    )

    entry = next(e for e in load_index(root).entries if e.id == "DR-0002")
    assert [(r.type, r.target) for r in entry.relationships] == [("depends-on", "DR-0001")]


def test_log_supersede_marks_the_old_record_and_reports_it(root):
    write_record(root, "0001", "Redis Streams for events")
    build_index(root, force=True)

    out = log_confirmed(
        "Kafka for events",
        relationships=json.dumps([{"type": "supersedes", "target": "DR-0001"}]),
        alternatives=json.dumps(["Redis Streams — no consumer group replay"]),
    )

    assert "superseded by DR-0002" in out
    old = (root / ".decisions" / "records" / "0001-redis-streams-for-events.md").read_text()
    assert "**Status**: superseded by DR-0002" in old


def test_log_rejects_malformed_relationship_json_without_writing(root):
    out = log_confirmed("some decision", relationships="{not json}")

    assert "Error parsing relationships JSON" in out
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_log_routes_a_deferral_to_the_deferred_file(root):
    out = log_confirmed("holding off on sharding until post-MVP")

    assert "Deferred decision recorded" in out
    assert "sharding" in (root / ".decisions" / "deferred.md").read_text()
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_log_persists_why_and_review_trigger_for_a_deferral(root):
    log_confirmed(
        "holding off on sharding until post-MVP",
        why="Premature at current scale.",
        review_trigger="user table crosses 10M rows.",
    )

    content = (root / ".decisions" / "deferred.md").read_text()
    assert "**Why deferred**: Premature at current scale." in content
    assert "**Review trigger**: user table crosses 10M rows." in content


def test_explicit_weight_and_category_override_the_classifier(root):
    log_confirmed(
        "some vague thing", weight="heavy", category="domain",
        alternatives=json.dumps(["Doing nothing — status quo was untenable"]),
    )

    entry = load_index(root).entries[0]
    assert entry.weight == "heavy"
    assert entry.category == "domain"


# --- alternatives (WP-03) -------------------------------------------------

def test_log_rejects_a_heavy_record_with_no_alternatives(root):
    out = log_confirmed("Kafka for events")  # architectural -> heavy by default

    assert "alternatives" in out.lower()
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_log_rejects_malformed_alternatives_json_without_writing(root):
    out = log_confirmed(
        "sliding window rate limiter", weight="standard",
        alternatives="{not json}",
    )

    assert "Error parsing alternatives JSON" in out
    assert list((root / ".decisions" / "records").iterdir()) == []


def test_log_writes_supplied_alternatives_into_the_record(root):
    log_confirmed(
        "Postgres LISTEN/NOTIFY for the job queue",
        alternatives=json.dumps([
            "RabbitMQ — extra ops burden",
            "Redis streams — no durability guarantee we need",
        ]),
    )

    content = (root / ".decisions" / "records" / "0001-postgres-listen-notify-for-the-job-queue.md").read_text()
    assert "- RabbitMQ — extra ops burden" in content
    assert "- Redis streams — no durability guarantee we need" in content


def test_alternatives_survive_the_full_round_trip_to_query(root):
    log_confirmed(
        "Postgres LISTEN/NOTIFY for the job queue",
        alternatives=json.dumps([
            "RabbitMQ — extra ops burden",
            "Redis streams — no durability guarantee we need",
        ]),
    )
    build_index(root, force=True)

    out = query_tool("Postgres LISTEN/NOTIFY for the job queue")
    assert "Rejected: RabbitMQ — extra ops burden" in out
    assert "Redis streams — no durability guarantee we need" in out


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


def test_list_shows_the_superseded_marker(root):
    write_record(root, "0001", "Old approach", status="superseded by DR-0002")
    build_index(root, force=True)

    out = list_tool()
    assert "SUPERSEDED BY DR-0002" in out


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


def test_chain_flags_an_unknown_root_id_without_raising(root):
    write_record(root, "0001", "A")
    build_index(root, force=True)

    out = chain_tool("DR-9999")
    assert "not in index" in out


def test_chain_shows_the_superseded_marker(root):
    write_record(root, "0001", "Old approach", status="superseded by DR-0002")
    build_index(root, force=True)

    assert "SUPERSEDED BY DR-0002" in chain_tool("DR-0001")


# --- blast radius (WP-09) --------------------------------------------------

def test_chain_lists_everything_that_depends_on_the_target_under_blast_radius(root):
    write_record(root, "0001", "A")
    write_record(root, "0002", "B", body="## Relationships\n\n- depends-on: DR-0001\n")
    write_record(root, "0003", "C", body="## Relationships\n\n- depends-on: DR-0001\n")
    build_index(root, force=True)

    out = chain_tool("DR-0001")

    blast_section = out.split("blast radius")[1]
    assert "DR-0002" in blast_section
    assert "DR-0003" in blast_section


def test_chain_blast_radius_follows_transitive_dependents(root):
    write_record(root, "0001", "A")
    write_record(root, "0002", "B", body="## Relationships\n\n- depends-on: DR-0001\n")
    write_record(root, "0003", "C", body="## Relationships\n\n- depends-on: DR-0002\n")
    build_index(root, force=True)

    out = chain_tool("DR-0001")

    blast_section = out.split("blast radius")[1]
    assert "DR-0002" in blast_section
    assert "DR-0003" in blast_section  # transitively affected, not just the direct dependent


def test_chain_shows_the_root_once_even_when_a_cycle_loops_back_to_it(root):
    write_record(root, "0001", "A", body="## Relationships\n\n- depends-on: DR-0002\n")
    write_record(root, "0002", "B", body="## Relationships\n\n- depends-on: DR-0001\n")
    build_index(root, force=True)

    out = chain_tool("DR-0001")  # must terminate in both directions
    # The root itself must appear exactly once (as the ▶ header) and never
    # be re-rendered as a child bullet, even though A depends on B and B
    # depends on A.
    assert out.count("▶ DR-0001") == 1
    assert "└─ DR-0001" not in out
    assert "DR-0002" in out


def test_chain_depth_cap_engages_on_a_long_dependency_chain(root):
    # Build a straight-line chain of 15 records, each depending on the next —
    # longer than MAX_CHAIN_DEPTH (10).
    n = 15
    for i in range(n, 0, -1):
        body = f"## Relationships\n\n- depends-on: DR-{i + 1:04d}\n" if i < n else ""
        write_record(root, f"{i:04d}", f"Step {i}", body=body)
    build_index(root, force=True)

    out = chain_tool("DR-0001")
    ids_present = [f"DR-{i:04d}" in out for i in range(1, n + 1)]
    # Some deep id must be missing — the walk was cut off, not exhaustive.
    assert not all(ids_present)
