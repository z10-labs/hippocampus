# Hippocampus — Agent Instructions

This is the Hippocampus MCP server. It provides decision memory tools to any project that
configures it. Read this before modifying the server code.

## What this project is

Hippocampus is a stdio MCP server. It exposes five tools that any MCP-aware agent can call
directly — no shell commands, no npm scripts. Decision records live in the consuming project's
`.decisions/records/` directory.

## Development setup

```bash
cd /Users/dzithendolabs/Documents/z10labs/hippocampus-v2
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Testing against another project

Add to the consuming project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "hippocampus": {
      "command": "python",
      "args": [
        "/Users/dzithendolabs/Documents/z10labs/hippocampus-v2/src/hippocampus/server.py",
        "--root", "."
      ]
    }
  }
}
```

`"."` resolves to the consuming project's root because Claude Code sets CWD there when
spawning the stdio process.

After editing server code, start a new Claude Code session (or `/mcp` to reconnect) —
the server is spawned fresh each session.

## Tools exposed

| Tool | When to use |
|---|---|
| `hippocampus_query` | Before any non-trivial decision |
| `hippocampus_log` | At each decision fork (two-phase: suggest then confirm) |
| `hippocampus_classify` | When unsure if something is worth recording |
| `hippocampus_list` | To browse all records by category or weight |
| `hippocampus_chain` | To trace the full dependency chain of a DR |

## Index location

The vector index lives at `.hippocampus/index.json` in the consuming project root (gitignored).
It is rebuilt automatically after each `hippocampus_log` call. To force a full rebuild, call
`build_index(root, force=True)` directly or delete `.hippocampus/index.json`.

## Constraints

- No external services — works fully offline after first model download (~30MB)
- No API key required
- `--root` argument is mandatory for cross-repo use; defaults to CWD
