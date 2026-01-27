# app/etl/ingest_usda_bulk.py
# ---------------------------------------------------------
# USDA BULK INGEST (Weekly job)
#
# Goal:
# - Ingest *bulk* USDA FoodData Central data into Postgres
# - Keep ANY food that has at least 1 essential amino acid reported
# - Track "coverage" per food so the UI can warn about incomplete data
#
# Important meaning:
# - If an amino acid is missing from USDA for a food, that usually means
#   "not measured / not reported" (unknown), NOT "0".
#
# What this script does:
# 1) Download & unzip USDA dataset ZIP (CSV distribution)
# 2) Read nutrient.csv -> build a map of nutrient_id -> essential amino acid name + unit
# 3) Read food_nutrient.csv -> for rows that match essential amino acids, accumulate amounts per food
# 4) Keep ALL foods that have >= 1 essential AA row in food_nutrient.csv
# 5) Read food.csv -> get names/data_type for kept foods
# 6) Upsert into DB:
#    - sources: one row representing this USDA dataset run
#    - foods: one row per kept food (external_source="USDA", external_food_id=<fdc_id>)
#             plus coverage fields:
#             * essential_aa_present_count (0..9)
#             * essential_aa_total (always 9)
#             * amino_data_incomplete (True if present < total)
#    - food_amino_acids: amino acid values (mg/100g)
#
# How you run it (inside Docker container):
#   python -m app.etl.ingest_usda_bulk
# ---------------------------------------------------------

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Set

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Source, Food, FoodAminoAcid
from app.schemas import ESSENTIAL_AMINO_ACIDS

from app.etl.usda_bulk import fetch_and_extract_usda_zip, guess_dataset_root


# ---------------------------------------------------------
# Config via environment variables
# ---------------------------------------------------------
# REQUIRED:
# - USDA_ZIP_URL: direct link to the USDA bulk dataset ZIP
#
# OPTIONAL:
# - USDA_WORK_DIR: where to store downloaded/extracted files (default: data/usda)
# - USDA_ZIP_NAME: filename used for zip (default: usda_fdc.zip)
# - USDA_SOURCE_NAME: name stored in sources table
# - USDA_SOURCE_URL: link stored in sources table (can be the same as download page)
# - USDA_VERSION: e.g., "2026-01-01" or dataset release label
# ---------------------------------------------------------

DEFAULT_SOURCE_NAME = os.getenv("USDA_SOURCE_NAME", "USDA FoodData Central (Bulk)")
DEFAULT_SOURCE_URL = os.getenv("USDA_SOURCE_URL", "https://fdc.nal.usda.gov/download-datasets.html")
DEFAULT_VERSION = os.getenv("USDA_VERSION", "") or None

USDA_WORK_DIR = os.getenv("USDA_WORK_DIR", "data/usda")
USDA_ZIP_NAME = os.getenv("USDA_ZIP_NAME", "usda_fdc.zip")


# ---------------------------------------------------------
# USDA CSV filenames (common in the CSV distribution)
# ---------------------------------------------------------
FOOD_CSV_NAME = "food.csv"
NUTRIENT_CSV_NAME = "nutrient.csv"
FOOD_NUTRIENT_CSV_NAME = "food_nutrient.csv"


# ---------------------------------------------------------
# Data structures used during parsing
# ---------------------------------------------------------

# Maps nutrient_id -> (amino_acid_name, unit_name)
# Example: 1234 -> ("lysine", "MG")
NutrientMap = Dict[int, Tuple[str, str]]

# Maps fdc_id -> dict(amino_acid_name -> amount_mg_per_100g)
FoodAAMap = Dict[int, Dict[str, float]]


# ---------------------------------------------------------
# Helpers: locate USDA CSV files inside extracted dataset
# ---------------------------------------------------------

def _find_required_file(dataset_root: Path, filename: str) -> Path:
    """
    Find a required file under dataset_root.
    We use a recursive search because USDA sometimes wraps files in subfolders.

    If file not found, raise a clear error.
    """
    matches = list(dataset_root.rglob(filename))
    if not matches:
        raise FileNotFoundError(f"Could not find '{filename}' under {dataset_root}")
    return matches[0]

def refresh_coverage_flags(db: Session) -> None:
    """
    Recompute coverage flags from the actual amino rows in food_amino_acids.
    This prevents coverage drift across multiple ingests / sources.
    """
    db.execute(
        text(
            """
            with aa_counts as (
              select
                food_id,
                count(distinct lower(amino_acid)) as present
              from food_amino_acids
              where lower(amino_acid) in (
                'histidine','isoleucine','leucine','lysine','methionine',
                'phenylalanine','threonine','tryptophan','valine'
              )
              group by food_id
            )
            update foods f
            set
              essential_aa_present_count = a.present,
              essential_aa_total = 9,
              amino_data_incomplete = (a.present < 9)
            from aa_counts a
            where f.id = a.food_id;
            """
        )
    )

# ---------------------------------------------------------
# Step 1: Build nutrient_id -> essential amino acid mapping
# ---------------------------------------------------------

