# DR-0002: Enforce alternatives for heavy records at the tool boundary, not by softening the classifier

**Date**: 2026-08-01
**Category**: architectural
**Status**: accepted
**Weight**: heavy

## Context

WP-03 makes `hippocampus_log` require an `alternatives` argument in Phase 2
whenever a record classifies as `heavy`. Investigating which existing test
fixtures broke under this rule showed that `classify()` defaults any
description to category `architectural` unless another category's keywords
match — and `architectural` is in `HEAVY_CATEGORIES`. In practice this means
most decisions logged without an explicit `weight="standard"` override will
classify as heavy, and will now be rejected in Phase 2 unless the caller
supplies alternatives.

## Decision

Enforce the alternatives requirement at the `hippocampus_log` tool boundary
exactly as WP-03 specifies, and leave the classifier's heavy-by-default
behavior for `architectural` category untouched. Do not soften the
requirement (e.g. by only enforcing it for a narrower set of categories) to
compensate for the classifier casting a wide net.

## Alternatives Considered

- Only require alternatives for a narrower set of categories (e.g. security,
  compliance, cost) — rejected: it reintroduces exactly the silent-placeholder
  problem WP-03 exists to fix, just for a smaller set of records, and the
  README's own pitch treats architectural decisions as the primary case that
  matters.
- Adjust `classify()` so `architectural` is no longer heavy by default —
  rejected as out of scope for this WP: `classify.py` is not in WP-03's file
  list, and changing default classification behavior deserves its own
  decision and its own tests, not a side effect of enforcing a write-time
  requirement.

## Consequences

### Positive
- No heavy record can be written with a placeholder alternatives section;
  the round trip this project's own tests exercise is honest end to end.

### Negative / Trade-offs
- Agents that don't pass `weight="standard"` explicitly will hit the new
  Phase 2 rejection far more often than the WP-03 write-up's framing
  ("heavy" as a special case) suggests, because the classifier's practical
  default is heavy. This will surface as friction the first time it's used
  against a real project, not just in this repo's own test suite.

### Risks
- If this friction turns out to be excessive in practice, the fix belongs in
  `classify.py`'s category-to-weight mapping, not in loosening the
  alternatives requirement.

## Relationships

- (none)

## Review Trigger

Revisit if real usage shows agents routing around the requirement by
passing `weight="standard"` on decisions that are genuinely heavy, just to
avoid supplying alternatives.
