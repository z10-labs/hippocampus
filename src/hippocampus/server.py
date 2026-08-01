"""Hippocampus MCP server — codebase decision memory."""
from __future__ import annotations

import argparse
import json
import re
import secrets
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

import hippocampus.settings as settings
from hippocampus.classify import classify
from hippocampus.indexer import build_index, ensure_index, load_index
from hippocampus.logger import (
    apply_supersedes,
    write_deferred_entry,
    write_heavy_record,
    write_standard_record,
)
from hippocampus.retriever import _rel_label, query
from hippocampus.types import ClassificationResult, Relationship

mcp = FastMCP("hippocampus")


def _empty_state_message(root: Path) -> str:
    """Distinguish why a query came back empty. The tool can only report what
    it did or did not find — never assert that no constraints apply."""
    records_dir = root / ".decisions" / "records"
    record_files = list(records_dir.glob("*.md")) if records_dir.exists() else []
    if not record_files:
        return "No decision records exist yet in this project. Nothing has been recorded."

    index = load_index(root)
    if not index.entries:
        return (
            f"{len(record_files)} records found but none could be indexed — "
            "check record format (each needs a '# DR-NNNN: Title' heading)."
        )

    return "No decisions matched this query above the relevance threshold."


def _status_marker(status: str) -> str:
    """A marker for the id line so a dead decision can't be skimmed past as
    if it were live. Superseded/deprecated records are never filtered out —
    they stay part of the history — but must not read identically to one
    that is still in force. Deferred entries get their own marker: they are
    not dead, they are an open question that was consciously not decided."""
    if status in ("accepted", "proposed"):
        return ""
    if status == "deferred":
        return "⏸ NOT YET DECIDED"
    m = re.search(r'superseded by\s+(DR-\d+)', status, re.I)
    if m:
        return f"⚠ SUPERSEDED BY {m.group(1).upper()}"
    return "⚠ SUPERSEDED"


# ---------------------------------------------------------------------------
# hippocampus_query
# ---------------------------------------------------------------------------

