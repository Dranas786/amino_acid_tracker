# app/crud.py
# ---------------------------------------------------------
# CRUD = Create, Read, Update, Delete
#
# This file contains functions that TALK TO THE DATABASE.
# These functions do NOT contain "business logic" like:
# - summing amino acids across multiple foods
# - recommending foods
#
# Instead, CRUD functions are for:
# - searching foods in the DB
# - fetching amino acids for a specific food
#
# Think of it like this:
# - models.py  -> defines what tables look like
# - crud.py    -> reads/writes rows in those tables
# - logic.py   -> uses crud results to compute higher-level outputs
# ---------------------------------------------------------

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Food, FoodAminoAcid, Source
from app.schemas import FoodOut, FoodAminoOut, SourceOut
from typing import Optional

def search_foods(db: Session, query: str, limit: int = 20) -> list[FoodOut]:
    """
    Search foods by name.

    INPUT:
      - db: a SQLAlchemy Session (a connection "conversation" with the database)
      - query: the user’s search text, like "chicken"
      - limit: max results to return

    OUTPUT:
      - A list of FoodOut schemas (safe JSON-ready objects)
        that the frontend can display in search results.

    WHAT THIS FUNCTION DOES STEP-BY-STEP:
      1) Builds a SQL query:
         SELECT * FROM foods WHERE name ILIKE '%query%' LIMIT 20;
      2) Executes the query using SQLAlchemy
      3) Converts database rows (ORM objects) into FoodOut schemas
         so FastAPI can return JSON
    """

    # Build a SQL query using SQLAlchemy's "select" syntax.
    # Food.name.ilike("%...%") means:
    # - case-insensitive match (ILIKE in Postgres)
    # - find foods where the name contains the query text anywhere
    stmt = (
        select(Food)
        .where(Food.name.ilike(f"%{query}%"))
        .limit(limit)
    )

    # Execute the query against the database.
    # db.execute(stmt) returns a Result object.
    # .scalars() extracts the first column of each row (here it's Food objects).
    # .all() gives us a Python list.
    foods: list[Food] = db.execute(stmt).scalars().all()

    # Convert ORM objects -> Pydantic schemas.
    # We do this conversion so:
    # - we control exactly what fields we return
    # - the frontend gets consistent JSON
    # - we do NOT accidentally leak DB internals
    return [
        FoodOut.model_validate(f)  # model_validate uses from_attributes=True in FoodOut
        for f in foods
    ]


def get_food_amino(db: Session, food_id: int) -> FoodAminoOut:
    """
    Get amino acid values (mg/100g) + provenance links for ONE food.

    INPUT:
      - db: database session
      - food_id: internal DB id (foods.id)

    OUTPUT:
      - FoodAminoOut schema containing:
        * food info
        * amino acids dictionary {amino_acid -> mg/100g}
        * sources dictionary {amino_acid -> provenance info}

    WHY THIS IS IMPORTANT:
      The frontend needs BOTH:
      - the numeric values (for bars / totals)
      - the source link and citation (for trust / info buttons)

    WHAT THIS DOES STEP-BY-STEP:
      1) Fetch the food row from foods table
      2) Fetch all amino acid rows for that food
      3) Join each amino acid row to its Source row
      4) Build two dictionaries:
         - amino_acids_mg_per_100g
         - sources (per amino acid)
      5) Return FoodAminoOut (JSON-ready)
    """

    # ---------------------------
    # 1) Fetch the Food record
    # ---------------------------
    food: Food | None = db.get(Food, food_id)

    # If the food doesn’t exist, that means:
    # - the frontend sent a wrong id
    # - or the DB has no such record
    if food is None:
        # We raise ValueError for now.
        # Later, in routes.py, we'll convert this into a proper HTTP 404.
        raise ValueError(f"Food with id={food_id} not found")

    # -----------------------------------------------
    # 2) Fetch amino acids for this food + join source
    # -----------------------------------------------
    # We want rows that contain BOTH:
    # - FoodAminoAcid (values)
    # - Source (where the value came from)
    #
    # That means we do a JOIN:
    # SELECT ...
    # FROM food_amino_acids
    # JOIN sources ON food_amino_acids.source_id = sources.id
    # WHERE food_amino_acids.food_id = :food_id
    stmt = (
        select(FoodAminoAcid, Source)
        .join(Source, FoodAminoAcid.source_id == Source.id)
        .where(FoodAminoAcid.food_id == food_id)
    )

    # Execute the join query.
    # Each row in "rows" will look like:
    # (FoodAminoAcid_object, Source_object)
    rows = db.execute(stmt).all()

    # --------------------------------------
    # 3) Convert DB rows to dictionaries
    # --------------------------------------
    # We'll build:
    # amino_acids_mg_per_100g = { "lysine": 2600, ... }
    # sources = { "lysine": SourceOut(...), ... }
    amino_acids_mg_per_100g: dict[str, float] = {}
    sources: dict[str, SourceOut] = {}

    for aa_row, source_row in rows:
        # Example:
        # aa_row.amino_acid might be "lysine"
        # aa_row.amount_mg_per_100g might be 2600.0
        aa_name = aa_row.amino_acid

        # Store the numeric value in the amino acid dictionary
        amino_acids_mg_per_100g[aa_name] = float(aa_row.amount_mg_per_100g)

        # Convert Source ORM object -> SourceOut schema
        # IMPORTANT:
        # - SourceOut has from_attributes=True, so model_validate(source_row) works
        # - We also attach the amino-row confidence, because confidence is per value
        sources[aa_name] = SourceOut(
            source_type=source_row.source_type,
            source_name=source_row.source_name,
            source_url=source_row.source_url,
            citation_text=source_row.citation_text,
            version=source_row.version,
            confidence=float(aa_row.confidence),
        )

    # --------------------------------------
    # 4) Return the final response schema
    # --------------------------------------
    # We return FoodAminoOut, which includes:
    # - the food (FoodOut)
    # - amino acids values dictionary
    # - per-amino-acid sources dictionary
    return FoodAminoOut(
        food=FoodOut.model_validate(food),
        amino_acids_mg_per_100g=amino_acids_mg_per_100g,
        sources=sources,
    )


