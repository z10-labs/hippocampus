from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np

from hippocampus.types import IndexEntry, Relationship, ReverseLink, VectorIndex

_model = None

# Bumped whenever the on-disk shape changes (WP-07: unit-normalized
# embeddings, no indent, deferred entries mixed in). A missing/mismatched
# version is treated as no index at all rather than risking a KeyError on a
# field an older file doesn't have — see load_index.
INDEX_SCHEMA_VERSION = 2

# path -> (file mtime, VectorIndex). Avoids re-reading and re-parsing the
# whole JSON file on every single query within a process. Refreshed directly
# by _save_index right after a write, and invalidated implicitly by the
# mtime check whenever the file changes underneath it.
_index_cache: dict[Path, tuple[float, VectorIndex]] = {}

# path -> (file mtime, (N, D) float32 matrix of every entry's embedding, in
# load_index(root).entries order). Kept separate from _index_cache: building
# this matrix from a list of Python-float lists is the actual expensive step
# in scoring (measured ~3ms for 500x384 on one machine), not the dot product
# against it (~0.004ms) — so it is worth holding onto across queries within
# a session exactly like the parsed index itself, not rebuilding it per call.
_matrix_cache: dict[Path, tuple[float, np.ndarray]] = {}


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    return _model


def embed(text: str) -> list[float]:
    model = _get_model()
    return next(model.embed([text])).tolist()