@mcp.tool()
def hippocampus_query(query_text: str, top_n: int = 5) -> str:
    """Semantic search over past architectural decisions.

    Call this before any non-trivial decision: choosing a library, designing a
    schema, picking an interface, or any fork with real alternatives.

    Returns ranked results with inline Why, Rejected alternatives, and
    Depends-on so you do not need to open record files.
    """
    ensure_index(settings.ROOT)
    results = query(settings.ROOT, query_text, top_n)
    if not results:
        return _empty_state_message(settings.ROOT)

    lines = [f'Querying: "{query_text}"\n', "─" * 70]
    for r in results:
        if r.surfaced_via == "direct":
            badge = f"[direct | score: {r.score:.3f}]"
        else:
            badge = f"[via {r.relationship_type} | {r.relevance_note}]"

        meta = " · ".join(filter(None, [r.category, r.weight]))
        header = f"{r.id}  {badge}  ({meta})"
        status_marker = _status_marker(r.status)
        if status_marker:
            header += f"  {status_marker}"
        lines.append(header)
        lines.append(f"  {r.title}")

        if r.why:
            short = r.why[:160] + "…" if len(r.why) > 160 else r.why
            lines.append(f"  Why: {short}")

        # Deferred entries were never a decision with rejected options — a
        # "Rejected:" line on one would misrepresent an open question as one.
        if r.weight != "deferred":
            alts = (r.alternatives or "").strip()
            if alts:
                alt_lines = alts.splitlines()[:3]
                lines.append(f"  Rejected: {alt_lines[0]}")
                for a in alt_lines[1:]:
                    lines.append(f"            {a}")
            else:
                lines.append("  Rejected: (none documented)")

        if r.depends_on:
            lines.append(f"  Depends on: {', '.join(r.depends_on)}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# hippocampus_log
# ---------------------------------------------------------------------------

# An agent that passes confirmed=True on the first call skips Phase 1
# entirely and never sees the related-decisions list it's supposed to link
# against — the two-phase flow was advisory, and agents optimizing for turn
# count route around advisory steps. Phase 1 issues a token here; Phase 2
# requires it, proving Phase 1 actually ran for this exact description.
#
# Random rather than a hash of the description: a hash would be a formula an
# agent could compute without ever calling Phase 1, silently defeating the
# point. This is a guard rail, not a security boundary, so 8 hex chars of
# randomness is plenty — the server is a per-session stdio process, so a
# bounded in-memory dict is enough; persistence across restarts is not
# needed and would be a liability.
_PHASE1_TOKEN_LIMIT = 100
_phase1_tokens: dict[str, str] = {}  # token -> description at issue time


def _issue_phase1_token(description: str) -> str:
    token = secrets.token_hex(4)
    _phase1_tokens[token] = description
    while len(_phase1_tokens) > _PHASE1_TOKEN_LIMIT:
        oldest = next(iter(_phase1_tokens))
        del _phase1_tokens[oldest]
    return token


def _phase1_output(classification: ClassificationResult, description: str) -> str:
    """Renders Phase 1's classification + candidates output and issues a
    fresh token for it. Reused both for a genuine Phase 1 call and for a
    Phase 2 call that arrives with no token or a stale one."""
    token = _issue_phase1_token(description)
    lines = [
        f"Classification: {classification.weight} | {classification.category}",
        f"Reason: {classification.reason}",
        "",
    ]

    if classification.weight == "deferred":
        lines.append("This looks like a deliberate deferral.")
        lines.append(f'Call again with confirmed=True and token="{token}" to record it.')
        return "\n".join(lines)

    # Phase 1 uses its own, looser bar than the retrieval floor
    # (retriever.MIN_DIRECT_SCORE) — a candidate worth surfacing for
    # possible relationship linking doesn't need to clear the same noise
    # threshold as a result shown as an established prior decision.
    candidates = query(settings.ROOT, description, 5, min_score=0.20)
    direct = [r for r in candidates if r.surfaced_via == "direct"]

    if direct:
        lines.append("Related decisions found — if any constrained your choice, include them as relationships in Phase 2:")
        lines.append("─" * 70)
        for r in direct:
            lines.append(f"  {r.id}  [score: {r.score:.3f}]  {r.title}")
            if r.why:
                short = r.why[:120] + "…" if len(r.why) > 120 else r.why
                lines.append(f"         Why: {short}")
        lines.append("")

    lines.append(f'Call again with confirmed=True, token="{token}" (and relationships=[...]) to write the record.')
    return "\n".join(lines)


@mcp.tool()
def hippocampus_log(
    description: str,
    weight: Optional[str] = None,
    category: Optional[str] = None,
    confirmed: bool = False,
    token: Optional[str] = None,
    relationships: Optional[str] = None,
    title: Optional[str] = None,
    why: Optional[str] = None,
    trade_off: Optional[str] = None,
    review_trigger: Optional[str] = None,
    alternatives: Optional[str] = None,
) -> str:
    """Record an architectural decision. Two-phase flow:

    Phase 1 — call without confirmed=True. Returns classification and related
    decisions as candidates for relationship linking, plus a token. Read the
    candidates carefully: if any constrained your choice, include them in
    Phase 2.

    Phase 2 — call again with confirmed=True, the token from Phase 1, and the
    relationships list (JSON string: '[{"type":"depends-on","target":"DR-0001"}]',
    or '[]' for none). Writes the record and updates the index.

    The token proves Phase 1 actually ran for this exact description before
    anything gets written. confirmed=True with no token, or with a token
    issued for a different (or since-edited) description, does not write —
    it returns Phase 1's output again, with a fresh token, instead. This is
    deliberate: an agent that logs a decision with no Why produces a record
    worth nothing, and Phase 1's related-decisions list is the mechanism for
    catching that before it's written, not an optional courtesy.

    Relationship types: depends-on | supersedes | conflicts-with

    weight options: heavy | standard | deferred (omit to auto-classify)
    category options: architectural | domain | data | security | api |
      performance | dependency | testing | error-handling | state | naming |
      operational | compliance | cost | team | ux-product

    alternatives — what was considered and rejected, as a JSON array of
    strings: '["RabbitMQ — extra ops burden", "Redis streams — no durability
    guarantee we need"]'. Each entry should name the option AND the reason it
    was rejected ("option — reason"), not just the option name; a bare option
    name is not useful to a future reader. Optional for standard records, but
    encouraged — it is the whole point of recording a decision instead of
    just the outcome. REQUIRED for heavy records: a heavy decision written
    without alternatives is rejected in Phase 2.

    For a deferral (weight="deferred"), why and review_trigger are reused
    to record why the decision was put off and what should trigger revisiting
    it. A deferral with no review trigger never gets revisited, which makes
    it a leak rather than a decision — supply one whenever there's a concrete
    condition (a metric threshold, a milestone) that should prompt a second
    look.
    """
    classification = classify(description)
    if weight:
        classification.weight = weight
    if category:
        classification.category = category

    if classification.weight == "skip":
        return "Implementation-level decision — not worth recording."

    # Phase 1: suggest candidates and issue a token
    if not confirmed:
        return _phase1_output(classification, description)

    # Phase 2: the token must prove Phase 1 actually ran for this exact
    # description. No token, an unrecognized one, or one issued for
    # different text (edited since Phase 1) — regenerate Phase 1's output
    # (with a fresh token) instead of writing anything.
    if token is None or _phase1_tokens.get(token) != description:
        reason = (
            "Phase 2 requires the token from Phase 1 — none was supplied."
            if token is None
            else "That token doesn't match this description — it may have changed since Phase 1, or the token expired."
        )
        return f"{reason}\n\n{_phase1_output(classification, description)}"

    _phase1_tokens.pop(token, None)  # single-use

    # Phase 2: write record
    rels: list[Relationship] = []
    if relationships:
        try:
            raw = json.loads(relationships)
            rels = [Relationship(type=r["type"], target=r["target"]) for r in raw]
        except (json.JSONDecodeError, KeyError) as e:
            return f"Error parsing relationships JSON: {e}\nExpected: '[{{\"type\":\"depends-on\",\"target\":\"DR-0001\"}}]'"

    alts: list[str] = []
    if alternatives:
        try:
            raw_alts = json.loads(alternatives)
            alts = [str(a) for a in raw_alts]
        except json.JSONDecodeError as e:
            return (
                f"Error parsing alternatives JSON: {e}\n"
                'Expected: \'["RabbitMQ — extra ops burden", "Redis streams — no durability guarantee we need"]\''
            )

    if classification.weight == "deferred":
        file_path = write_deferred_entry(settings.ROOT, description, why=why, review_trigger=review_trigger)
        return f"Deferred decision recorded in: {file_path}"

    if classification.weight == "heavy":
        if not alts:
            return (
                "Heavy decisions require documented alternatives — a heavy record with none "
                "has no value to a future reader. Call again with alternatives as a JSON array "
                'of strings, e.g. alternatives=\'["RabbitMQ — extra ops burden", '
                '"Redis streams — no durability guarantee we need"]\'.'
            )
        file_path = write_heavy_record(
            settings.ROOT, description, classification,
            title=title, why=why, trade_off=trade_off,
            relationships=rels, review_trigger=review_trigger, alternatives=alts,
        )
    else:
        file_path = write_standard_record(
            settings.ROOT, description, classification,
            title=title, why=why, trade_off=trade_off,
            relationships=rels, alternatives=alts,
        )

    # Extract the new DR-NNNN from the written file path
    fname = Path(file_path).name
    new_id_match = re.match(r'^(\d{4})-', fname)
    new_dr_id = f"DR-{new_id_match.group(1)}" if new_id_match else "DR-????"

    # Apply supersedes side-effects
    superseded = []
    for rel in rels:
        if rel.type == "supersedes":
            if apply_supersedes(settings.ROOT, new_dr_id, rel.target):
                superseded.append(rel.target)

    # Incremental reindex
    build_index(settings.ROOT, force=False)

    lines = [f"Record written: {file_path}"]
    if superseded:
        lines.append(f"Status updated to 'superseded by {new_dr_id}': {', '.join(superseded)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# hippocampus_classify
# ---------------------------------------------------------------------------

@mcp.tool()
def hippocampus_classify(description: str) -> str:
    """Classify the weight and category of a decision without writing anything.

    Use this when unsure whether something is worth recording, or to preview
    how a description will be classified before calling hippocampus_log.
    """
    result = classify(description)
    record_recommended = result.weight in ("heavy", "standard")
    lines = [
        f"Weight: {result.weight}",
        f"Category: {result.category or 'N/A'}",
        f"Reason: {result.reason}",
        f"Record recommended: {'yes' if record_recommended else 'no'}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# hippocampus_list
# ---------------------------------------------------------------------------

@mcp.tool()
def hippocampus_list(category: Optional[str] = None, weight: Optional[str] = None) -> str:
    """List all decision records with inline Why and Depends-on.

    Filter by category (e.g. 'data', 'security') or weight ('heavy', 'standard').
    Useful for discovering relevant precedent before starting work in an unfamiliar area.
    """
    ensure_index(settings.ROOT)
    index = load_index(settings.ROOT)
    entries = index.entries

    if category:
        entries = [e for e in entries if e.category.lower() == category.lower()]
    if weight:
        entries = [e for e in entries if e.weight.lower() == weight.lower()]

    if not entries:
        filters = ", ".join(filter(None, [
            f"category={category}" if category else None,
            f"weight={weight}" if weight else None,
        ]))
        return f"No records match{' (' + filters + ')' if filters else ''}."

    filter_desc = ", ".join(filter(None, [
        f"category={category}" if category else None,
        f"weight={weight}" if weight else None,
    ]))
    header = f"Decision records{' (' + filter_desc + ')' if filter_desc else ''}:"
    lines = [header, "─" * 70]

    for e in sorted(entries, key=lambda x: x.id):
        meta = " · ".join(filter(None, [e.category, e.weight]))
        header = f"{e.id}  ({meta})  {e.date}"
        status_marker = _status_marker(e.status)
        if status_marker:
            header += f"  {status_marker}"
        lines.append(header)
        lines.append(f"  {e.title}")
        if e.why:
            short = e.why[:120] + "…" if len(e.why) > 120 else e.why
            lines.append(f"  Why: {short}")
        deps = [r.target for r in e.relationships if r.type == "depends-on"]
        if deps:
            lines.append(f"  Depends on: {', '.join(deps)}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# hippocampus_chain
# ---------------------------------------------------------------------------

MAX_CHAIN_DEPTH = 10

# Reverse-link types that represent "this would be affected if the target
# changes" — i.e. the blast radius. Deliberately excludes conflicts-with
# (symmetric, not a dependency direction), infers, and linked-from (a bare
# prose reference, not a real dependency).
_BLAST_RADIUS_REL_TYPES = {"depended-on-by", "overridden-by", "superseded-by"}


def _render_chain_node(lines: list[str], entry, depth: int) -> None:
    indent = "  " * depth
    meta = " · ".join(filter(None, [entry.category, entry.weight]))
    header = f"{indent}└─ {entry.id}  ({meta})"
    status_marker = _status_marker(entry.status)
    if status_marker:
        header += f"  {status_marker}"
    lines.append(header)
    lines.append(f"{indent}   {entry.title}")
    if entry.why:
        short = entry.why[:100] + "…" if len(entry.why) > 100 else entry.why
        lines.append(f"{indent}   Why: {short}")
    alts = (entry.alternatives or "").strip()
    if alts:
        first = alts.splitlines()[0]
        lines.append(f"{indent}   Rejected: {first}")


@mcp.tool()
def hippocampus_chain(dr_id: str) -> str:
    """Trace the full dependency chain from a decision record.

    Renders two directions:
    - Depends on: what constrains this decision (recursively follows
      depends-on links) — read this before assuming a decision stands alone.
    - Blast radius: what would be affected if you changed this decision
      (recursively follows depended-on-by, overridden-by, and superseded-by
      reverse links) — read this before modifying a decision others may
      depend on.

    Example: hippocampus_chain("DR-0015")
    """
    ensure_index(settings.ROOT)
    dr_id = dr_id.upper()
    index = load_index(settings.ROOT)
    if not index.entries:
        return "Index is empty. Run hippocampus_log or ensure .decisions/records/ exists."

    by_id = {e.id: e for e in index.entries}
    lines: list[str] = [f"Decision chain for {dr_id}:", ""]

    root_entry = by_id.get(dr_id)
    if not root_entry:
        lines.append(f"▶ {dr_id}  (not in index)")
        return "\n".join(lines)

    meta = " · ".join(filter(None, [root_entry.category, root_entry.weight]))
    header = f"▶ {root_entry.id}  ({meta})"
    status_marker = _status_marker(root_entry.status)
    if status_marker:
        header += f"  {status_marker}"
    lines.append(header)
    lines.append(f"   {root_entry.title}")

    def walk(target_id: str, depth: int, visited: set[str], next_ids) -> None:
        if target_id in visited or depth > MAX_CHAIN_DEPTH:
            return
        visited.add(target_id)
        entry = by_id.get(target_id)
        if not entry:
            lines.append(f"{'  ' * depth}└─ {target_id}  (not in index)")
            return
        _render_chain_node(lines, entry, depth)
        for next_id in next_ids(entry):
            walk(next_id, depth + 1, visited, next_ids)

    def depends_on_ids(entry) -> list[str]:
        return [r.target for r in entry.relationships if r.type == "depends-on"]

    def blast_radius_ids(entry) -> list[str]:
        return [r.source for r in entry.reverse_links if r.type in _BLAST_RADIUS_REL_TYPES]

    # Each direction gets its own visited set (seeded with the root, so a
    # cycle can't loop back and re-render it), so being rendered in one
    # section never suppresses a record from also appearing in the other.
    deps = depends_on_ids(root_entry)
    if deps:
        lines.append("")
        lines.append("  ── depends on ──────────────────")
        visited = {dr_id}
        for target in deps:
            walk(target, 1, visited, depends_on_ids)

    blast_radius = blast_radius_ids(root_entry)
    if blast_radius:
        lines.append("")
        lines.append("  ── blast radius (would be affected by a change) ──")
        visited = {dr_id}
        for target in blast_radius:
            walk(target, 1, visited, blast_radius_ids)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hippocampus MCP server — codebase decision memory"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory containing .decisions/ (default: cwd)",
    )
    # parse_known_args so MCP internal args don't cause failures
    args, _ = parser.parse_known_args()
    settings.ROOT = Path(args.root).resolve()
    # Build/refresh the index up front so the first query of the session
    # doesn't pay for it, and so a fresh clone with no index isn't read as
    # "nothing has been decided" (see WP-02).
    ensure_index(settings.ROOT)
    mcp.run()


if __name__ == "__main__":
    main()
