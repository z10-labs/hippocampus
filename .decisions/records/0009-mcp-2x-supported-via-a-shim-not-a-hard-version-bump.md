# DR-0009: Support both mcp 1.x and 2.x via a shim, not a hard 2.x requirement

**Date**: 2026-08-01
**Category**: dependency
**Status**: accepted
**Weight**: heavy

## Context

WP-01 pinned `mcp<2` as a stopgap after mcp 2.0.0 relocated
`mcp.server.fastmcp`, breaking the documented install. WP-12 asked to scope
the actual 2.x migration and decide explicitly between two options: support
both versions via a compatibility shim, or drop 1.x support and require 2.x
(bumping to 0.3.0).

Scoping first, as instructed: installed mcp 2.0.0 in a scratch venv and
inspected the real package (not just changelog text). Findings —

- `FastMCP` no longer exists under that name. It was renamed to `MCPServer`
  and moved from `mcp.server.fastmcp` to `mcp.server` (top-level re-export
  of `mcp.server.mcpserver.MCPServer`).
- The constructor still takes `name` as the first positional argument.
- `.tool()` still supports the zero-argument decorator call
  (`@mcp.tool()`) this codebase uses, with the same signature-introspection
  and docstring-as-description behavior.
- `.run()` still defaults to `transport="stdio"`, matching the existing
  bare `mcp.run()` call in `main()`.

Confirmed all of this empirically: constructed an `MCPServer`, registered a
tool via the decorator, and inspected the resulting `Tool` object — not just
read about the API.

## Decision

Added a `try`/`except ImportError` shim in `server.py`: import `FastMCP`
from `mcp.server.fastmcp` (mcp < 2.0); on failure, import `MCPServer` from
`mcp.server` and alias it to the same local name (mcp >= 2.0). Removed the
`<2` upper bound from `pyproject.toml` entirely — `mcp>=1.0.0` now spans
both major versions. Verified the full test suite and the
`hippocampus-mcp --help` CLI entry point against fresh installs of both
mcp 1.29.0 and mcp 2.0.0.

Also added a `test-mcp-1x` CI job (alongside the existing jobs, which now
naturally resolve to the latest 2.x with no upper bound) so a future change
that breaks the 1.x half of the shim is caught automatically, not reported
by a user still on 1.x.

## Alternatives Considered

- Require mcp >= 2.0 and bump to 0.3.0, dropping 1.x — rejected: the shim
  costs six lines and one `type: ignore` comment, in exchange for not
  forcing every existing 1.x install to upgrade on the next release. Given
  the actual API surface this codebase touches is identical across both
  majors (constructor, zero-arg `.tool()`, bare `.run()`), there is no
  real migration cost to defer by NOT requiring 2.x — so there is no
  reason to force the break.
- Keep the `<2` pin indefinitely rather than porting at all — rejected:
  this is the status quo WP-12 exists to move past, and a pin with no
  upper-version plan eventually stops resolving at all as 1.x ages out of
  active maintenance upstream.

## Consequences

### Positive
- No forced major-version bump or install-time constraint change for
  existing users; both mcp majors work identically going forward.

### Negative / Trade-offs
- `mypy` cannot verify the `except` branch's import against whichever mcp
  version is actually installed in a given environment (it only has type
  stubs for one at a time) — the import is `# type: ignore`d there rather
  than fully type-checked. Acceptable: it's a two-line shim, not
  application logic, and both branches are exercised by CI against real
  installs of each major version, which is a stronger guarantee than
  static typing would add here anyway.

### Risks
- If a future mcp 2.x release changes `MCPServer`'s constructor, `.tool()`,
  or `.run()` signatures in a way this codebase's minimal usage happens to
  rely on, the shim would need updating — caught by the weekly CI schedule
  (WP-11) and the dedicated `test-mcp-1x`/latest-2.x jobs, not silently.

## Relationships

- (none)

## Review Trigger

Revisit if mcp 1.x is EOL'd upstream and the shim's `except` branch becomes
the only reachable path — at that point the `try` branch and the pin's
lower bound can both be removed as dead code.
