# DR-0006: The write lock uses fcntl.flock, dropping Windows portability

**Date**: 2026-08-01
**Category**: dependency
**Status**: accepted
**Weight**: standard

## Why

`_with_lock` used `O_CREAT | O_EXCL` to create a marker file, deleted on
release. A crashed or killed process skips the `finally` cleanup, leaving
the marker behind forever — every subsequent write then fails with "Could
not acquire decision log lock within 5 seconds" and no hint that deleting a
file fixes it. WP-08 offered two options: switch to `fcntl.flock` on a
persistent lock file (OS releases it on process death, POSIX-only), or keep
the create/delete scheme but write the pid and check liveness on contention
(portable to Windows, more code).

## What

Used `fcntl.flock`. Nothing in this project (pyproject.toml classifiers,
README, CI) claims or implies Windows support, and the codebase already
targeted POSIX-only behavior implicitly (nothing else uses a
Windows-compatible file API). `fcntl` is stdlib on macOS and Linux, which
covers the actual target audience for an offline, file-based, per-repo dev
tool. The lock file is now persistent (never deleted, only unlocked),
opened in append mode so it's never truncated, and added to `.gitignore`.

## Trade-off

If Windows support becomes a real requirement later, `_with_lock` is the
only place that needs to change — it is not threaded through the rest of
the codebase.

## Alternatives Skipped

- Keep O_CREAT|O_EXCL, write the pid, check liveness (e.g. via
  `os.kill(pid, 0)`) on contention — rejected: `os.kill`-based liveness
  checks are themselves platform-inconsistent in edge cases (pid reuse), add
  real complexity, and buy portability nobody has asked for on a project
  with no other cross-platform constraints.

## Relationships

- (none)
