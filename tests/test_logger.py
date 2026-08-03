from hippocampus.logger import (
    _next_id,
    _slug,
    apply_supersedes,
    write_deferred_entry,
    write_heavy_record,
    write_standard_record,
)
from hippocampus.types import ClassificationResult, Relationship

from conftest import write_record

STANDARD = ClassificationResult(weight="standard", category="performance", reason="test")
HEAVY = ClassificationResult(weight="heavy", category="security", reason="test")


def test_ids_start_at_0001_and_increment(root):
    assert _next_id(root) == "0001"
    write_record(root, "0001", "First")
    assert _next_id(root) == "0002"


def test_next_id_continues_past_the_highest_existing(root):
    write_record(root, "0009", "Nine")
    write_record(root, "0003", "Three")
    assert _next_id(root) == "0010"


def test_slug_is_filesystem_safe_and_bounded():
    slug = _slug("Redis: shared counters & rate limits!! (v2)")
    assert slug == "redis-shared-counters-rate-limits-v2"
    assert len(_slug("x" * 200)) <= 50


def test_standard_record_carries_the_relationship_block(root):
    path = write_standard_record(
        root, "sliding window limiter", STANDARD,
        title="Sliding window limiter",
        why="Token bucket bursts at the window edge",
        relationships=[Relationship(type="depends-on", target="DR-0006")],
    )
    content = open(path).read()
    assert "# DR-0001: Sliding window limiter" in content
    assert "**Weight**: standard" in content
    assert "Token bucket bursts at the window edge" in content
    assert "- depends-on: DR-0006" in content


def test_record_with_no_relationships_says_so_explicitly(root):
    path = write_standard_record(root, "some decision", STANDARD)
    assert "- (none)" in open(path).read()


def test_standard_record_with_no_alternatives_falls_back_to_the_placeholder(root):
    path = write_standard_record(root, "some decision", STANDARD)
    assert "_See description above._" in open(path).read()


def test_standard_record_writes_one_bullet_per_alternative(root):
    path = write_standard_record(
        root, "sliding window limiter", STANDARD,
        alternatives=["Token bucket — bursts at the window edge", "Fixed window — allows edge bursts"],
    )
    content = open(path).read()
    assert "- Token bucket — bursts at the window edge" in content
    assert "- Fixed window — allows edge bursts" in content
    assert "_See description above._" not in content


def test_heavy_record_adds_consequences_and_review_trigger(root):
    path = write_heavy_record(
        root, "mTLS between services", HEAVY,
        title="mTLS between services",
        review_trigger="If we add a third-party service to the mesh",
    )
    content = open(path).read()
    assert "**Weight**: heavy" in content
    assert "## Consequences" in content
    assert "If we add a third-party service to the mesh" in content
    assert "_No alternatives documented._" in content


def test_heavy_record_writes_one_bullet_per_alternative(root):
    path = write_heavy_record(
        root, "mTLS between services", HEAVY,
        alternatives=["No mTLS, VPC-only — insufficient given multi-tenant nodes"],
    )
    content = open(path).read()
    assert "- No mTLS, VPC-only — insufficient given multi-tenant nodes" in content
    assert "_No alternatives documented._" not in content


def test_deferred_entries_append_rather_than_overwrite(root):
    write_deferred_entry(root, "multi-region, revisit post-MVP")
    path = write_deferred_entry(root, "sharding, revisit at 10M rows")

    content = open(path).read()
    assert "multi-region" in content
    assert "sharding" in content


def test_deferred_entry_with_no_why_or_trigger_falls_back_to_placeholders(root):
    path = write_deferred_entry(root, "multi-region, revisit post-MVP")
    content = open(path).read()
    assert "**Why deferred**: Not documented" in content
    assert "**Review trigger**: Not specified" in content


def test_deferred_entry_records_why_and_review_trigger_when_supplied(root):
    path = write_deferred_entry(
        root, "sharding, revisit at 10M rows",
        why="Premature at current scale.",
        review_trigger="user table crosses 10M rows.",
    )
    content = open(path).read()
    assert "**Why deferred**: Premature at current scale." in content
    assert "**Review trigger**: user table crosses 10M rows." in content


def test_supersedes_flips_the_target_status_in_place(root):
    write_record(root, "0002", "Redis Streams", status="accepted")

    assert apply_supersedes(root, "DR-0021", "DR-0002") is True

    content = (root / ".decisions" / "records" / "0002-redis-streams.md").read_text()
    assert "**Status**: superseded by DR-0021" in content
    # The decision itself must survive — only its status changes.
    assert "# DR-0002: Redis Streams" in content


def test_supersedes_on_a_missing_target_reports_failure(root):
    assert apply_supersedes(root, "DR-0021", "DR-9999") is False
