# app/papers/types.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class PaperHit:
    provider: str              # "crossref" | "pubmed"
    title: str
    doi: str | None
    url: str
    published_year: int | None
    authors: str | None
    abstract: str | None
    raw_score: float | None
