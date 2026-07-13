import pytest

from hippocampus.indexer import build_index
from hippocampus.retriever import _cosine, _rel_label, query

from conftest import write_record


def test_cosine_of_orthogonal_vectors_is_zero():
    assert _cosine([1, 0, 0], [0, 1, 0]) == 0.0


def test_cosine_of_identical_vectors_is_one():
    assert _cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_cosine_of_a_zero_vector_does_not_divide_by_zero():
    assert _cosine([0, 0, 0], [1, 2, 3]) == 0.0


def test_query_on_an_empty_index_returns_nothing(root):
    assert query(root, "anything") == []


def test_direct_hits_are_ranked_by_similarity(root):
    write_record(root, "0001", "Redis for shared counters")
    write_record(root, "0002", "Typography scale for the design system")
    build_index(root, force=True)

    results = query(root, "Redis for shared counters", top_n=2)

    assert results[0].id == "DR-0001"
    assert results[0].surfaced_via == "direct"
    assert results[0].score > results[1].score


def test_a_hit_drags_in_what_it_depends_on(root):
    """Outbound expansion — the constraints behind the match."""
    write_record(root, "0001", "Event sourced core")
    write_record(
        root, "0002", "Postgres ledger",
        body="## Relationships\n\n- depends-on: DR-0001\n",
    )
    build_index(root, force=True)

    results = query(root, "Postgres ledger", top_n=1)
    by_id = {r.id: r for r in results}

    assert by_id["DR-0002"].surfaced_via == "direct"
    assert by_id["DR-0001"].surfaced_via == "relationship"
    assert by_id["DR-0001"].relationship_type == "depends-on"


def test_a_hit_drags_in_what_depends_on_it(root):
    """Inbound expansion — the blast radius. This is the load-bearing behaviour:
    querying the thing you want to remove must surface what breaks."""
    write_record(root, "0001", "Redis for shared counters")
    write_record(
        root, "0002", "Sliding window rate limiter",
        body="## Relationships\n\n- depends-on: DR-0001\n",
    )
    build_index(root, force=True)

    results = query(root, "Redis for shared counters", top_n=1)
    by_id = {r.id: r for r in results}

    assert by_id["DR-0001"].surfaced_via == "direct"
    assert by_id["DR-0002"].surfaced_via == "relationship"
    assert by_id["DR-0002"].relationship_type == "depended-on-by"


def test_each_record_is_surfaced_at_most_once(root):
    write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger", body="## Relationships\n\n- depends-on: DR-0001\n")
    write_record(root, "0003", "Read models", body="## Relationships\n\n- depends-on: DR-0001\n")
    build_index(root, force=True)

    results = query(root, "Event sourced core", top_n=3)
    ids = [r.id for r in results]
    assert len(ids) == len(set(ids))


def test_depends_on_is_exposed_inline_so_no_file_read_is_needed(root):
    write_record(root, "0001", "Event sourced core")
    write_record(root, "0002", "Postgres ledger", body="## Relationships\n\n- depends-on: DR-0001\n")
    build_index(root, force=True)

    result = next(r for r in query(root, "Postgres ledger") if r.id == "DR-0002")
    assert result.depends_on == ["DR-0001"]


def test_rel_labels_are_human_readable():
    assert _rel_label("depended-on-by") == "Depended on by"
    assert _rel_label("supersedes") == "Supersedes"
    # Unknown types degrade to the raw string rather than blowing up.
    assert _rel_label("invented-type") == "invented-type"
