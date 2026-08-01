# Contributing

## Dev loop

```bash
git clone https://github.com/z10-labs/hippocampus.git
cd hippocampus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The suite is fast (~1s) because the embedding model is stubbed — see
[Test stubbing](#test-stubbing-conftestpy) below. Before pushing, run the same checks CI runs:

```bash
ruff check .
ruff format --check .
pytest --cov=src --cov-report=term-missing
mypy src/
```

`ruff format --check` will tell you if a file needs `ruff format .` run on it; the project is kept
`ruff format`-clean, not hand-formatted. `mypy` starts non-strict (see `pyproject.toml`'s
`[tool.mypy]` — `ignore_missing_imports` is on, nothing stricter yet); do not introduce a
stricter-mypy-only change without discussing it, since tightening the gate is a deliberate future
step, not something to back into as a side effect of one PR.

To exercise the tools against the real embedding model instead of the test stub (useful when
calibrating a threshold or debugging retrieval quality), point the server at a scratch directory:

```bash
python -c "
from pathlib import Path
import hippocampus.settings as settings
settings.ROOT = Path('/tmp/some-scratch-dir')
from hippocampus.server import hippocampus_log, hippocampus_query
print(hippocampus_log('...'))
"
```

The first call downloads the ~30MB `all-MiniLM-L6-v2` model from Hugging Face; expect it to be slow
once and fast after.

## Test stubbing (`conftest.py`)

This is the part of the test suite most likely to break silently if you're not expecting it.

`tests/conftest.py` replaces the real embedding calls with `fake_embed` — a deterministic
hashed-bag-of-words vector, not a real model — so the suite runs offline and in about a second. The
non-obvious part: **the stub is patched onto three separate names**, not one:

```python
monkeypatch.setattr(indexer, "embed", fake_embed)
monkeypatch.setattr(indexer, "embed_many", fake_embed_many)
monkeypatch.setattr(retriever, "embed", fake_embed)
```

`retriever.py` does `from hippocampus.indexer import embed`, which binds retriever's own `embed`
name to whatever function object `indexer.embed` pointed to *at import time*. Patching
`indexer.embed` after that does not change `retriever.embed`'s binding — Python's `from x import y`
copies a reference, it doesn't create a live alias. So if you add a new call to `embed()` or
`embed_many()` in a module that imports it this way, and don't patch that module's own copy of the
name in `conftest.py`, the test suite will silently fall through to the real model: slow, requires
network on first run, and not what the test is actually meant to exercise. If a test starts hanging
or trying to download something, check this first.

Retrieval scores in tests are a function of `fake_embed`'s hashed-bag-of-words scheme, not the real
model's semantics — don't tune a relevance threshold or calibrate a constant against `fake_embed`'s
output. When a change needs real semantic behavior verified (a new relevance floor, a new soft-match
threshold), do that calibration separately against the real model on a realistic corpus, and only
assert *filtering behavior* in unit tests via injected/monkeypatched scores — never a hardcoded
numeric threshold, since retuning the constant would then break the suite for no real reason.

## Record format contract

Everything the indexer needs from a decision record file lives in
`src/hippocampus/indexer.py`'s `_parse_file` and its helpers. If you're writing a new record
template, a test fixture, or documentation, this is the actual contract, not the example in the
README:

- A record file must start with a top-level heading matching `# DR-NNNN: Title` (regex
  `^# (DR-\d+):\s*(.+)`, first line). No heading, no record — `_parse_file` returns `None` and the
  file is silently skipped by `build_index`.
- Metadata fields are `**Name**: value` lines anywhere in the file, read by `_field(content, name)`:
  `**Category**`, `**Status**`, `**Weight**`, `**Date**`. Each has a fallback default if missing
  (`architectural`, `proposed`, `standard`, empty date respectively) — a record missing all of them
  still indexes, just with defaults that may not be what you meant.
- `## Why` or `## Context` — free text, truncated to ~220 chars for inline display
  (`_parse_why`).
- `## Alternatives Skipped` or `## Alternatives Considered` — bullet lines starting with `-`, `*`,
  or `1.` (any of the three; `_parse_alternatives`), each truncated to 80 chars. A line starting
  with `because` is treated as a continuation of the previous bullet and dropped, not read as its
  own alternative.
- `## Relationships` — lines matching
  `- (overrides|inferred-by|depends-on|supersedes|conflicts-with|references): DR-NNNN`. Prose
  mentions of `DR-NNNN` elsewhere in Why/What/Context/Decision sections are also picked up as a
  soft `references` relationship if not already covered by an explicit line.

Deferred entries (`.decisions/deferred.md`) are a different, append-only format — one shared file,
parsed by `_parse_deferred_blocks` — not one-file-per-record. See `write_deferred_entry` in
`logger.py` for the exact block shape (`## YYYY-MM-DD — title` heading, then
`**What was deferred**` / `**Why deferred**` / `**Review trigger**` / `**Risk of deferral**` lines).

## Decision records for this repo's own decisions

This project dogfoods itself: non-obvious design choices made while working on it are logged as
real decision records under `.decisions/records/` in this repo, the same way a consuming project
would use the tool. If you make a judgment call that isn't obvious from the code or the PR diff
alone — a trade-off, a rejected alternative, a deliberate deviation from a written spec — log it.
