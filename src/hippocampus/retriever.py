from __future__ import annotations

import math
from pathlib import Path

from hippocampus.indexer import embed, load_index
from hippocampus.types import IndexEntry, RetrievalResult

SOFT_RELATED_THRESHOLD = 0.80

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


def query(root: Path, query_text: str, top_n: int = 5) -> list[RetrievalResult]:
    index = load_index(root)
    if not index.entries:
        return []

    q_emb = embed(query_text)

    scored = sorted(
        ((e, _effective_score(e, _cosine(q_emb, e.embedding))) for e in index.entries),
        key=lambda x: x[1],
        reverse=True,
    )

    top = scored[:top_n]
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

    # Soft related-to: high similarity entries not yet included
    for e, score in scored:
        if e.id in seen:
            continue
        if score < SOFT_RELATED_THRESHOLD:
            break
        seen.add(e.id)
        results.append(RetrievalResult(
            id=e.id,
            title=e.title,
            file_path=e.file_path,
            score=score,
            surfaced_via="relationship",
            relationship_type="related-to",
            relevance_note=f"Related (similarity: {score:.3f})",
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
