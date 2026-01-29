# app/papers/pipeline.py
# ------------------------------------------------------------
# Step 4: failed_searches -> paper_candidates
#
# Assumptions:
# - Your junk filter already triages failed_searches:
#     status = "queued"   => real food-like query, ready for paper search
#     status = "rejected" => junk
#
# This pipeline:
# 1) pulls queued failed_searches
# 2) searches Crossref + PubMed
# 3) ranks using heuristic scoring (rank_hits)
# 4) upserts paper_candidates (best-effort de-dupe)
# 5) marks failed_searches as "candidates_done" so we don't reprocess forever
#
# Next stage (later):
# - a "paper reader/extractor" job reads from paper_candidates (status-based)
# ------------------------------------------------------------

from __future__ import annotations

from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import FailedSearch, PaperCandidate
from app.papers.providers import crossref_search, pubmed_search
from app.papers.ranker import rank_hits


FAILED_READY_STATUS = "queued"
FAILED_DONE_STATUS = "candidates_done"


def _candidate_exists_for_failed_search(
    db: Session,
    failed_search_id: int,
    provider: str,
    doi: str | None,
    url: str | None,
    title: str,
    year: int | None,
) -> bool:
    """
    Your DB uniqueness is (failed_search_id, provider, doi).
    BUT doi can be NULL, and NULLs don't dedupe in Postgres unique constraints.
    So we do best-effort checks:
      - if DOI exists: check exact (failed_search_id, provider, doi)
      - else: check (failed_search_id, provider, url) if url exists
      - else: check (failed_search_id, provider, title, year) fallback
    """
    if doi:
        row = db.execute(
            select(PaperCandidate.id).where(
                PaperCandidate.failed_search_id == failed_search_id,
                PaperCandidate.provider == provider,
                PaperCandidate.doi == doi,
            )
        ).first()
        return row is not None

    if url:
        row = db.execute(
            select(PaperCandidate.id).where(
                PaperCandidate.failed_search_id == failed_search_id,
                PaperCandidate.provider == provider,
                PaperCandidate.url == url,
            )
        ).first()
        return row is not None

    row = db.execute(
        select(PaperCandidate.id).where(
            PaperCandidate.failed_search_id == failed_search_id,
            PaperCandidate.provider == provider,
            PaperCandidate.title == title,
            PaperCandidate.published_year == year,
        )
    ).first()
    return row is not None


def enqueue_candidates_for_failed_search(db: Session, fs: FailedSearch, top_n: int = 10) -> int:
    """
    Generates paper_candidates for a single failed_search query.
    Returns inserted count.
    """
    query = fs.query

    # 1) Fetch raw hits from providers
    hits = crossref_search(query) + pubmed_search(query)

    # 2) Rank with heuristic scorer (Step 3)
    scored = rank_hits(hits, query, top_n=top_n)

    inserted = 0

    for s in scored:
        provider = s.hit.provider

        # Apply your column size constraints / normalization
        title = (s.hit.title or "").strip()
        if not title:
            continue
        if len(title) > 500:
            title = title[:500]

        doi = (s.hit.doi or "").strip() or None
        url = (s.hit.url or "").strip() or None
        year = s.hit.published_year
        authors = s.hit.authors
        abstract = s.hit.abstract
        raw_score = s.hit.raw_score

        # 3) Dedupe best-effort
        if _candidate_exists_for_failed_search(
            db=db,
            failed_search_id=fs.id,
            provider=provider,
            doi=doi,
            url=url,
            title=title,
            year=year,
        ):
            continue

        # 4) Insert candidate
        db.add(
            PaperCandidate(
                failed_search_id=fs.id,
                provider=provider,
                title=title,
                doi=doi,
                url=url,
                published_year=year,
                authors=authors,
                abstract=abstract,
                score=float(s.score),
                raw_score=(float(raw_score) if raw_score is not None else None),
            )
        )
        inserted += 1

    return inserted


def run_once(limit_failed: int = 25, top_n_per_query: int = 10) -> None:
    """
    Batch runner:
    - reads failed_searches with status='queued'
    - creates paper_candidates
    - marks failed_searches as 'candidates_done'
    """
    with SessionLocal() as db:
        failed = db.execute(
            select(FailedSearch)
            .where(FailedSearch.status == FAILED_READY_STATUS)
            .order_by(FailedSearch.last_seen_at.desc())
            .limit(limit_failed)
        ).scalars().all()

        total_inserted = 0

        for fs in failed:
            inserted = enqueue_candidates_for_failed_search(db, fs, top_n=top_n_per_query)
            total_inserted += inserted

            # Mark "done generating candidates" to avoid reprocessing forever
            fs.status = FAILED_DONE_STATUS

        db.commit()

    print(
        f"✅ Step 4 complete: {len(failed)} failed_searches processed "
        f"({FAILED_READY_STATUS} -> {FAILED_DONE_STATUS}), "
        f"{total_inserted} paper_candidates inserted."
    )


if __name__ == "__main__":
    run_once()
