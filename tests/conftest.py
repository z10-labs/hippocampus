"""Shared fixtures.

The real embedding model is a ~30MB download and is slow to load. Tests stub it
with a deterministic bag-of-words vector so they stay offline and fast — the
retrieval logic under test is the graph expansion, not the model itself.
"""
from __future__ import annotations

import hashlib

import pytest

import hippocampus.indexer as indexer
import hippocampus.retriever as retriever

DIMS = 64


def fake_embed(text: str) -> list[float]:
    """Deterministic hashed bag-of-words. Similar text → similar vectors."""
    vec = [0.0] * DIMS
    for word in text.lower().split():
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % DIMS] += 1.0
    if not any(vec):
        vec[0] = 1.0
    return vec


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    # retriever does `from ... import embed`, so it holds its own reference —
    # both names have to be patched.
    monkeypatch.setattr(indexer, "embed", fake_embed)
    monkeypatch.setattr(retriever, "embed", fake_embed)


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".decisions" / "records").mkdir(parents=True)
    return tmp_path


def write_record(root, num, title, body="", category="architectural", weight="standard", status="accepted"):
    path = root / ".decisions" / "records" / f"{num}-{title.lower().replace(' ', '-')}.md"
    path.write_text(
        f"# DR-{num}: {title}\n\n"
        f"**Date**: 2026-01-01\n"
        f"**Category**: {category}\n"
        f"**Status**: {status}\n"
        f"**Weight**: {weight}\n\n"
        f"{body}\n"
    )
    return path
