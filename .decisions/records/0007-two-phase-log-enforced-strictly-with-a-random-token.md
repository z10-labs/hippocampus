# DR-0007: Two-phase log flow enforced strictly, using a random (not hash-derived) token

**Date**: 2026-08-01
**Category**: architectural
**Status**: accepted
**Weight**: heavy

## Context

WP-10 flagged that nothing enforced the two-phase `hippocampus_log` flow: an
agent could pass `confirmed=True` on the first call, skip Phase 1 entirely,
and never see the related-decisions list it's supposed to link against. The
write-up explicitly asked for a judgment call: enforce strictly (Phase 2
without a valid token writes nothing), or softer (token optional, but a
missing one gets logged as a visible warning in the record's provenance).

It also suggested "a short token derived from the description (an 8-char
hash is sufficient)" and asked to keep tokens in a bounded module-level
dict.

## Decision

Enforced strictly: Phase 2 without a valid token writes nothing and returns
Phase 1's output again (with a fresh token), matching the pattern already
set by WP-03's heavy-record-requires-alternatives enforcement.

Deviated from the letter of the suggested mechanism: the token is
`secrets.token_hex(4)` (8 random hex chars), not a hash of the description.
A hash of the description is a formula documented in the tool's own
docstring (since agents read docstrings as the contract) — any caller could
compute `sha256(description)[:8]` themselves without ever actually calling
Phase 1, silently defeating the enforcement the moment the formula is
known. A random token can only be obtained by actually calling Phase 1,
which is the entire point. The module-level dict maps token to the
description it was issued for, so Phase 2 can still detect "the description
changed since Phase 1" by comparing stored text to current text.

## Alternatives Considered

- Softer version: optional token, Phase 2 without one still writes but
  flags the record's provenance — rejected: this is exactly what the
  write-up calls "advisory," which is the property this WP exists to
  remove. An agent optimizing for turn count has no reason to supply an
  optional token.
- Literal hash-of-description token, no dict needed — rejected: stateless
  and simpler, but gameable by any caller that has seen the derivation
  formula (which the docstring must describe for agents to use the tool
  correctly), defeating the guard rail's actual purpose.

## Consequences

### Positive
- An agent cannot reach Phase 2 without a real Phase 1 call for this exact
  description having happened first, in this process.

### Negative / Trade-offs
- One more round trip is mandatory for every logged decision, including
  simple ones. Accepted as the intended trade-off per the write-up's own
  framing ("trades agent convenience for record quality").

### Risks
- None identified. The token dict is bounded (100 entries, FIFO eviction)
  and per-process, so it cannot grow unbounded or leak across sessions.

## Relationships

- (none)

## Review Trigger

Revisit if real usage shows agents retrying Phase 1 repeatedly just to get
a fresh token without reading the candidates it surfaces, which would mean
the guard rail is being routed around mechanically rather than actually
changing behavior.
