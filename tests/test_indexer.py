import os

from hippocampus.indexer import (
    _parse_alternatives,
    _parse_file,
    _parse_relationships,
    _parse_why,
    _reverse_type,
    build_index,
    ensure_index,
    load_index,
)
from hippocampus.retriever import query

from conftest import write_record


def test_parses_explicit_relationship_bullets():
    content = "## Relationships\n\n- depends-on: DR-0001\n- supersedes: DR-0002\n"
    rels = _parse_relationships(content)
    assert [(r.type, r.target) for r in rels] == [
        ("depends-on", "DR-0001"),
        ("supersedes", "DR-0002"),
    ]


def test_bare_dr_mention_in_prose_becomes_a_reference():
    content = "## Why\n\nThis builds on DR-0007 and its queue model.\n\n## Relationships\n\n- (none)\n"
    rels = _parse_relationships(content)
    assert [(r.type, r.target) for r in rels] == [("references", "DR-0007")]


def test_prose_mention_does_not_duplicate_an_explicit_link():
    content = (
        "## Why\n\nSupersedes DR-0002 because it never scaled.\n\n"
        "## Relationships\n\n- supersedes: DR-0002\n"
    )
    rels = _parse_relationships(content)
    assert [(r.type, r.target) for r in rels] == [("supersedes", "DR-0002")]


def test_why_is_truncated_for_inline_display():
    content = "## Why\n\n" + ("word " * 200) + "\n"
    why = _parse_why(content)
    assert len(why) <= 220
    assert why.endswith("…")


def test_alternatives_drops_because_continuation_lines():
    content = "## Alternatives Skipped\n\n- DynamoDB — no cross-partition txns\n- because it would need a rewrite\n"
    assert _parse_alternatives(content) == "DynamoDB — no cross-partition txns"


def test_alternatives_accepts_dash_star_and_numbered_bullets():
    content = (
        "## Alternatives Considered\n\n"
        "- RabbitMQ — extra ops burden\n"
        "* Redis streams — no durability guarantee\n"
        "1. DynamoDB — no cross-partition txns\n"
    )
    assert _parse_alternatives(content) == (
        "RabbitMQ — extra ops burden\n"
        "Redis streams — no durability guarantee\n"
        "DynamoDB — no cross-partition txns"
    )


def test_parse_file_returns_none_for_non_record(tmp_path):
    stray = tmp_path / "notes.md"
    stray.write_text("Just some notes, no DR heading.\n")
    assert _parse_file(stray) is None


def test_reverse_type_inverts_known_links():
    assert _reverse_type("depends-on") == "depended-on-by"
    assert _reverse_type("supersedes") == "superseded-by"
    assert _reverse_type("conflicts-with") == "conflicts-with"  # symmetric


def test_build_index_populates_reverse_links(root):
    write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger", body="## Relationships\n\n- depends-on: DR-0001\n")

    stats = build_index(root, force=True)
    assert stats["indexed"] == 2

    index = load_index(root)
    by_id = {e.id: e for e in index.entries}

    assert [(r.type, r.target) for r in by_id["DR-0002"].relationships] == [("depends-on", "DR-0001")]
    # DR-0001 never declares anything, but must learn it is depended upon.
    assert [(r.type, r.source) for r in by_id["DR-0001"].reverse_links] == [("depended-on-by", "DR-0002")]


def test_rebuild_skips_unmodified_records(root):
    write_record(root, "0001", "Event sourced core")
    build_index(root, force=True)

    stats = build_index(root, force=False)
    assert stats["indexed"] == 0
    assert stats["skipped"] == 1
    assert stats["total"] == 1


def test_force_reindexes_everything(root):
    write_record(root, "0001", "Event sourced core")
    build_index(root, force=True)

    stats = build_index(root, force=True)
    assert stats["indexed"] == 1
    assert stats["skipped"] == 0


def test_load_index_on_missing_file_is_empty_not_an_error(tmp_path):
    index = load_index(tmp_path)
    assert index.entries == []
    assert index.built_at == 0


# --- deletion --------------------------------------------------------------

def test_build_index_prunes_records_whose_file_was_deleted(root):
    path = write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger")
    build_index(root, force=True)

    path.unlink()
    stats = build_index(root, force=False)

    assert stats["removed"] == 1
    ids = [e.id for e in load_index(root).entries]
    assert ids == ["DR-0002"]


def test_deleting_a_dependency_does_not_leave_a_phantom_relationship(root):
    dep_path = write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger", body="## Relationships\n\n- depends-on: DR-0001\n")
    build_index(root, force=True)

    dep_path.unlink()
    build_index(root, force=False)

    # Must not crash, and the deleted id must not resolve to a live entry.
    results = query(root, "Postgres ledger")
    assert all(r.id != "DR-0001" for r in results)


# --- ensure_index ------------------------------------------------------------

def test_ensure_index_builds_from_scratch_on_cold_start(root):
    write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger")

    ensure_index(root)  # no build_index call has ever happened

    ids = sorted(e.id for e in load_index(root).entries)
    assert ids == ["DR-0001", "DR-0002"]


def test_ensure_index_picks_up_a_hand_edited_record(root):
    path = write_record(root, "0001", "Postgres for the ledger", body="## Why\n\nOriginal reason.\n")
    build_index(root, force=True)
    built_at = load_index(root).built_at

    path.write_text(path.read_text().replace("Original reason.", "Updated reason."))
    newer = (built_at / 1000) + 1
    os.utime(path, (newer, newer))

    ensure_index(root)

    entry = next(e for e in load_index(root).entries if e.id == "DR-0001")
    assert "Updated reason." in entry.why


def test_ensure_index_prunes_after_a_record_is_deleted(root):
    path = write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger")
    build_index(root, force=True)

    path.unlink()
    ensure_index(root)

    ids = [e.id for e in load_index(root).entries]
    assert ids == ["DR-0002"]


def test_ensure_index_is_a_noop_when_records_dir_is_missing(tmp_path):
    ensure_index(tmp_path)  # no .decisions/records/ at all
    assert not (tmp_path / ".hippocampus" / "index.json").exists()


def test_ensure_index_does_not_rebuild_when_nothing_changed(root, monkeypatch):
    path = write_record(root, "0001", "Event sourced core")
    build_index(root, force=True)
    built_at = load_index(root).built_at

    # Force the file mtime safely earlier than the build, so this test isn't
    # flaky about landing in the same millisecond as build_index's clock read.
    older = (built_at - 1000) / 1000
    os.utime(path, (older, older))

    calls = []
    monkeypatch.setattr(
        "hippocampus.indexer.build_index",
        lambda *a, **k: calls.append(1) or {"indexed": 0, "skipped": 1, "total": 1, "removed": 0},
    )
    ensure_index(root)
    assert calls == []
