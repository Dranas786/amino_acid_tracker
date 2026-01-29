# app/papers/ranker.py
# ------------------------------------------------------------
# Step 3: Rank / score paper search hits.
#
# Why this exists:
# - Crossref gives lots of irrelevant stuff (it searches everything).
# - PubMed is better but still broad.
# - We need a cheap, explainable filter that prefers papers
#   likely to contain amino-acid measurements for a food.
#
# Output:
# - score in [0, 1]
# - "reasons" list so you can see WHY something ranked high
# ------------------------------------------------------------

"""
NOTE (Roadmap):
---------------
This module currently uses a lightweight heuristic scoring system
(token overlap, keyword signals, metadata checks) to rank scientific
papers for nutrient-related relevance.

Next planned upgrades (non-blocking):
1. Introduce TF-IDF–based similarity scoring for improved ranking.
2. Combine heuristic score + TF-IDF score into a single weighted score.
3. Add a simple ML classifier (food-related vs junk) trained on
   accumulated failed_searches labels.
4. Optionally upgrade to embeddings for semantic search if scale demands.

Current design prioritizes:
- Explainability
- Low compute cost
- Deterministic behavior
"""


from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

# If you already have PaperHit in a types.py file, import it.
# Otherwise, import from wherever you defined it.
from app.papers.types import PaperHit  # <- adjust if your path differs


# -----------------------------
# Scoring config (tune anytime)
# -----------------------------

# Words/phrases strongly associated with composition / measurement tables
MEASUREMENT_KEYWORDS = [
    "amino acid",
    "amino acids",
    "amino-acid",
    "composition",
    "profile",
    "content",
    "quantification",
    "determination",
    "analysis",
    "hplc",
    "uhplc",
    "chromatography",
    "mass spectrometry",
    "lc-ms",
    "gc-ms",
    "mg/100g",
    "g/100g",
    "protein quality",
    "digestibility",
    "iaao",  # indicator amino acid oxidation
]

# Words that usually indicate "not what we want" (policy / literature / non-measurement)
LOW_VALUE_KEYWORDS = [
    "review",
    "systematic review",
    "meta-analysis",
    "scoping review",
    "editorial",
    "commentary",
    "case report",
    "protocol",
    "guideline",
]

# Words that often indicate the paper is in a totally different domain
OFF_DOMAIN_KEYWORDS = [
    "novel",
    "poetry",
    "literature",
    "philosophy",
    "politics",
    "chickpeas, and",  # example weird crossref junk you saw
]

# Basic token cleanup (simple + fast) (cheap helper to tokenize)
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ScoredHit:
    hit: PaperHit
    score: float
    reasons: List[str]


def _normalize_text(s: str | None) -> str:
    """Lowercase + collapse whitespace. Safe for None."""
    if not s:
        return ""
    return " ".join(s.lower().strip().split())


def _tokens(s: str) -> List[str]:
    """Return simple alphanumeric tokens."""
    return _WORD_RE.findall(s)


def _contains_phrase(text: str, phrase: str) -> bool:
    """Phrase match on normalized text."""
    return phrase in text


def score_hit(hit: PaperHit, query: str) -> ScoredHit:
    """
    Produce a score in [0, 1] for a PaperHit.

    Inputs:
    - hit: PaperHit(title, abstract, etc.)
    - query: the user's search query, e.g. "lysine chickpeas"

    Approach:
    - Cheap, explainable feature scoring:
      1) keyword boosts (measurement/composition terms)
      2) penalties for reviews / non-measurement paper types
      3) query overlap (does title/abstract mention query tokens?)
      4) provider boost (PubMed usually more relevant to nutrition)
      5) clamp score to [0, 1]
    """
    title = _normalize_text(hit.title)
    abstract = _normalize_text(hit.abstract)
    q = _normalize_text(query)

    combined = f"{title} {abstract}".strip()

    reasons: List[str] = []
    raw = 0.0

    # -------------------------
    # 1) Measurement keyword hits
    # -------------------------
    for kw in MEASUREMENT_KEYWORDS:
        if _contains_phrase(combined, kw):
            raw += 1.2
            reasons.append(f"+measurement:{kw}")

    # Special: if it literally mentions mg/100g or g/100g, boost harder
    if "mg/100g" in combined or "g/100g" in combined:
        raw += 2.5
        reasons.append("+units:mg/100g_or_g/100g")

    # -------------------------
    # 2) Penalize low-value / review-ish papers
    # -------------------------
    for kw in LOW_VALUE_KEYWORDS:
        if _contains_phrase(title, kw) or _contains_phrase(abstract, kw):
            raw -= 1.8
            reasons.append(f"-low_value:{kw}")

    # -------------------------
    # 3) Penalize obvious off-domain junk
    # -------------------------
    for kw in OFF_DOMAIN_KEYWORDS:
        if _contains_phrase(title, kw) or _contains_phrase(abstract, kw):
            raw -= 3.0
            reasons.append(f"-off_domain:{kw}")

    # -------------------------
    # 4) Query overlap signal (very important)
    # -------------------------
    # We want papers that actually mention the food or amino acid in text.
    q_tokens = set(_tokens(q))
    text_tokens = set(_tokens(combined))

    if q_tokens:
        overlap = len(q_tokens.intersection(text_tokens))
        # scale overlap: 0, 1, 2, 3+
        raw += min(3, overlap) * 0.9
        reasons.append(f"+query_overlap:{overlap}")

    # -------------------------
    # 5) Provider prior
    # -------------------------
    # PubMed tends to be more nutrition/biomed relevant than Crossref.
    if hit.provider == "pubmed":
        raw += 1.0
        reasons.append("+provider:pubmed_prior")
    elif hit.provider == "crossref":
        raw += 0.2
        reasons.append("+provider:crossref_prior")

    # -------------------------
    # 6) Normalize to [0, 1]
    # -------------------------
    # This is a simple squashing function:
    # - raw around 0 => ~0.5
    # - raw negative => closer to 0
    # - raw positive => closer to 1
    score = 1.0 / (1.0 + pow(2.718281828, -raw / 2.5))  # logistic-ish

    # Clamp (just in case)
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0

    return ScoredHit(hit=hit, score=float(score), reasons=reasons)


def rank_hits(hits: Iterable[PaperHit], query: str, top_n: int = 10) -> List[ScoredHit]:
    """Score all hits then return the top_n by score desc."""
    scored = [score_hit(h, query) for h in hits]
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:top_n]
