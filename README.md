# Hippocampus

**Decision memory for AI coding agents.** An MCP server that lets an agent ask *"what did we already decide, and why?"* before it writes a line of code.

---

## The problem

An agent starts every session with amnesia.

Git history records **what changed**. The code records **what is**. Neither records **what was rejected, and why** — so the agent re-litigates settled arguments, reaches for the library the team already ruled out, and quietly contradicts decisions nobody wrote down anywhere it can see.

The knowledge exists. It's in a Slack thread from March, in someone's head, in a PR comment on a merged branch. It is not anywhere the agent will look.

Hippocampus puts it somewhere the agent will look.

## What it is

A stdio MCP server exposing five tools. Any MCP-aware agent calls them directly — no shell commands, no npm scripts, no prompting the human to remember.

Decision records are **plain markdown** in your repo, under `.decisions/records/`. They're committed, PR-reviewable, greppable, and readable without this tool ever being installed. The vector index is a local, gitignored cache derived from them — delete it any time and it rebuilds.

Cloning a repo that already has `.decisions/records/` but no index yet (or checking out a branch where records changed by hand) doesn't require a manual reindex step. Every tool call checks freshness first — missing index, an edited record, a deleted one — and rebuilds incrementally before answering, so the first query of a session is never a false "nothing's been decided here."

No external services. No API key. One ~30 MB embedding model, downloaded once, then fully offline.

## Install

```bash
pip install git+https://github.com/z10-labs/hippocampus.git
```

Works with `mcp` 1.x or 2.x — the 2.0 release renamed `FastMCP` to `MCPServer` and relocated it
from `mcp.server.fastmcp` to `mcp.server`; `server.py` shims the import so either major version
resolves without a pin.

Register it with your agent — for Claude Code, in the consuming project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "hippocampus": {
      "command": "hippocampus-mcp",
      "args": ["--root", "."]
    }
  }
}
```

`"."` resolves to the consuming project's root, because the MCP host sets CWD there when it spawns the process. Start a new session, and the tools are live.

## The five tools

| Tool | When the agent calls it |
|---|---|
| `hippocampus_query` | Before any non-trivial decision — choosing a library, designing a schema, picking an interface |
| `hippocampus_log` | At each decision fork. Two-phase and enforced: Phase 1 suggests related records and issues a token; Phase 2 requires that token, or nothing gets written |
| `hippocampus_classify` | When unsure whether something is even worth recording |
| `hippocampus_list` | To browse precedent by category or weight before starting in an unfamiliar area |
| `hippocampus_chain` | Two directions from one record: what it depends on, and the blast radius — everything that would be affected if you changed it |

## How it works

Retrieval is not just semantic search. A query embeds, cosine-scans the index against a relevance floor — low-similarity noise is filtered before results are even assembled, not just truncated — and then **expands along the decision graph in both directions**:

- **Outbound** — what this decision depends on. The constraints behind it.
- **Inbound** — what depends on this decision. The blast radius if you change it.
- **Soft-related** — high-similarity records with no declared link.

That second hop is the whole point. Pure similarity search returns what *sounds* like your query. The graph hop returns what actually *constrains* it — including the record that supersedes your best match, which similarity alone would happily hide. Superseded records aren't filtered out (the argument stays part of the readable history) but they are ranked below live ones and flagged inline — `⚠ SUPERSEDED BY DR-0002` on the id line — so a dead decision can't be skimmed past as if it were still in force.

```
Query: "drop Redis, move rate limiting in-memory"

  DR-0006  [direct | 0.74]           Redis for shared counters
  DR-0014  [via depended-on-by]      Sliding-window rate limiter
  DR-0011  [via depended-on-by]      Distributed lock on job claim
           ^^^ removing Redis invalidates these two
```

Full architecture diagrams: [`docs/hippocampus-architecture.drawio`](docs/hippocampus-architecture.drawio) — six pages covering the module graph, both tool flows, the indexing pipeline, and an end-to-end session in a repo.

## Record format

Records are ADRs with a relationship block. A `standard` record:

```markdown
# DR-0014: Sliding-window rate limiter in Redis

**Date**: 2026-03-04
**Category**: performance
**Status**: accepted
**Weight**: standard

## Why
Token bucket allows a full-burst refill at the window edge, which
defeats the point for our abuse case.

## Trade-off
Sliding window costs one extra Redis round-trip per request.