def load_nutrient_map(nutrient_csv_path: Path) -> NutrientMap:
    """
    Reads nutrient.csv and returns a mapping of nutrient_id -> essential amino acid info.
    """
    essential_set = set(ESSENTIAL_AMINO_ACIDS)
    nutrient_map: NutrientMap = {}

    with open(nutrient_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            nid_str = (row.get("id") or "").strip()
            name = (row.get("name") or "").strip().lower()
            unit = (row.get("unit_name") or "").strip().upper()

            if not nid_str:
                continue

            try:
                nid = int(nid_str)
            except ValueError:
                continue

            # Keep only essential amino acids.
            if name in essential_set:
                nutrient_map[nid] = (name, unit)

    return nutrient_map


# ---------------------------------------------------------
# Step 2: Read food_nutrient.csv and accumulate AA values per food
# ---------------------------------------------------------

def _to_mg(amount: float, unit: str) -> float:
    """
    Convert USDA nutrient amount to mg (if needed).
    We store everything in mg/100g in our DB.

    If USDA gives:
    - MG -> keep as-is
    - G  -> multiply by 1000
    """
    if unit == "MG":
        return amount
    if unit == "G":
        return amount * 1000.0
    return amount


def accumulate_food_amino_acids(food_nutrient_csv_path: Path, nutrient_map: NutrientMap) -> FoodAAMap:
    """
    Scan food_nutrient.csv and build:
      fdc_id -> { amino_acid_name -> amount_mg_per_100g }
    """
    per_food: FoodAAMap = {}

    with open(food_nutrient_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            fdc_id_str = (row.get("fdc_id") or "").strip()
            nutrient_id_str = (row.get("nutrient_id") or "").strip()
            amount_str = (row.get("amount") or "").strip()

            if not fdc_id_str or not nutrient_id_str or not amount_str:
                continue

            try:
                fdc_id = int(fdc_id_str)
                nutrient_id = int(nutrient_id_str)
                amount = float(amount_str)
            except ValueError:
                continue

            aa_info = nutrient_map.get(nutrient_id)
            if not aa_info:
                continue

            aa_name, unit = aa_info
            amount_mg = _to_mg(amount, unit)

            if fdc_id not in per_food:
                per_food[fdc_id] = {}

            per_food[fdc_id][aa_name] = amount_mg

    return per_food


# ---------------------------------------------------------
# Step 3: Read food.csv to get names/data_type for kept foods
# ---------------------------------------------------------

@dataclass
class FoodInfo:
    name: str
    data_type: str


def load_food_info(food_csv_path: Path, keep_ids: Set[int]) -> Dict[int, FoodInfo]:
    """
    Reads food.csv and returns only the rows we need (fdc_id in keep_ids).
    """
    out: Dict[int, FoodInfo] = {}

    with open(food_csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            fdc_id_str = (row.get("fdc_id") or "").strip()
            if not fdc_id_str:
                continue

            try:
                fdc_id = int(fdc_id_str)
            except ValueError:
                continue

            if fdc_id not in keep_ids:
                continue

            name = (row.get("description") or "").strip()
            data_type = (row.get("data_type") or "").strip()

            out[fdc_id] = FoodInfo(name=name, data_type=data_type)

    return out


# ---------------------------------------------------------
# DB helpers: get or create Source / Food / FoodAminoAcid (upsert)
# ---------------------------------------------------------

def get_or_create_usda_source(db: Session) -> Source:
    """
    Create one Source row for this USDA bulk dataset (or reuse if already exists).
    """
    stmt = select(Source).where(
        Source.source_name == DEFAULT_SOURCE_NAME,
        Source.source_url == DEFAULT_SOURCE_URL,
        Source.version == DEFAULT_VERSION,
    )
    existing = db.execute(stmt).scalars().first()
    if existing:
        return existing

    src = Source(
        source_type="dataset",
        source_name=DEFAULT_SOURCE_NAME,
        source_url=DEFAULT_SOURCE_URL,
        citation_text=DEFAULT_SOURCE_NAME,
        version=DEFAULT_VERSION,
    )
    db.add(src)
    db.flush()
    return src


def get_or_create_food(
    db: Session,
    fdc_id: int,
    name: str,
    present_count: int,
    total_count: int,
) -> Food:
    """
    Upsert a Food row for USDA item and store amino-acid coverage metadata.

    Coverage meaning:
    - present_count = how many essential amino acids have values for this food
    - total_count   = total essential amino acids we track (normally 9)
    - amino_data_incomplete = True if USDA does NOT provide full coverage
    """
    external_source = "USDA"
    external_food_id = str(fdc_id)

    incomplete = present_count < total_count

    stmt = select(Food).where(
        Food.external_source == external_source,
        Food.external_food_id == external_food_id,
    )
    existing = db.execute(stmt).scalars().first()

    if existing:
        if name and existing.name != name:
            existing.name = name

        # Always refresh coverage fields
        existing.essential_aa_present_count = present_count
        existing.essential_aa_total = total_count
        existing.amino_data_incomplete = incomplete

        return existing

    food = Food(
        name=name or f"USDA Food {fdc_id}",
        external_source=external_source,
        external_food_id=external_food_id,
        essential_aa_present_count=present_count,
        essential_aa_total=total_count,
        amino_data_incomplete=incomplete,
    )
    db.add(food)
    db.flush()
    return food


def upsert_food_amino_acid(
    db: Session,
    food_id: int,
    source_id: int,
    amino_acid: str,
    amount_mg_per_100g: float,
    confidence: float,
) -> None:
    """
    Upsert (food_id, amino_acid) row.
    """
    stmt = select(FoodAminoAcid).where(
        FoodAminoAcid.food_id == food_id,
        FoodAminoAcid.amino_acid == amino_acid,
    )
    existing = db.execute(stmt).scalars().first()

    if existing:
        existing.amount_mg_per_100g = amount_mg_per_100g
        existing.units = "mg/100g"
        existing.source_id = source_id
        existing.confidence = confidence
        return

    aa = FoodAminoAcid(
        food_id=food_id,
        amino_acid=amino_acid,
        amount_mg_per_100g=amount_mg_per_100g,
        units="mg/100g",
        confidence=confidence,
        source_id=source_id,
    )
    db.add(aa)


def confidence_for_data_type(data_type: str) -> float:
    """
    Simple confidence heuristic:
    - Foundation / SR Legacy are generally high quality
    - Branded can be noisier
    """
    dt = (data_type or "").lower()
    if "foundation" in dt or "sr legacy" in dt:
        return 1.0
    if "survey" in dt:
        return 0.9
    if "branded" in dt:
        return 0.8
    return 0.85


# ---------------------------------------------------------
# Main runner
# ---------------------------------------------------------

def run() -> None:
    """
    Main weekly job:
    - download -> unzip -> parse -> keep foods with >=1 AA -> upsert -> commit
    """
    dataset_zip_url = os.getenv("USDA_ZIP_URL")
    if not dataset_zip_url:
        raise RuntimeError("Missing env var USDA_ZIP_URL (direct link to USDA dataset zip).")

    # 1) Download + extract
    result = fetch_and_extract_usda_zip(
        dataset_zip_url=dataset_zip_url,
        work_dir=USDA_WORK_DIR,
        filename=USDA_ZIP_NAME,
    )

    dataset_root = guess_dataset_root(result.extracted_dir)

    # 2) Locate CSV files
    nutrient_csv = _find_required_file(dataset_root, NUTRIENT_CSV_NAME)
    food_nutrient_csv = _find_required_file(dataset_root, FOOD_NUTRIENT_CSV_NAME)
    food_csv = _find_required_file(dataset_root, FOOD_CSV_NAME)

    print(f"📄 Using dataset root: {dataset_root}")
    print(f"📄 nutrient.csv: {nutrient_csv}")
    print(f"📄 food_nutrient.csv: {food_nutrient_csv}")
    print(f"📄 food.csv: {food_csv}")

    # 3) Build nutrient_id -> essential amino acid mapping
    nutrient_map = load_nutrient_map(nutrient_csv)
    print(f"✅ Essential amino acids found in nutrient.csv: {len(nutrient_map)}")

    # 4) Accumulate amino acids per food (fdc_id)
    per_food = accumulate_food_amino_acids(food_nutrient_csv, nutrient_map)
    print(f"✅ Foods with at least 1 essential AA row: {len(per_food)}")

    # 5) Keep ALL foods that have >= 1 essential AA in USDA
    keep_ids = set(per_food.keys())
    print(f"✅ Foods kept (>= 1 essential AA): {len(keep_ids)}")

    # 6) Load names/data_type only for kept foods
    food_info = load_food_info(food_csv, keep_ids)
    print(f"✅ Loaded food info for kept foods: {len(food_info)}")

    # 7) Upsert into DB
    total_essential = len(ESSENTIAL_AMINO_ACIDS)

    with SessionLocal() as db:
        src = get_or_create_usda_source(db)

        inserted_foods = 0
        inserted_values = 0

        for fdc_id in keep_ids:
            info = food_info.get(fdc_id)
            if not info:
                continue

            present_essential = len(per_food[fdc_id])

            food = get_or_create_food(
                db,
                fdc_id=fdc_id,
                name=info.name,
                present_count=present_essential,
                total_count=total_essential,
            )
            inserted_foods += 1

            conf = confidence_for_data_type(info.data_type)

            for aa_name, mg_amount in per_food[fdc_id].items():
                upsert_food_amino_acid(
                    db=db,
                    food_id=food.id,
                    source_id=src.id,
                    amino_acid=aa_name,
                    amount_mg_per_100g=float(mg_amount),
                    confidence=float(conf),
                )
                inserted_values += 1

        refresh_coverage_flags(db)
        db.commit()

    print(f"🎉 USDA bulk ingest complete. Foods processed: {inserted_foods}, AA values upserted: {inserted_values}")


if __name__ == "__main__":
    run()
