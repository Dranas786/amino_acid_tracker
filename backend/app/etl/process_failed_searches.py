# script ran by cron (runs the basic nlp script)

from __future__ import annotations

from sqlalchemy import select
from app.db import SessionLocal
from app.models import FailedSearch
from app.nlp_food_filter import classify_query


def run(limit: int = 500) -> None:
    """
    Processes unresolved failed searches:
    - classify query as food/junk
    - mark status accordingly:
        - "queued" for food-like (future: paper search / enrichment)
        - "rejected" for junk
    """
    with SessionLocal() as db:
        stmt = (
            select(FailedSearch)
            .where(FailedSearch.status.in_(["new"]))
            .order_by(FailedSearch.seen_count.desc(), FailedSearch.last_seen_at.desc())
            .limit(limit)
        )
        rows = db.execute(stmt).scalars().all()

        if not rows:
            print("No new failed searches.")
            return

        food_cnt = 0
        junk_cnt = 0

        for r in rows:
            res = classify_query(r.query)
            r.nlp_label = res.label
            r.nlp_score = res.score

            if res.label == "food":
                r.status = "queued"
                r.note = f"NLP: {res.reason}"
                food_cnt += 1
            else:
                r.status = "rejected"
                r.note = f"NLP: {res.reason}"
                junk_cnt += 1

        db.commit()

    print(f"Processed {len(rows)} failed searches. queued(food-like)={food_cnt}, rejected={junk_cnt}")


if __name__ == "__main__":
    run()
