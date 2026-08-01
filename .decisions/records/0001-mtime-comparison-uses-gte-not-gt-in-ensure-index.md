# DR-0001: ensure_index freshness check uses >= for mtime comparison, not >

**Date**: 2026-08-01
**Category**: architectural
**Status**: accepted
**Weight**: heavy

## Context

WP-02 adds `ensure_index(root)`, a cheap freshness check called at the top of
`hippocampus_query`, `hippocampus_list`, `hippocampus_chain`, and once in
`main()`. It compares the newest record mtime on disk against the index's
`built_at` timestamp (both in unix ms) to decide whether to trigger an
incremental rebuild.

## Decision

Use `max_mtime_ms >= built_at` rather than `max_mtime_ms > built_at`. This
means a record edited in the same millisecond as the last build is treated
as stale and triggers a rebuild, even though in the common case (a record
written well before the build, or well after) the two clocks never actually
tie.

## Alternatives Considered

- Strict `>` comparison — rejected: on filesystems with coarse mtime
  granularity (some report only whole seconds), a record written in the same
  tick as the build can compare equal to `built_at` and be silently treated
  as already-indexed, which is exactly the stale-read bug this WP exists to
  fix. The file-count check is a backstop for additions/deletions, but not
  for in-place edits that don't change the file count.
- A stronger consistency mechanism (content hash instead of mtime) — rejected
  as overkill for a cheap per-call check; the cost of an occasional harmless
  redundant rebuild is lower than the cost of implementing and maintaining
  hashing, and the incremental build already skips unchanged files by mtime
  internally.

## Consequences

### Positive
- A record edited and re-saved within the same millisecond as a prior build
  is never missed.

### Negative / Trade-offs
- Rare same-millisecond writes trigger one harmless redundant `build_index`
  call rather than being skipped. Confirmed via test
  (`test_ensure_index_does_not_rebuild_when_nothing_changed`) that this only
  fires when mtime and built_at genuinely tie; normal operation with any
  time gap between write and build does not redundantly rebuild.

### Risks
- None identified.

## Relationships

- (none)

## Review Trigger

Revisit if `ensure_index` shows up as a measurable hot path once WP-07
(performance) lands and the corpus grows past a few hundred records.
