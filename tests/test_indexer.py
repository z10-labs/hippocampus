from hippocampus.indexer import (
    _parse_alternatives,
    _parse_file,
    _parse_relationships,
    _parse_why,
    _reverse_type,
    build_index,
    load_index,
)

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
