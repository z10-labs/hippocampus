from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from hippocampus.indexer import embed, load_index
from hippocampus.types import IndexEntry, RetrievalResult

# Calibrated against the real all-MiniLM-L6-v2 model (see WP-06 PR body for
# the full corpus/query set and score distribution): on a 25-record corpus
# covering a dozen categories, queries with a genuine intended answer scored
# 0.48-0.80 against it; queries with no good answer in the corpus topped out
# at 0.25. MIN_DIRECT_SCORE sits in that gap with margin on both sides.
# Below it, a "match" is noise the agent should not be shown as prior art.
MIN_DIRECT_SCORE = 0.30

# Was 0.80, effectively unreachable on this model — genuinely related
# technical sentences land in the 0.4-0.6 range (confirmed above), so 0.80
# meant this branch almost never fired.
SOFT_RELATED_THRESHOLD = 0.50

# Records that are no longer the live decision (superseded, deprecated, ...)
# still carry historical value and are never filtered out, but should not
# outrank a live record at equal similarity. Applied before the top-N cut.
# "deferred" is exempt: an open question isn't dead history, it's a live
# consideration the caller may still act on.
SUPERSEDED_SCORE_MULTIPLIER = 0.5
_LIVE_STATUSES = {"accepted", "proposed", "deferred"}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    denom = mag_a * mag_b
    return dot / denom if denom else 0.0


def _effective_score(entry: IndexEntry, raw_score: float) -> float:
    if entry.status in _LIVE_STATUSES:
        return raw_score
    return raw_score * SUPERSEDED_SCORE_MULTIPLIER


def query(
    root: Path,
    query_text: str,
    top_n: int = 5,
    min_score: Optional[float] = None,
) -> list[RetrievalResult]:
    index = load_index(root)
    if not index.entries:
        return []

    floor = MIN_DIRECT_SCORE if min_score is None else min_score
    q_emb = embed(query_text)

    # Each entry carries its raw cosine score and its status-adjusted
    # (effective) score. Sorting and display use the effective score, so a
    # superseded record ranks behind an equally-relevant live one (WP-04).
    # The floor is applied to the *raw* score, not the effective one: a
    # superseded record must only ever be filtered for being irrelevant, not
    # for being superseded — WP-04 requires it stay visible, demoted, never
    # hidden. Demotion and the noise floor are separate concerns.
    scored = sorted(
        ((e, raw, _effective_score(e, raw)) for e, raw in (
            (e, _cosine(q_emb, e.embedding)) for e in index.entries
        )),
        key=lambda t: t[2],
        reverse=True,
    )

    # Filter before the top-N cut: a low-score "match" is noise regardless of
    # whether there were enough good candidates to fill top_n. Relationship
    # expansion below is exempt from this floor by design — those results
    # earn their place structurally, not by similarity.
    top = [(e, eff) for e, raw, eff in scored if raw >= floor][:top_n]
    seen = {e.id for e, _ in top}
    by_id = {e.id: e for e in index.entries}

    results: list[RetrievalResult] = [
        RetrievalResult(
            id=e.id,
            title=e.title,
            file_path=e.file_path,
            score=score,
            surfaced_via="direct",
            relevance_note=f"Similarity: {score:.3f}",
            why=e.why,
            alternatives=e.alternatives,
            category=e.category,
            weight=e.weight,
            depends_on=[r.target for r in e.relationships if r.type == "depends-on"],
            status=e.status,
        )
        for e, score in top
    ]

    # Outbound relationship expansion (declared links in each top result)
    for e, _ in top:
        for rel in e.relationships:
            if rel.target in seen:
                continue
            related = by_id.get(rel.target)
            if not related:
                continue
            seen.add(rel.target)
            results.append(RetrievalResult(
                id=rel.target,
                title=related.title,
                file_path=related.file_path,
                score=0.0,
                surfaced_via="relationship",
                relationship_type=rel.type,
                relevance_note=f"{_rel_label(rel.type)} {e.id}",
                why=related.why,
                alternatives=related.alternatives,
                category=related.category,
                weight=related.weight,
                depends_on=[r.target for r in related.relationships if r.type == "depends-on"],
                status=related.status,
            ))

    # Inbound relationship expansion (reverse links — bidirectional)
    for e, _ in top:
        for rev in e.reverse_links:
            if rev.source in seen:
                continue
            related = by_id.get(rev.source)
            if not related:
                continue
            seen.add(rev.source)
            results.append(RetrievalResult(
                id=rev.source,
                title=related.title,
                file_path=related.file_path,
                score=0.0,
                surfaced_via="relationship",
                relationship_type=rev.type,
                relevance_note=f"{_rel_label(rev.type)} {e.id}",
                why=related.why,
                alternatives=related.alternatives,
                category=related.category,
                weight=related.weight,
                depends_on=[r.target for r in related.relationships if r.type == "depends-on"],
                status=related.status,
            ))

    # Soft related-to: high similarity entries not yet included. Compared
    # against the effective score, consistent with the sort key above, so
    # the early break remains valid.
    for e, raw, eff in scored:
        if e.id in seen:
            continue
        if eff < SOFT_RELATED_THRESHOLD:
            break
        seen.add(e.id)
        results.append(RetrievalResult(
            id=e.id,
            title=e.title,
            file_path=e.file_path,
            score=eff,
            surfaced_via="relationship",
            relationship_type="related-to",
            relevance_note=f"Related (similarity: {eff:.3f})",
            why=e.why,
            alternatives=e.alternatives,
            category=e.category,
            weight=e.weight,
            depends_on=[r.target for r in e.relationships if r.type == "depends-on"],
            status=e.status,
        ))

    return results


def _rel_label(rel_type: str) -> str:
    return {
        "depends-on": "Depends on",
        "supersedes": "Supersedes",
        "conflicts-with": "Conflicts with",
        "depended-on-by": "Depended on by",
        "superseded-by": "Superseded by",
        "overrides": "Overrides",
        "overridden-by": "Overridden by",
        "inferred-by": "Follows from",
        "infers": "Inferred by",
        "related-to": "Related to",
    }.get(rel_type, rel_type)
