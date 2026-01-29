# app/process_failed_searches.py
# ------------------------------------------------------------
# This script is meant to be run by cron (or manually).
#
# Goal:
#   Take "new" failed searches (user typed something that returned 0 foods),
#   classify them as:
#     - food-like  -> status = "queued"   (eligible for paper search pipeline)
#     - junk       -> status = "rejected" (ignore)
#
# Classifier used:
#   app.nlp_food_filter.classify_query() (DB trigram similarity via pg_trgm)
#
# Notes:
#   - We ONLY process status="new" so we don't re-label already triaged rows.
#   - "queued" is your "good" bucket (as you wanted).
#   - We store label/score/reason for auditing and debugging.
# ------------------------------------------------------------

from __future__ import annotations

from sqlalchemy import select
from app.db import SessionLocal
from app.models import FailedSearch
from app.nlp_food_filter import classify_query


def run(limit: int = 500) -> None:
    """
    Process up to `limit` rows from failed_searches where status="new".

    For each row:
      1) classify the raw query with classify_query()
      2) store model outputs:
           - nlp_label: "food" | "junk"
           - nlp_score: similarity score (0..1-ish)
           - note: explanation string (includes best DB match)
      3) update status:
           - "queued" if label == "food"
           - "rejected" otherwise
      4) commit once at the end (faster + atomic)
    """
    with SessionLocal() as db:
        # Pull the most important "new" items first:
        # - higher seen_count first (more common user pain)
        # - then most recent
        stmt = (
            select(FailedSearch)
            .where(FailedSearch.status == "new")
            .order_by(FailedSearch.seen_count.desc(), FailedSearch.last_seen_at.desc())
            .limit(limit)
        )

        # scalars().all() gives us a list[FailedSearch]
        rows: list[FailedSearch] = db.execute(stmt).scalars().all()

        if not rows:
            print("No new failed searches.")
            return

        queued_cnt = 0
        rejected_cnt = 0

        for r in rows:
            # Run the classifier on the raw user query (not normalized)
            res = classify_query(r.query)

            # Persist classifier outputs for transparency/debugging
            r.nlp_label = res.label
            r.nlp_score = float(res.score)

            # Your chosen workflow:
            # - queued = food-like -> later paper lookup creates PaperCandidates
            # - rejected = junk -> ignore
            if res.label == "food":
                r.status = "queued"
                queued_cnt += 1
            else:
                r.status = "rejected"
                rejected_cnt += 1

            # Store the "why" (includes trigram threshold + best match)
            # Example: "db_trgm>=0.30 match=Peppers, sweet, red, raw"
            r.note = f"NLP: {res.reason}"

        # One commit for all updates (faster + all-or-nothing)
        db.commit()

    print(
        f"Processed {len(rows)} failed searches. "
        f"queued={queued_cnt}, rejected={rejected_cnt}"
    )


if __name__ == "__main__":
    run()
