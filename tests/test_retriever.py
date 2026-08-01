import random

import numpy as np
import pytest

from hippocampus.indexer import _normalize, build_index
from hippocampus.retriever import _cosine, _rel_label, _score_all, query
from hippocampus.types import IndexEntry

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
        root,
        "0002",
        "Postgres ledger",
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
        root,
        "0002",
        "Sliding window rate limiter",
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


# --- status (WP-04) --------------------------------------------------------


def test_results_carry_the_source_records_status(root):
    write_record(root, "0001", "Postgres ledger")
    build_index(root, force=True)

    result = next(r for r in query(root, "Postgres ledger") if r.id == "DR-0001")
    assert result.status == "accepted"


def test_a_superseded_record_is_demoted_below_an_accepted_one_at_equal_similarity(root):
    body = "## Why\n\nSame content, so both entries embed identically.\n"
    write_record(root, "0001", "Old approach", body=body, status="superseded by DR-0002")
    write_record(root, "0002", "Old approach", body=body, status="accepted")
    build_index(root, force=True)

    results = query(root, "Old approach", top_n=2)
    by_id = {r.id: r for r in results}

    # Both are surfaced — superseded records are demoted, never filtered out.
    assert set(by_id) == {"DR-0001", "DR-0002"}
    assert by_id["DR-0002"].score > by_id["DR-0001"].score


# --- relevance floor (WP-06) -------------------------------------------------
# Scores are injected via monkeypatching _score_all (the vectorized scoring
# function query() actually calls, since WP-07) rather than relying on
# fake_embed's natural output or asserting against the MIN_DIRECT_SCORE
# constant directly, so retuning that constant doesn't break these tests.


def test_direct_hits_below_the_relevance_floor_are_filtered_out(root, monkeypatch):
    write_record(root, "0001", "Some record")
    build_index(root, force=True)

    from hippocampus import retriever

    monkeypatch.setattr(retriever, "_score_all", lambda matrix, q: [0.01] * len(matrix))

    assert query(root, "anything") == []


def test_min_score_override_is_respected(root, monkeypatch):
    write_record(root, "0001", "Some record")
    build_index(root, force=True)

    from hippocampus import retriever

    monkeypatch.setattr(retriever, "_score_all", lambda matrix, q: [0.01] * len(matrix))

    # The default floor excludes this score; an explicit, lower override lets
    # the same result through.
    results = query(root, "anything", min_score=0.0)
    assert [r.id for r in results] == ["DR-0001"]


def test_a_relationship_expanded_result_at_score_zero_is_exempt_from_the_floor(root, monkeypatch):
    write_record(root, "0001", "Event sourced core")
    write_record(
        root,
        "0002",
        "Postgres ledger",
        body="## Relationships\n\n- depends-on: DR-0001\n",
    )
    build_index(root, force=True)

    from hippocampus import retriever
    from hippocampus.indexer import load_index

    # _score_all now receives a bare matrix, not the entry objects, so match
    # by position: query()'s zip(index.entries, raw_scores) pairs them up in
    # this same load_index(root).entries order.
    ordered_ids = [e.id for e in load_index(root).entries]

    def fake_score_all(matrix, q_normalized):
        # DR-0002 is the direct hit; DR-0001's raw similarity is forced to
        # 0.0 — a score no direct hit could ever clear on its own.
        return [0.9 if eid == "DR-0002" else 0.0 for eid in ordered_ids]

    monkeypatch.setattr(retriever, "_score_all", fake_score_all)

    results = query(root, "Postgres ledger", top_n=1)
    by_id = {r.id: r for r in results}

    assert by_id["DR-0002"].surfaced_via == "direct"
    assert by_id["DR-0001"].surfaced_via == "relationship"
    assert by_id["DR-0001"].score == 0.0


# --- numpy scoring parity (WP-07) -------------------------------------------


def test_vectorized_scoring_matches_the_reference_cosine_implementation():
    random.seed(42)
    raw_vectors = [[random.uniform(-1, 1) for _ in range(16)] for _ in range(10)]
    q_raw = [random.uniform(-1, 1) for _ in range(16)]

    entries = [
        IndexEntry(
            id=f"DR-{i:04d}",
            title="t",
            category="architectural",
            status="accepted",
            weight="standard",
            date="2026-01-01",
            file_path="x.md",
            relationships=[],
            reverse_links=[],
            embedding=_normalize(v),
            document="",
            why="",
            alternatives="",
        )
        for i, v in enumerate(raw_vectors)
    ]

    matrix = np.asarray([e.embedding for e in entries], dtype=np.float32)
    vectorized_scores = _score_all(matrix, _normalize(q_raw))
    reference_scores = [_cosine(q_raw, v) for v in raw_vectors]

    for vectorized, reference in zip(vectorized_scores, reference_scores):
        assert vectorized == pytest.approx(reference, abs=1e-5)
