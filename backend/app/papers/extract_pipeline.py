# app/papers/extract_pipeline.py
# ---------------------------------------------------------
# Paper extraction pipeline
#
# Responsibilities:
# - Orchestrate full-text fetch
# - Run table extraction
# - Write amino-acid values to DB with provenance
# - Update failed_search status
#
# This file GLUES together:
#   extractor.py
#   table_extractor.py
#   crud.py
# ---------------------------------------------------------

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db import SessionLocal
from app import models
from app.crud import (
    get_or_create_publication_source,
    upsert_food_amino_acid,
)
from app.failed_searches import normalize_query
from app.papers.extractor import fetch_fulltext_offline_first
from app.papers.table_extractor import extract_amino_tables_from_pmc_xml


# -----------------------------------------
# Status constants
# -----------------------------------------

FAILED_STATUS_CANDIDATES_DONE = "candidates_done"
FAILED_STATUS_RESOLVED = "resolved"
FAILED_STATUS_NEEDS_REVIEW = "needs_review"


# -----------------------------------------
# Helper: choose a food for a failed search
# -----------------------------------------

def _best_food_match_for_query(db: Session, query: str) -> models.Food | None:
    """
    Very conservative food matcher:
    - exact normalized name match only
    - avoids accidental wrong associations

    This can be improved later with trigram similarity.
    """
    norm = normalize_query(query)

    stmt = select(models.Food).where(
        models.Food.name.ilike(norm)
    )

    return db.execute(stmt).scalar_one_or_none()


# -----------------------------------------
# Core processor
# -----------------------------------------

def process_one_failed_search(db: Session, fs: models.FailedSearch) -> None:
    """
    Process ONE failed search:
      - fetch paper text
      - extract amino acids
      - write to DB
      - update status
    """

    # Pick the top-ranked paper candidate
    candidate = db.execute(
        select(models.PaperCandidate)
        .where(models.PaperCandidate.failed_search_id == fs.id)
        .order_by(models.PaperCandidate.score.desc())
        .limit(1)
    ).scalar_one_or_none()

    if candidate is None:
        fs.status = FAILED_STATUS_NEEDS_REVIEW
        fs.note = "no paper candidates available"
        db.add(fs)
        return

    # 1) Fetch full text (offline-first) fetches online if offline not available
    doc = fetch_fulltext_offline_first(
        title=candidate.title,
        url=candidate.url or "",
        doi=candidate.doi,
    )

    if not doc.pmc_xml:
        fs.status = FAILED_STATUS_NEEDS_REVIEW
        fs.note = "; ".join(doc.warnings) or "no PMC XML"
        db.add(fs)
        return

    # 2) Extract amino-acid values
    extracted = extract_amino_tables_from_pmc_xml(doc.pmc_xml)

    if not extracted:
        fs.status = FAILED_STATUS_NEEDS_REVIEW
        fs.note = "no amino-acid tables found"
        db.add(fs)
        return

    # 3) Resolve food as failed search can be anything as it is user input
    food = _best_food_match_for_query(db, fs.query)
    if food is None:
        fs.status = FAILED_STATUS_NEEDS_REVIEW
        fs.note = "could not confidently match food"
        db.add(fs)
        return

    # 4) Create / reuse publication Source
    source = get_or_create_publication_source(
        db,
        source_name=doc.title,
        source_url=doc.source_url,
        citation_text=doc.title,
        version=doc.doi,
    )

    # 5) Write amino-acid values (confidence < 1)
    wrote_any = False
    for row in extracted:
        upsert_food_amino_acid(
            db,
            food_id=food.id,
            source_id=source.id,
            amino_acid=row["amino_acid"],
            amount_mg_per_100g=row["amount_mg_per_100g"],
            confidence=0.7,  # conservative default for paper extraction
        )
        wrote_any = True

    if not wrote_any:
        fs.status = FAILED_STATUS_NEEDS_REVIEW
        fs.note = "extraction yielded no valid rows"
        db.add(fs)
        return

    # 6) Update coverage flags (simple recompute)
    food.essential_aa_present_count = len(food.amino_acids)
    food.amino_data_incomplete = food.essential_aa_present_count < food.essential_aa_total

    # 7) Mark success
    fs.status = FAILED_STATUS_RESOLVED
    fs.note = f"extracted from paper candidate {candidate.id}"

    db.add(food)
    db.add(fs)


# -----------------------------------------
# Batch runner
# -----------------------------------------

def run(limit: int = 10) -> None:
    """
    Process a batch of failed searches that reached candidate stage.
    """

    db = SessionLocal()
    try:
        failed = db.execute(
            select(models.FailedSearch)
            .where(models.FailedSearch.status == FAILED_STATUS_CANDIDATES_DONE)
            .order_by(models.FailedSearch.last_seen_at.asc())
            .limit(limit)
        ).scalars().all()

        for fs in failed:
            process_one_failed_search(db, fs)
            db.commit()

    finally:
        db.close()


# -----------------------------------------
# CLI entrypoint
# -----------------------------------------

if __name__ == "__main__":
    run()
