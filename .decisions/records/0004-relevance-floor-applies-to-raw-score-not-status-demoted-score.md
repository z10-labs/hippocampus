# DR-0004: The relevance floor filters on raw similarity, not the status-demoted score

**Date**: 2026-08-01
**Category**: architectural
**Status**: accepted
**Weight**: heavy

## Context

WP-06 adds `MIN_DIRECT_SCORE`, a floor below which a direct hit is treated as
noise and filtered out before the top-N cut. WP-04 (already merged) added a
`SUPERSEDED_SCORE_MULTIPLIER` that halves a non-live record's score before
ranking, so a superseded record ranks behind an equally-relevant accepted
one — but WP-04 also explicitly requires superseded records are never
filtered out entirely, only demoted, since the README promises the
superseded argument stays part of the readable history.

These two features weren't designed together (WP-06's dependency in the
plan is only WP-02, not WP-04), and combining them naively breaks WP-04's
guarantee: if the floor is applied to the already-halved (effective) score,
a superseded record that is genuinely relevant can be halved to just below
the floor and disappear from results entirely — silently violating "never
filter a superseded record out" the moment a real query happens to land a
superseded record close to the floor.

Caught by an existing WP-04 test
(`test_a_superseded_record_is_demoted_below_an_accepted_one_at_equal_similarity`)
failing once the floor was introduced.

## Decision

Compute both a raw cosine score and a status-adjusted effective score per
entry. Sort and display using the effective score (so demotion still
affects ranking order, per WP-04). Apply `MIN_DIRECT_SCORE` to the *raw*
score only. A record is filtered for being irrelevant to the query, never
for being superseded — demotion and the noise floor are kept as separate,
independently-reasoned concerns operating on different numbers.

## Alternatives Considered

- Apply the floor to the effective (demoted) score, as a literal first
  reading of WP-06's text suggests — rejected: directly breaks WP-04's own
  acceptance criterion the first time a superseded record's demoted score
  crosses the floor, which is exactly the failure this project's tests
  exist to catch, and did.
- Exempt all non-live-status records from the floor entirely, the same way
  relationship-expanded results are exempt — rejected: that would let a
  superseded record that has genuine zero relevance to the query still
  appear as a "direct" hit, reintroducing noise the floor exists to remove.
  The floor should still apply to superseded records — just measured before
  demotion, not after.

## Consequences

### Positive
- Both WP-04's and WP-06's acceptance criteria hold simultaneously, verified
  by the full suite (112 tests) rather than by inspection alone.

### Negative / Trade-offs
- `retriever.query`'s internal scoring now carries two numbers (raw,
  effective) instead of one, a small increase in local complexity to keep
  the two features correctly decoupled.

### Risks
- None identified.

## Relationships

- (none)

## Review Trigger

Revisit if a future WP introduces a third score adjustment (e.g. a recency
decay) — the raw/effective split established here is the place to hang a
third concern, not a reason to collapse back to one number.