## Alternatives Skipped
- Token bucket — burst at window edge
- In-memory counters — breaks across replicas

## Relationships
- depends-on: DR-0006
```

The Alternatives Skipped section above is written from `hippocampus_log`'s `alternatives` argument — a JSON array of strings, each one `"option — reason rejected"`, not a bare option name. It's optional for `standard` records but required for `heavy` ones: `hippocampus_log` rejects a heavy record in Phase 2 if none were supplied, rather than writing the placeholder text a future reader would have to trust blindly. Nothing enforces the same on `standard` records; sparse or missing alternatives there is a judgment call left to the agent.

`heavy` records (architectural, security, compliance, cost, domain) additionally carry Consequences and a Review Trigger. Deliberate non-decisions go to `.decisions/deferred.md` — because "we consciously chose not to decide this yet" is itself worth remembering, and deferred entries are retrievable through the same tools as real decisions (badged `⏸ NOT YET DECIDED` so they aren't mistaken for one).

Relationship types: `depends-on`, `supersedes`, `conflicts-with`, `overrides`, `inferred-by`, `references`. Inverses (`depended-on-by`, `superseded-by`, …) are computed at index time, so the inbound hop is a lookup rather than a scan.

Logging a record that `supersedes` another patches the old record's `**Status**` line in place. The superseded decision stays readable — the repo keeps the whole argument, not just the winner.

## Validation

This isn't a thought experiment. It was tested against a 13-feature TypeScript job processor built across 7 spec versions by autonomous agents:

| What was tested | Result |
|---|---|
| Will agents write records with accurate relationships and alternatives? | 3/3 records per run, relationships non-empty — **alternatives needs re-validation, see note below** |
| Can a *fresh* agent understand the architecture from the decision index alone? | Source-file reads dropped from 13/21 → 1/21 → **0/21** |

Method, logs, and raw data: [hippocampus-research](https://github.com/z10-labs/hippocampus-research) · [hippocampus-validation](https://github.com/z10-labs/hippocampus-validation)

> **Note on the alternatives claim (added after an internal fix pass):** the original run predates a
> fix where `hippocampus_log` had no `alternatives` parameter at all — every record written through
> the tool got a hardcoded `_No alternatives documented._` placeholder, regardless of what the agent
> supplied. The "both fields non-empty" result as originally worded is not reproducible against that
> code path; whatever produced it, it wasn't testing what this line implies. `alternatives` is now a
> real parameter (a JSON array of `"option — reason"` strings, required for heavy records), and the
> round trip from `hippocampus_log` through `hippocampus_query` is covered by this repo's own test
> suite — but the *specific number* in this table needs an actual re-run against the current code by
> whoever maintains the `hippocampus-validation` suite before it's restated as current. A weaker
> honest number here is worth more than a strong one nobody can reproduce.

## Limitations

Worth knowing before you adopt it:

- **Classification is regex, not a model.** `classify.py` uses keyword rules to assign weight and category. It's fast, offline, and predictable — and it will misfile things. Both are overridable per call. Two concrete failure modes: a description that doesn't match any more specific category keyword defaults to `architectural`, which is itself a `heavy`-weight category — in practice most decisions logged without an explicit `weight="standard"` override classify as heavy, not just the ones that obviously are. And category checks are an `elif` chain, so a description matching more than one category's keywords (e.g. "encrypt cached auth tokens for lower latency") is silently categorized by whichever check happens to come first, not by which category actually fits best.
- **Retrieval is a vectorized linear scan.** Cosine similarity against every entry — no ANN index — but not a naive per-entry Python loop either: embeddings are unit-normalized and held as a cached numpy matrix, so it's one matrix-vector multiply per query, not N. Fine at ADR scale (tens to hundreds, comfortably into the low thousands, of records); it is not a vector database and does not pretend to be one.
- **Record quality depends on the agent.** Hippocampus stores what it's given. An agent that logs `"used Redis"` with no Why produces a record worth nothing. The two-phase `log` flow (Phase 1 issues a token; Phase 2 requires it, or nothing gets written) exists to push against exactly this — it is enforced, not advisory.

## Development

```bash
git clone https://github.com/z10-labs/hippocampus.git
cd hippocampus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full dev loop (lint, type-check, coverage), the
test-stubbing approach in `conftest.py` (non-obvious and easy to break), and the record-format
contract the indexer actually parses.

## License

MIT — see [LICENSE](LICENSE).
