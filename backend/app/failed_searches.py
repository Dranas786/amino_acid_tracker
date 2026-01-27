from __future__ import annotations

import re
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import FailedSearch


_space_re = re.compile(r"\s+")


def normalize_query(q: str) -> str:
    q = (q or "").strip().lower()
    q = _space_re.sub(" ", q)
    return q


def log_failed_search(db: Session, query: str) -> FailedSearch | None:
    """
    Upsert-like behavior:
    - If normalized query already exists: increment seen_count + update last_seen_at
    - Else insert a new row with status='new'
    """
    norm = normalize_query(query)
    if len(norm) < 2:
        return None

    stmt = select(FailedSearch).where(FailedSearch.normalized_query == norm)
    existing = db.execute(stmt).scalars().first()

    if existing:
        existing.seen_count += 1
        existing.last_seen_at = func.now()
        return existing

    row = FailedSearch(query=query.strip(), normalized_query=norm, seen_count=1, status="new")
    db.add(row)
    db.flush()
    return row
