# DR-0005: Cache the numpy embeddings matrix itself, not just the parsed index

**Date**: 2026-08-01
**Category**: performance
**Status**: accepted
**Weight**: standard

## Why

WP-07 says to "hold embeddings as a single (N, D) float32 array" and
separately calls the in-memory index-parse cache "the single biggest win —
do it before anything else if you only do one." Implementing the numpy scan
first by building `np.asarray([e.embedding for e in entries])` fresh inside
`_score_all` on every call technically vectorized the multiply, but
profiling (500 x 384) showed the array construction itself, not the dot
product, was the dominant per-query cost: ~3ms to build the matrix from a
list of Python-float lists vs ~0.004ms for the multiply against an
already-built matrix. The index-parse cache alone doesn't catch this — it
only avoids re-reading JSON, not re-building the matrix from the parsed
Python-float lists on every single query.

## What

Added a second cache, `indexer._matrix_cache`, keyed the same way as the
existing index-parse cache (path, file mtime), storing the built (N, D)
float32 matrix. Exposed as `indexer.embeddings_matrix(root)`;
`retriever._score_all` now takes a pre-built matrix instead of a list of
entries, so the caller decides whether to fetch it fresh or from cache.

## Trade-off

A second cache dict to keep in sync with the first, both using the same
(path, mtime) staleness check independently rather than sharing one lookup.
Slightly more bookkeeping than one merged cache, in exchange for not
coupling "parsed index" and "scoring matrix" into one cached value — many
callers (hippocampus_list, hippocampus_chain) only ever need the parsed
entries and have no reason to pay for building a matrix they'll never use.

## Alternatives Skipped

- Store the matrix as a field on VectorIndex itself — rejected: VectorIndex
  is JSON-serialized via dataclasses.asdict in _save_index; adding a numpy
  array field would either break that round trip or require excluding it
  from serialization, adding complexity to a dataclass that's supposed to
  stay a plain data shape.
- Skip matrix caching and accept ~3ms/query — rejected: it's the actual
  bottleneck per profiling, and the cache is a direct, low-risk extension of
  the exact (path, mtime) invalidation scheme already proven correct for the
  index-parse cache, not a new invalidation strategy to get wrong.

## Relationships

- (none)
