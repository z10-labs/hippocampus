# DR-0008: ruff configured with a conservative rule set and 150-char lines, not defaults

**Date**: 2026-08-01
**Category**: dependency
**Status**: accepted
**Weight**: standard

## Why

WP-11 asks for `ruff check` and `ruff format --check` in CI. Running ruff
with no `[tool.ruff]` config at all against the existing codebase surfaced
58 findings — but most were from rule families (`I001` import sorting,
`PLR0402` import-alias style) this codebase's actual conventions don't
follow, not real defects. Running `ruff format` with ruff's default line
length (88) would also have reformatted every file with dense regex
alternations in `classify.py` into far less readable multi-line wraps.

## What

Configured `select = ["E", "F", "W"]` (the traditional pycodestyle +
pyflakes baseline) instead of ruff's broader modern defaults, and
`line-length = 150` instead of 88. Under this config, the real findings
were 3 genuine issues (two unused imports, one pointless f-string) — fixed
directly — plus a codebase-wide `ruff format` pass, which changed only
whitespace/quote-style, verified behavior-preserving via the full test
suite before and after.

## Trade-off

A narrower rule set than ruff's shipped defaults means some categories of
issue (import order, several pylint-style refactor suggestions) are not
enforced yet. Deliberately deferred rather than chased in this pass, same
reasoning WP-11 already applies to starting mypy non-strict: get the gate
in place first, tighten later once it's proven stable.

## Alternatives Skipped

_See description above._

## Relationships

- (none)
