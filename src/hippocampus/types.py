from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class Relationship:
    type: str  # depends-on | supersedes | conflicts-with
    target: str  # DR-NNNN


@dataclass
class ReverseLink:
    type: str  # depended-on-by | superseded-by | conflicts-with
    source: str  # DR-NNNN


@dataclass
class IndexEntry:
    id: str
    title: str
    category: str
    status: str
    weight: str
    date: str
    file_path: str
    relationships: list[Relationship]
    reverse_links: list[ReverseLink]
    embedding: list[float]
    document: str
    why: str
    alternatives: str


@dataclass
class VectorIndex:
    entries: list[IndexEntry]
    built_at: int  # unix timestamp ms


@dataclass
class ClassificationResult:
    weight: str
    category: Optional[str]
    reason: str


@dataclass
class RetrievalResult:
    id: str
    title: str
    file_path: str
    score: float
    surfaced_via: str  # direct | relationship
    relevance_note: str
    why: str
    alternatives: str
    category: str
    weight: str
    depends_on: list[str]
    relationship_type: Optional[str] = None
    status: str = "accepted"
