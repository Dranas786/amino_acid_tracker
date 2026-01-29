from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from app.db import SessionLocal

# ----------------------------
# Normalization helpers
# ----------------------------
_NONWORD_RE = re.compile(r"[^a-z0-9\s\-']+")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

# If the query is ONLY these words (or mostly these), treat as junk
JUNK_WORDS = {"help", "hi", "hello", "test", "asdf", "lol", "pls", "please"}

# Threshold tuned from your DB test:
# "red peepper" matched "Peppers, sweet, red, raw" at sim=0.375
# So 0.30 catches it and still rejects obvious wrong matches.
DB_TRGM_THRESHOLD = 0.30


def _normalize(s: str) -> str:
    """
    Lowercase + strip + remove punctuation (keep letters/numbers/spaces/hyphen/apostrophe)
    + collapse whitespace.
    """
    t = (s or "").strip().lower()
    t = _NONWORD_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


@dataclass
class NlpResult:
    label: str    # "food" | "junk"
    score: float  # 0..1 (here: best trigram similarity)
    reason: str   # explanation string for debugging/audits


def _best_food_similarity_db(norm: str) -> tuple[float, str | None]:
    """
    Uses pg_trgm similarity against foods.name.

    Requirements:
      - CREATE EXTENSION pg_trgm;
      - trigram indexes (optional but recommended)

    Returns:
      (best_similarity, best_food_name)
    """
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                select name, similarity(lower(name), :q) as sim
                from foods
                where lower(name) % :q
                order by sim desc
                limit 1
                """
            ),
            {"q": norm},
        ).first()

    if not row:
        return 0.0, None

    # SQLAlchemy row supports attribute access by column label
    best_name = row.name
    best_sim = float(row.sim or 0.0)
    return best_sim, best_name


def classify_query(q: str) -> NlpResult:
    norm = _normalize(q)

    # ----------------------------
    # Hard rejects (cheap + safe)
    # ----------------------------
    if not norm:
        return NlpResult("junk", 0.0, "empty")
    if len(norm) < 3:
        return NlpResult("junk", 0.0, "too_short")
    if norm.isdigit():
        return NlpResult("junk", 0.0, "digits_only")
    if _URL_RE.search(norm):
        return NlpResult("junk", 0.0, "url")

    tokens = norm.split()

    # Example: "help" / "pls" / "hello" etc.
    # We only apply this rule for short queries so we don't reject "help me find lentils"
    if len(tokens) <= 2 and all(t in JUNK_WORDS for t in tokens):
        return NlpResult("junk", 0.05, "junk_word_only")

    # ----------------------------
    # DB trigram classification
    # ----------------------------
    best_sim, best_name = _best_food_similarity_db(norm)

    if best_sim >= DB_TRGM_THRESHOLD:
        # score is similarity; reason includes best match for debugging
        return NlpResult("food", best_sim, f"db_trgm>={DB_TRGM_THRESHOLD} match={best_name}")

    return NlpResult("junk", best_sim, f"db_trgm<{DB_TRGM_THRESHOLD} best_match={best_name}")
