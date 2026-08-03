from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from hippocampus.types import IndexEntry, Relationship, ReverseLink, VectorIndex

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    return _model


def embed(text: str) -> list[float]:
    model = _get_model()
    return next(model.embed([text])).tolist()


def _index_path(root: Path) -> Path:
    return root / ".hippocampus" / "index.json"


def load_index(root: Path) -> VectorIndex:
    path = _index_path(root)
    if not path.exists():
        return VectorIndex(entries=[], built_at=0)
    data = json.loads(path.read_text())
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
    return VectorIndex(entries=entries, built_at=data["built_at"])


def _save_index(root: Path, index: VectorIndex) -> None:
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "built_at": index.built_at,
        "entries": [asdict(e) for e in index.entries],
    }, indent=2))


def _records_dir(root: Path) -> Path:
    return root / ".decisions" / "records"


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
        "category": (re.search(r'\*\*Category\*\*:\s*(.+)', content) or type('', (), {'group': lambda s, n: ''})()).group(1).strip() or "architectural",
        "status": (re.search(r'\*\*Status\*\*:\s*(.+)', content) or type('', (), {'group': lambda s, n: ''})()).group(1).strip() or "proposed",
        "weight": (re.search(r'\*\*Weight\*\*:\s*(.+)', content) or type('', (), {'group': lambda s, n: ''})()).group(1).strip() or "standard",
        "date": (re.search(r'\*\*Date\*\*:\s*(.+)', content) or type('', (), {'group': lambda s, n: ''})()).group(1).strip() or "",
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


def build_index(root: Path, force: bool = False) -> dict:
    records_dir = _records_dir(root)
    existing = VectorIndex(entries=[], built_at=0) if force else load_index(root)
    by_id: dict[str, IndexEntry] = {e.id: e for e in existing.entries}

    last_built = 0 if force else existing.built_at
    indexed = 0
    skipped = 0
    seen_ids: set[str] = set()

    record_files = sorted(records_dir.glob("*.md")) if records_dir.exists() else []

    for file_path in record_files:
        mtime_ms = int(file_path.stat().st_mtime * 1000)
        parsed = _parse_file(file_path)
        if not parsed:
            continue

        seen_ids.add(parsed["id"])

        if not force and mtime_ms <= last_built and parsed["id"] in by_id:
            skipped += 1
            continue

        text = f"{parsed['title']}\n\n{parsed['content']}"
        embedding = embed(text)

        by_id[parsed["id"]] = IndexEntry(
            id=parsed["id"],
            title=parsed["title"],
            category=parsed["category"],
            status=parsed["status"],
            weight=parsed["weight"],
            date=parsed["date"],
            file_path=str(file_path.relative_to(root)),
            relationships=parsed["relationships"],
            reverse_links=[],  # populated below
            embedding=embedding,
            document=text,
            why=parsed["why"],
            alternatives=parsed["alternatives"],
        )
        indexed += 1

    # Drop entries whose source file is gone. Reverse links are rebuilt from
    # scratch below, so any dangling reference to a removed id heals itself.
    removed = 0
    for stale_id in set(by_id) - seen_ids:
        del by_id[stale_id]
        removed += 1

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

    Triggers an incremental rebuild if the index is missing, a record on disk
    is newer than the last build, or the number of record files has changed
    (a record was added or deleted). The incremental build already skips
    unchanged files, so the common case costs a handful of stat() calls and
    no embedding.
    """
    records_dir = _records_dir(root)
    if not records_dir.exists():
        return

    record_files = list(records_dir.glob("*.md"))
    index = load_index(root)
    max_mtime_ms = max((int(f.stat().st_mtime * 1000) for f in record_files), default=0)

    # `>=` rather than `>`: on filesystems with coarse mtime granularity, a
    # record written in the same millisecond as the build must still count
    # as stale. This also covers a missing index, since built_at is then 0.
    stale = max_mtime_ms >= index.built_at or len(record_files) != len(index.entries)
    if stale:
        build_index(root, force=False)