def get_or_create_publication_source(
    db: Session,
    *,
    source_name: str,
    source_url: str,
    citation_text: str,
    version: Optional[str] = None,
) -> Source:
    """
    Create (or reuse) a Source row for a publication (paper).

    WHY:
      - Paper extraction will insert amino acid values from papers.
      - We want provenance: where each number came from.
      - DOI can be missing, so we dedupe using (source_type, source_url).

    DEDUPE RULE:
      If a publication Source already exists with the same URL,
      reuse it instead of inserting duplicates.
    """

    existing: Source | None = db.execute(
        select(Source).where(
            Source.source_type == "publication",
            Source.source_url == source_url,
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Optional: backfill missing fields if the row was created with less info.
        changed = False

        if source_name and not existing.source_name:
            existing.source_name = source_name
            changed = True

        if citation_text and not existing.citation_text:
            existing.citation_text = citation_text
            changed = True

        if version and not existing.version:
            existing.version = version
            changed = True

        if changed:
            db.add(existing)

        return existing

    # Create new source row
    src = Source(
        source_type="publication",
        source_name=(source_name or "Unknown publication")[:200],
        source_url=(source_url or "")[:500],
        citation_text=(citation_text or "")[:500],
        version=version,
    )
    db.add(src)
    db.flush()  # flush so src.id is available immediately
    return src


def upsert_food_amino_acid(
    db: Session,
    *,
    food_id: int,
    source_id: int,
    amino_acid: str,
    amount_mg_per_100g: float,
    confidence: float = 1.0,
    units: str = "mg/100g",
) -> FoodAminoAcid:
    """
    Upsert ONE amino acid row for ONE food.

    UNIQUE KEY:
      (food_id, amino_acid)

    UPDATE BEHAVIOR:
      - Always overwrite the numeric amount
      - Always set the source_id to the newest write
      - Confidence keeps the *higher* value:
          USDA (1.0) should beat paper extraction (e.g. 0.7)
    """

    aa_key = amino_acid.strip().lower()

    existing: FoodAminoAcid | None = db.execute(
        select(FoodAminoAcid).where(
            FoodAminoAcid.food_id == food_id,
            FoodAminoAcid.amino_acid == aa_key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.amount_mg_per_100g = float(amount_mg_per_100g)
        existing.units = units
        existing.source_id = source_id

        # Keep the best confidence (higher wins)
        existing.confidence = float(max(existing.confidence or 0.0, confidence))

        db.add(existing)
        return existing

    row = FoodAminoAcid(
        food_id=food_id,
        amino_acid=aa_key,
        amount_mg_per_100g=float(amount_mg_per_100g),
        units=units,
        confidence=float(confidence),
        source_id=source_id,
    )
    db.add(row)
    db.flush()
    return row
