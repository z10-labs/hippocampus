from __future__ import annotations

import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

from hippocampus.types import ClassificationResult, Relationship


def _today() -> str:
    return date.today().isoformat()


def _records_dir(root: Path) -> Path:
    return root / ".decisions" / "records"


def _next_id(root: Path) -> str:
    records_dir = _records_dir(root)
    records_dir.mkdir(parents=True, exist_ok=True)
    files = [f for f in records_dir.iterdir() if re.match(r'^\d{4}-', f.name)]
    if not files:
        return "0001"
    max_id = max(int(f.name[:4]) for f in files)
    return str(max_id + 1).zfill(4)


def _lock_path(root: Path) -> Path:
    return root / ".decisions" / ".write.lock"


def _with_lock(root: Path, fn):
    lock = _lock_path(root)
    deadline = time.monotonic() + 5.0
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise RuntimeError("Could not acquire decision log lock within 5 seconds")
            time.sleep(0.05)
    try:
        return fn()
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _slug(title: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return s[:50]


def _relationships_block(relationships: list[Relationship]) -> str:
    if not relationships:
        return "- (none)"
    return "\n".join(f"- {r.type}: {r.target}" for r in relationships)


def _alternatives_block(alternatives: Optional[list[str]], placeholder: str) -> str:
    if not alternatives:
        return placeholder
    return "\n".join(f"- {a}" for a in alternatives)


def write_standard_record(
    root: Path,
    description: str,
    classification: ClassificationResult,
    title: Optional[str] = None,
    why: Optional[str] = None,
    trade_off: Optional[str] = None,
    relationships: Optional[list[Relationship]] = None,
    alternatives: Optional[list[str]] = None,
) -> str:
    today = _today()
    title = (title or description[:60]).strip()
    why = (why or description).strip()
    trade_off = (trade_off or "Not documented").strip()
    rels = relationships or []
    alts_block = _alternatives_block(alternatives, "_See description above._")

    slug = _slug(title)

    def write():
        record_id = _next_id(root)
        dr_id = f"DR-{record_id}"
        file_path = _records_dir(root) / f"{record_id}-{slug}.md"
        content = f"""# {dr_id}: {title}

**Date**: {today}
**Category**: {classification.category}
**Status**: accepted
**Weight**: standard

## Why

{why}

## What

{description}

## Trade-off

{trade_off}

## Alternatives Skipped

{alts_block}

## Relationships

{_relationships_block(rels)}
"""
        file_path.write_text(content)
        return str(file_path)

    return _with_lock(root, write)


def write_heavy_record(
    root: Path,
    description: str,
    classification: ClassificationResult,
    title: Optional[str] = None,
    why: Optional[str] = None,
    trade_off: Optional[str] = None,
    relationships: Optional[list[Relationship]] = None,
    review_trigger: Optional[str] = None,
    alternatives: Optional[list[str]] = None,
) -> str:
    today = _today()
    title = (title or description[:60]).strip()
    why = (why or description).strip()
    trade_off = (trade_off or "Not documented").strip()
    review_trigger = (review_trigger or "Not specified").strip()
    rels = relationships or []
    alts_block = _alternatives_block(alternatives, "_No alternatives documented._")

    slug = _slug(title)

    def write():
        record_id = _next_id(root)
        dr_id = f"DR-{record_id}"
        file_path = _records_dir(root) / f"{record_id}-{slug}.md"
        content = f"""# {dr_id}: {title}

**Date**: {today}
**Category**: {classification.category}
**Status**: accepted
**Weight**: heavy

## Context

{why}

## Decision

{description}

## Alternatives Considered

{alts_block}

## Consequences

### Positive
- To be documented

### Negative / Trade-offs
- {trade_off}

### Risks
- None identified

## Relationships

{_relationships_block(rels)}

## Review Trigger

{review_trigger}
"""
        file_path.write_text(content)
        return str(file_path)

    return _with_lock(root, write)


def write_deferred_entry(root: Path, description: str) -> str:
    today = _today()
    deferred_file = root / ".decisions" / "deferred.md"
    deferred_file.parent.mkdir(parents=True, exist_ok=True)
    entry = f"\n---\n\n## {today} — {description[:60]}\n\n**What was deferred**: {description}\n**Why deferred**: Not documented\n**Review trigger**: Not specified\n**Risk of deferral**: Not documented\n"
    with open(deferred_file, "a") as f:
        f.write(entry)
    return str(deferred_file)


def apply_supersedes(root: Path, new_dr_id: str, target_dr_id: str) -> bool:
    """Patches the target record's Status line to 'superseded by DR-XXXX'."""
    records_dir = _records_dir(root)
    matches = list(records_dir.glob(f"{target_dr_id.replace('DR-', '')}-*.md"))
    if not matches:
        # Try loose match by ID prefix
        num = target_dr_id.replace("DR-", "").lstrip("0") or "0"
        matches = [f for f in records_dir.glob("*.md") if re.match(rf'^0*{num}-', f.name)]
    if not matches:
        return False
    target_file = matches[0]
    content = target_file.read_text()
    updated = re.sub(
        r'(\*\*Status\*\*:\s*).*',
        f'\\1superseded by {new_dr_id}',
        content,
    )
    if updated != content:
        target_file.write_text(updated)
        return True
    return False
