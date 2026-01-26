# etl/ingest_csv.py
# ---------------------------------------------------------
# ETL SCRIPT (Extract -> Transform -> Load)
#
# GOAL:
# - Read amino-acid data from a CSV file
# - Insert / update the database tables:
#     1) sources
#     2) foods
#     3) food_amino_acids
#
# WHY WE START WITH CSV:
# - Easy to test locally
# - Same "shape" as future USDA/INFOODS ingestion
# - Lets you verify the DB + API end-to-end before adding complexity
#
# WHAT THIS SCRIPT DOES STEP-BY-STEP:
# 1) Open the CSV
# 2) For each row:
#    - find or create the Source
#    - find or create the Food
#    - insert or update the FoodAminoAcid value
# 3) Commit at the end
#
# SAFE TO RUN MULTIPLE TIMES:
# - If the row already exists, we update it (UPSERT behavior)
# ---------------------------------------------------------

import csv
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

# We import DB session factory + Base/engine so we can ensure tables exist
from app.db import SessionLocal, Base, engine

# We import ORM models (tables)
from app.models import Source, Food, FoodAminoAcid


# ---------------------------------------------------------
# IMPORTANT: Ensure tables exist (DEV/LEARNING mode)
# ---------------------------------------------------------
# In production we will use Alembic migrations.
# For now, this ensures the DB has tables before inserting.
# ---------------------------------------------------------
Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------
# Helper 1: Get or create Source row
# ---------------------------------------------------------
# A "Source" is where the data came from (dataset or publication).
#
# We try to find an existing source by:
# - source_name
# - source_url
# - version
#
# If it exists, reuse it.
# If not, insert a new one.
# ---------------------------------------------------------
def get_or_create_source(db: Session, row: dict) -> Source:
    source_type = (row.get("source_type") or "dataset").strip()
    source_name = (row.get("source_name") or "").strip()
    source_url = (row.get("source_url") or "").strip()
    citation_text = (row.get("citation_text") or "").strip()
    version = (row.get("version") or "").strip() or None

    # Build a query to find an existing source that matches
    stmt = select(Source).where(
        Source.source_name == source_name,
        Source.source_url == source_url,
        Source.version == version,
    )

    existing = db.execute(stmt).scalars().first()
    if existing:
        return existing

    # Create a new Source row
    src = Source(
        source_type=source_type,
        source_name=source_name,
        source_url=source_url,
        citation_text=citation_text,
        version=version,
    )

    # Add to the session (not committed yet)
    db.add(src)

    # Flush sends the INSERT to the DB immediately so src.id becomes available
    # (but still not committed until we call db.commit()).
    db.flush()

    return src


# ---------------------------------------------------------
# Helper 2: Get or create Food row
# ---------------------------------------------------------
# A "Food" is the searchable item in your app.
#
# We identify a food uniquely by:
# - external_source (ex: "USDA", "INFOODS")
# - external_food_id (ex: USDA foodId)
#
# Why not just food name?
# - names can change
# - duplicates exist
# - IDs are stable
# ---------------------------------------------------------
def get_or_create_food(db: Session, row: dict) -> Food:
    name = (row.get("name") or "").strip()
    external_source = (row.get("external_source") or "manual").strip()
    external_food_id = (row.get("external_food_id") or "").strip()

    stmt = select(Food).where(
        Food.external_source == external_source,
        Food.external_food_id == external_food_id,
    )

    existing = db.execute(stmt).scalars().first()
    if existing:
        # Optional improvement: update name if it changed
        if name and existing.name != name:
            existing.name = name
        return existing

    food = Food(
        name=name,
        external_source=external_source,
        external_food_id=external_food_id,
    )

    db.add(food)
    db.flush()  # ensures food.id is available
    return food


# ---------------------------------------------------------
# Helper 3: Upsert FoodAminoAcid row
# ---------------------------------------------------------
# FoodAminoAcid is the "fact table" containing:
# - food_id
# - amino_acid (lysine, leucine, etc.)
# - amount_mg_per_100g
# - source_id
# - confidence
#
# Upsert behavior:
# - If (food_id, amino_acid) exists -> UPDATE it
# - Else -> INSERT it
# ---------------------------------------------------------
def upsert_food_amino_acid(db: Session, food: Food, source: Source, row: dict) -> None:
    amino_acid = (row.get("amino_acid") or "").strip().lower()
    amount_str = (row.get("amount_mg_per_100g") or "").strip()
    confidence_str = (row.get("confidence") or "").strip()

    # Convert amount to float (will raise if CSV is invalid)
    amount = float(amount_str)

    # Default confidence to 1.0 if missing
    confidence = float(confidence_str) if confidence_str else 1.0

    # Find existing amino acid row for this food + amino acid name
    stmt = select(FoodAminoAcid).where(
        FoodAminoAcid.food_id == food.id,
        FoodAminoAcid.amino_acid == amino_acid,
    )

    existing = db.execute(stmt).scalars().first()

    if existing:
        # UPDATE existing row
        existing.amount_mg_per_100g = amount
        existing.source_id = source.id
        existing.confidence = confidence
        existing.units = "mg/100g"
        return

    # INSERT new row
    aa = FoodAminoAcid(
        food_id=food.id,
        amino_acid=amino_acid,
        amount_mg_per_100g=amount,
        units="mg/100g",
        confidence=confidence,
        source_id=source.id,
    )

    db.add(aa)


# ---------------------------------------------------------
# Main ETL function
# ---------------------------------------------------------
def run(csv_path: str) -> None:
    """
    Run the CSV ingestion pipeline.

    INPUT:
      - csv_path: path to a CSV file that contains amino acid rows

    EXPECTED CSV HEADERS (columns):
      name, external_source, external_food_id,
      amino_acid, amount_mg_per_100g,
      source_type, source_name, source_url, citation_text, version,
      confidence

    NOTE:
      - You can start with a tiny CSV (2 foods) to prove everything works.
      - Later, the same insertion logic will be used with USDA/INFOODS ingestion.
    """

    # Open a DB session for the entire ETL run
    # (One session is fine for this small pipeline.)
    with SessionLocal() as db:
        # Open the CSV file
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # DictReader reads each row as a dict:
            # row["name"], row["amino_acid"], etc.
            for row in reader:
                # 1) ensure Source exists
                src = get_or_create_source(db, row)

                # 2) ensure Food exists
                food = get_or_create_food(db, row)

                # 3) upsert amino acid value
                upsert_food_amino_acid(db, food, src, row)

        # Commit once at the end for performance and atomicity
        db.commit()

    print(f"✅ Ingest complete from: {csv_path}")


# ---------------------------------------------------------
# Allow running as a script
# ---------------------------------------------------------
if __name__ == "__main__":
    # Default CSV path if not provided
    # You can override by setting environment variable CSV_PATH
    csv_path = os.getenv("CSV_PATH", "app/etl/sample_amino.csv")
    run(csv_path)
