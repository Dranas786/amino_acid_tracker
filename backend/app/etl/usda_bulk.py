# app/etl/usda_bulk.py
# ---------------------------------------------------------
# USDA BULK DATA INGEST — DOWNLOAD + UNZIP HELPERS
#
# This file does NOT parse any nutrient data and does NOT write to Postgres.
# Its only job is:
#   1) Download the USDA dataset zip (CSV or JSON distribution)
#   2) Extract the zip into a known folder
#   3) Return paths so the next ETL step can parse the extracted files
#
# Why we do bulk download:
# - The USDA API is great for small lookups, but ingesting "all foods"
#   is too slow and rate-limited.
# - Bulk downloads let us ingest weekly and scale properly.
#
# USDA dataset downloads (official):
# https://fdc.nal.usda.gov/download-datasets.html
# ---------------------------------------------------------

from __future__ import annotations

import shutil            # used to delete old extracted folders cleanly
import zipfile           # used to extract zip files
from dataclasses import dataclass
from pathlib import Path

import requests          # HTTP client to download dataset zip


# ---------------------------------------------------------
# Simple return type for download+extract operations
# ---------------------------------------------------------
# Instead of returning a tuple (zip_path, extracted_dir),
# we return a named object so code is easier to read:
#   result.zip_path
#   result.extracted_dir
# ---------------------------------------------------------
@dataclass
class DownloadResult:
    zip_path: Path
    extracted_dir: Path


# ---------------------------------------------------------
# Helper: ensure a directory exists
# ---------------------------------------------------------
# p.mkdir(parents=True, exist_ok=True) means:
# - create all parent folders if needed
# - do not error if the folder already exists
# ---------------------------------------------------------
def _ensure_dir(p: Path) -> None:
    """Create directory p if it doesn't exist."""
    p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------
# Download helper
# ---------------------------------------------------------
# Downloads a large file safely by streaming it in chunks, rather than
# loading the whole file into memory.
#
# - url: where we download from
# - out_path: where we save it locally
# - timeout_s: network timeout to avoid hanging forever
#
# Returns: the out_path so callers can chain operations easily.
# ---------------------------------------------------------
def download_file(url: str, out_path: Path, timeout_s: int = 120) -> Path:
    """
    Download a file from `url` to `out_path`.

    We stream the response so large zips do not explode RAM usage.
    """

    # Ensure the folder we want to write into exists.
    # Example: if out_path is data/usda/usda_fdc.zip
    # then out_path.parent is data/usda/
    _ensure_dir(out_path.parent)

    # requests.get(..., stream=True) means:
    # - "give me the response body incrementally"
    # - we can write chunk by chunk to disk
    with requests.get(url, stream=True, timeout=timeout_s) as r:
        # If the server returns 404/403/500 etc, raise a helpful exception.
        r.raise_for_status()

        # Open output file in "write binary" mode because it's a zip file.
        with open(out_path, "wb") as f:
            # iter_content gives us the response body in chunks.
            # 1MB chunk size is a nice balance (fast but not memory heavy).
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                # Sometimes servers send empty keep-alive chunks; ignore them.
                if chunk:
                    f.write(chunk)

    return out_path


# ---------------------------------------------------------
# Unzip helper
# ---------------------------------------------------------
# Extracts a zip to a target folder.
#
# Important: we delete the existing extract folder first to keep this
# script repeatable. Otherwise you might accidentally mix old files
# with the new dataset.
# ---------------------------------------------------------
def unzip_file(zip_path: Path, extract_to: Path) -> Path:
    """
    Unzip `zip_path` into `extract_to`.

    If `extract_to` exists, delete it first so each run starts clean.
    """

    # If folder exists from a previous run, remove it completely.
    # shutil.rmtree deletes directories recursively.
    if extract_to.exists():
        shutil.rmtree(extract_to)

    # Recreate the empty folder we will extract into.
    _ensure_dir(extract_to)

    # Open the zip and extract everything inside it.
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_to)

    return extract_to


# ---------------------------------------------------------
# Combined helper: download + unzip USDA dataset zip
# ---------------------------------------------------------
# This is the primary function the "real" ingestor will call.
#
# Example output inside container:
#   zip_path      -> /app/data/usda/usda_fdc.zip
#   extracted_dir -> /app/data/usda/extracted/
# ---------------------------------------------------------
def fetch_and_extract_usda_zip(
    dataset_zip_url: str,
    work_dir: str = "data/usda",
    filename: str = "usda_fdc.zip",
) -> DownloadResult:
    """
    Download the USDA dataset zip and extract it.

    Args:
      dataset_zip_url: direct URL to USDA dataset zip file
      work_dir: where to store downloaded/extracted data (relative to /app)
      filename: name to save the zip as

    Returns:
      DownloadResult with:
        - zip_path: where the zip was saved
        - extracted_dir: where it was extracted
    """

    # Convert work_dir string into a Path object.
    # Path gives us safer, cross-platform path handling.
    work = Path(work_dir)

    # Ensure work dir exists.
    _ensure_dir(work)

    # Where the zip will be saved.
    zip_path = work / filename

    # Where the zip will be extracted.
    extracted_dir = work / "extracted"

    # 1) Download zip
    download_file(dataset_zip_url, zip_path)

    # 2) Unzip into extracted_dir
    unzip_file(zip_path, extracted_dir)

    return DownloadResult(zip_path=zip_path, extracted_dir=extracted_dir)


# ---------------------------------------------------------
# Helper: find the dataset "root" folder after extraction
# ---------------------------------------------------------
# Some zips have this layout:
#   extracted/
#     FoodData_Central_csv_YYYY-MM-DD/
#       food.csv
#       food_nutrient.csv
#
# Others extract directly into extracted/ with the csv files.
#
# This function tries to return the folder that actually contains the dataset files.
# ---------------------------------------------------------
def guess_dataset_root(extracted_dir: Path) -> Path:
    """
    Return the probable dataset root folder.

    If extracted_dir contains exactly one folder, return that child.
    Otherwise assume extracted_dir is already the dataset root.
    """

    # List all child directories inside extracted_dir
    children = [p for p in extracted_dir.iterdir() if p.is_dir()]

    # If there's exactly one folder, USDA likely wrapped files in it.
    if len(children) == 1:
        return children[0]

    # Otherwise, files are likely directly under extracted_dir.
    return extracted_dir
