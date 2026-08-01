# DR-0003: Deferred entries are fully rebuilt on every build_index call, not incrementally tracked

**Date**: 2026-08-01
**Category**: architectural
**Status**: accepted
**Weight**: heavy

## Context

WP-05 makes `build_index` parse `.decisions/deferred.md` into `DEF-NNNN` index
entries alongside the existing `DR-NNNN` entries parsed from
`.decisions/records/*.md`. The existing incremental-skip logic for `DR-`
records tracks staleness per file (one file per record, compared against the
file's own mtime). Deferred entries don't have that: all deferrals live in
one shared `deferred.md`, so there is no per-entry mtime to compare against
the last build.

## Decision

Recompute all `DEF-` entries from scratch on every `build_index` call —
delete every existing `DEF-` entry from `by_id` and re-parse+re-embed the
whole `deferred.md` file, rather than trying to track which blocks are
"new" since the last build. This keeps deferred handling independent of the
DR- deletion-pruning logic added in WP-02 (`seen_ids` there is only ever
populated from `record_files`, so DEF- ids are deliberately excluded from
that prune and handled in their own pass).

## Alternatives Considered

- Track per-block staleness by hashing or diffing deferred.md content
  against a stored snapshot — rejected: deferred.md is expected to stay
  small (a project defers a handful of decisions, not hundreds), so the
  embedding cost of a full re-parse is negligible, and the added bookkeeping
  buys nothing at this scale. This mirrors the reasoning already given in
  WP-07 for rejecting content-hashing generally.
- Give each deferred block its own file under `.decisions/deferred/`,
  matching the one-file-per-record pattern used for DR- records — rejected
  as out of scope: `write_deferred_entry`'s existing single-file, append-only
  format is unchanged by this WP, and switching it is a bigger, separate
  decision about the on-disk format, not a retrieval fix.

## Consequences

### Positive
- No incremental-skip bookkeeping to get wrong for deferred entries; the
  same class of millisecond-mtime race documented in DR-0001 (and the flaky
  test discovered while validating WP-05, fixed separately) simply cannot
  occur for deferred entries, since there's no per-entry mtime comparison at
  all.

### Negative / Trade-offs
- Every `build_index` call re-embeds every deferred block, even unchanged
  ones. Acceptable at the stated scale (tens of records); would need
  revisiting if a project accumulates hundreds of deferrals.

### Risks
- None identified at the current expected scale.

## Relationships

- (none)

## Review Trigger

Revisit if `deferred.md` grows large enough that re-embedding it on every
build_index call becomes measurable (see WP-07's performance work for the
DR- side of this same question).