def embed_many(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _get_model()
    return [v.tolist() for v in model.embed(texts)]


def _normalize(vec: list[float]) -> list[float]:
    """Unit-normalize an embedding at index time so retrieval-time cosine
    similarity reduces to a plain dot product against the query vector."""
    arr = np.asarray(vec, dtype=np.float64)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return list(vec)
    return (arr / norm).tolist()


def _index_path(root: Path) -> Path:
    return root / ".hippocampus" / "index.json"


def load_index(root: Path) -> VectorIndex:
    path = _index_path(root)
    if not path.exists():
        _index_cache.pop(path, None)
        _matrix_cache.pop(path, None)
        return VectorIndex(entries=[], built_at=0)

    file_mtime = path.stat().st_mtime
    cached = _index_cache.get(path)
    if cached is not None and cached[0] == file_mtime:
        return cached[1]

    data = json.loads(path.read_text())
    if data.get("version") != INDEX_SCHEMA_VERSION:
        # Unrecognized on-disk shape (older version, or none at all). Treat
        # exactly like a missing index rather than parsing fields that may
        # not exist — ensure_index/build_index already know how to turn an
        # empty existing index into a full rebuild, which writes the current
        # version back out.
        index = VectorIndex(entries=[], built_at=0)
        _index_cache[path] = (file_mtime, index)
        return index

    entries = [
        IndexEntry(
            id=e["id"],
            title=e["title"],
            category=e["category"],
            status=e["status"],
            weight=e["weight"],
            date=e["date"],
            file_path=e["file_path"],
            relationships=[Relationship(**r) for r in e.get("relationships", [])],
            reverse_links=[ReverseLink(**r) for r in e.get("reverse_links", [])],
            embedding=e["embedding"],
            document=e["document"],
            why=e["why"],
            alternatives=e["alternatives"],
        )
        for e in data["entries"]
    ]
    index = VectorIndex(entries=entries, built_at=data["built_at"])
    _index_cache[path] = (file_mtime, index)
    return index


def embeddings_matrix(root: Path) -> np.ndarray:
    """Cached (N, D) float32 matrix of every entry's embedding, in
    load_index(root).entries order. Callers doing a query against every
    entry (retriever.query) should use this instead of building their own
    matrix from a fresh list comprehension each time."""
    index = load_index(root)  # ensures the cache below keys off a fresh mtime
    path = _index_path(root)
    file_mtime = path.stat().st_mtime if path.exists() else 0.0

    cached = _matrix_cache.get(path)
    if cached is not None and cached[0] == file_mtime:
        return cached[1]

    matrix = (
        np.asarray([e.embedding for e in index.entries], dtype=np.float32)
        if index.entries else np.zeros((0, 0), dtype=np.float32)
    )
    _matrix_cache[path] = (file_mtime, matrix)
    return matrix


def _save_index(root: Path, index: VectorIndex) -> None:
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # No indent: this is a gitignored derived cache nobody reads by hand,
    # and indent=2 was most of the file size on lists of 384 floats.
    path.write_text(json.dumps({
        "version": INDEX_SCHEMA_VERSION,
        "built_at": index.built_at,
        "entries": [asdict(e) for e in index.entries],
    }))
    _index_cache[path] = (path.stat().st_mtime, index)


def _records_dir(root: Path) -> Path:
    return root / ".decisions" / "records"


def _deferred_path(root: Path) -> Path:
    return root / ".decisions" / "deferred.md"


def _field(content: str, name: str, default: str = "") -> str:
    m = re.search(rf'\*\*{name}\*\*:\s*(.+)', content)
    return m.group(1).strip() if m else default


def _parse_relationships(content: str) -> list[Relationship]:
    section_match = re.search(r'## Relationships\n([\s\S]*?)(?:\n##|$)', content)
    explicit: list[Relationship] = []
    if section_match:
        for line in section_match.group(1).splitlines():
            m = re.match(r'[-*]\s*(overrides|inferred-by|depends-on|supersedes|conflicts-with|references):\s*(DR-\d+)', line, re.I)
            if m:
                explicit.append(Relationship(type=m.group(1).lower(), target=m.group(2).upper()))

    # Prose fallback: scan Why/What/Context/Decision for bare DR-NNNN mentions.
    # Only add as 'references' if the target isn't already covered by an explicit link.
    explicit_targets = {r.target for r in explicit}
    body_match = re.search(r'## (?:Why|What|Context|Decision)\n([\s\S]*?)(?:\n##|$)', content)
    prose_refs: list[Relationship] = []
    if body_match:
        for dr_id in dict.fromkeys(re.findall(r'\bDR-\d{4}\b', body_match.group(1))):
            if dr_id not in explicit_targets:
                prose_refs.append(Relationship(type='references', target=dr_id))

    return explicit + prose_refs


def _parse_why(content: str) -> str:
    m = re.search(r'## (?:Why|Context)\n([\s\S]*?)(?:\n##|$)', content)
    if not m:
        return ""
    text = re.sub(r'\s+', ' ', m.group(1).strip())
    return text[:217] + "…" if len(text) > 220 else text


def _parse_alternatives(content: str) -> str:
    m = re.search(r'## Alternatives(?:\s+(?:Skipped|Considered))?\n([\s\S]*?)(?:\n##|$)', content)
    if not m:
        return ""
    lines = []
    for line in m.group(1).splitlines():
        stripped = line.strip()
        bullet = re.match(r'^(?:[-*]|\d+\.)\s+(.+)', stripped)
        if bullet:
            body = bullet.group(1).strip()
            if not re.match(r'^because\b', body, re.I):
                lines.append(body[:80])
    return "\n".join(lines)


def _parse_file(path: Path) -> Optional[dict]:
    content = path.read_text()
    id_match = re.match(r'^# (DR-\d+):\s*(.+)', content, re.M)
    if not id_match:
        return None
    return {
        "id": id_match.group(1),
        "title": id_match.group(2).strip(),
        "category": _field(content, "Category") or "architectural",
        "status": _field(content, "Status") or "proposed",
        "weight": _field(content, "Weight") or "standard",
        "date": _field(content, "Date"),
        "relationships": _parse_relationships(content),
        "why": _parse_why(content),
        "alternatives": _parse_alternatives(content),
        "content": content,
    }


def _reverse_type(rel_type: str) -> str:
    return {
        "depends-on": "depended-on-by",
        "supersedes": "superseded-by",
        "conflicts-with": "conflicts-with",
        "overrides": "overridden-by",
        "inferred-by": "infers",
    }.get(rel_type, f"linked-from")


_DEFERRED_HEADING_RE = re.compile(r'^## (\d{4}-\d{2}-\d{2}) — (.+)$', re.M)


def _parse_deferred_blocks(root: Path) -> list[dict]:
    path = _deferred_path(root)
    if not path.exists():
        return []
    content = path.read_text()
    headings = list(_DEFERRED_HEADING_RE.finditer(content))

    blocks = []
    for i, m in enumerate(headings):
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        block = content[m.end():block_end]
        blocks.append({
            "date": m.group(1),
            "title": m.group(2).strip(),
            "what": _field(block, "What was deferred"),
            "why": _field(block, "Why deferred"),
            "review_trigger": _field(block, "Review trigger"),
        })
    return blocks


def _build_deferred_entries(root: Path) -> list[IndexEntry]:
    """Deferred decisions live in one shared deferred.md, not one file per
    entry. DEF- ids never appear as relationship targets (only DR-\\d+ is
    matched by _parse_relationships), so these entries participate in
    retrieval but never in the dependency graph."""
    file_path = str(_deferred_path(root).relative_to(root))
    entries = []
    for i, block in enumerate(_parse_deferred_blocks(root)):
        why = block["why"]
        if block["review_trigger"]:
            trigger = block["review_trigger"].rstrip(".")
            why = f"{why} Review trigger: {trigger}."
        document = f"{block['title']}\n\n{block['what'] or block['title']}"
        entries.append(IndexEntry(
            id=f"DEF-{i + 1:04d}",
            title=block["title"],
            category="",
            status="deferred",
            weight="deferred",
            date=block["date"],
            file_path=file_path,
            relationships=[],
            reverse_links=[],
            embedding=_normalize(embed(document)),
            document=document,
            why=why,
            alternatives="",
        ))
    return entries


def build_index(root: Path, force: bool = False) -> dict:
    records_dir = _records_dir(root)
    existing = VectorIndex(entries=[], built_at=0) if force else load_index(root)
    by_id: dict[str, IndexEntry] = {e.id: e for e in existing.entries}

    last_built = 0 if force else existing.built_at
    indexed = 0
    skipped = 0
    seen_ids: set[str] = set()

    record_files = sorted(records_dir.glob("*.md")) if records_dir.exists() else []

    # First pass: parse every file and decide which need (re-)embedding, but
    # don't embed yet — fastembed is substantially faster given a batch than
    # called once per record in a loop.
    to_embed: list[dict] = []
    for file_path in record_files:
        mtime_ms = int(file_path.stat().st_mtime * 1000)
        parsed = _parse_file(file_path)
        if not parsed:
            continue

        seen_ids.add(parsed["id"])

        if not force and mtime_ms <= last_built and parsed["id"] in by_id:
            skipped += 1
            continue

        parsed["_file_path"] = file_path
        parsed["_text"] = f"{parsed['title']}\n\n{parsed['content']}"
        to_embed.append(parsed)

    embeddings = [_normalize(e) for e in embed_many([p["_text"] for p in to_embed])]

    for parsed, embedding in zip(to_embed, embeddings):
        by_id[parsed["id"]] = IndexEntry(
            id=parsed["id"],
            title=parsed["title"],
            category=parsed["category"],
            status=parsed["status"],
            weight=parsed["weight"],
            date=parsed["date"],
            file_path=str(parsed["_file_path"].relative_to(root)),
            relationships=parsed["relationships"],
            reverse_links=[],  # populated below
            embedding=embedding,
            document=parsed["_text"],
            why=parsed["why"],
            alternatives=parsed["alternatives"],
        )
        indexed += 1

    # Drop DR- entries whose source file is gone. Reverse links are rebuilt
    # from scratch below, so any dangling reference to a removed id heals
    # itself. Deferred (DEF-) entries are rebuilt separately below and are
    # not subject to this per-file prune.
    removed = 0
    stale_dr_ids = {eid for eid in by_id if eid.startswith("DR-")} - seen_ids
    for stale_id in stale_dr_ids:
        del by_id[stale_id]
        removed += 1

    # Deferred entries are cheap to re-derive in full from deferred.md on
    # every build — there is no incremental-skip bookkeeping for them, and
    # since they all live in one shared file there is no per-entry mtime to
    # compare against anyway.
    for stale_def_id in [eid for eid in by_id if eid.startswith("DEF-")]:
        del by_id[stale_def_id]
    for entry in _build_deferred_entries(root):
        by_id[entry.id] = entry

    # Build bidirectional reverse links across the full corpus
    reverse: dict[str, list[ReverseLink]] = {eid: [] for eid in by_id}
    for entry in by_id.values():
        for rel in entry.relationships:
            if rel.target in reverse:
                reverse[rel.target].append(ReverseLink(type=_reverse_type(rel.type), source=entry.id))

    for eid, entry in by_id.items():
        entry.reverse_links = reverse.get(eid, [])

    new_index = VectorIndex(
        entries=sorted(by_id.values(), key=lambda e: e.id),
        built_at=int(time.time() * 1000),
    )
    _save_index(root, new_index)
    return {"indexed": indexed, "skipped": skipped, "total": len(by_id), "removed": removed}


def ensure_index(root: Path) -> None:
    """Cheap freshness check, safe to call on every tool invocation.

    Triggers an incremental rebuild if the index is missing, a record (or
    deferred.md) on disk is newer than the last build, or the number of
    records plus deferred entries has changed. The incremental build already
    skips unchanged files, so the common case costs a handful of stat() calls
    and no embedding.
    """
    records_dir = _records_dir(root)
    deferred_file = _deferred_path(root)
    if not records_dir.exists() and not deferred_file.exists():
        return

    record_files = list(records_dir.glob("*.md")) if records_dir.exists() else []
    index = load_index(root)

    mtimes_ms = [int(f.stat().st_mtime * 1000) for f in record_files]
    if deferred_file.exists():
        mtimes_ms.append(int(deferred_file.stat().st_mtime * 1000))
    max_mtime_ms = max(mtimes_ms, default=0)

    deferred_count = len(_parse_deferred_blocks(root))
    expected_total = len(record_files) + deferred_count

    # `>=` rather than `>`: on filesystems with coarse mtime granularity, a
    # record written in the same millisecond as the build must still count
    # as stale. This also covers a missing index, since built_at is then 0.
    stale = max_mtime_ms >= index.built_at or expected_total != len(index.entries)
    if stale:
        build_index(root, force=False)
