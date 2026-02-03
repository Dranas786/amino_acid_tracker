# app/papers/table_extractor.py
# ---------------------------------------------------------
# Extract amino-acid tables from PMC XML and normalize units
#
# Responsibilities:
# - Parse PMC XML (JATS format)
# - Find table-like structures mentioning amino acids
# - Extract numeric values + units
# - Normalize everything to mg / 100 g
#
# Non-responsibilities:
# - No DB access
# - No confidence scoring
# - No food matching
# ---------------------------------------------------------

from __future__ import annotations

from typing import Optional
import re

from bs4 import BeautifulSoup


# ---------------------------------------------------------
# Canonical amino-acid names we care about
# ---------------------------------------------------------

AMINO_ALIASES = {
    "lysine": ["lysine", "lys"],
    "leucine": ["leucine", "leu"],
    "isoleucine": ["isoleucine", "ile"],
    "valine": ["valine", "val"],
    "threonine": ["threonine", "thr"],
    "tryptophan": ["tryptophan", "trp"],
    "methionine": ["methionine", "met"],
    "phenylalanine": ["phenylalanine", "phe"],
    "histidine": ["histidine", "his"],
}


# ---------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------

def normalize_to_mg_per_100g(amount: float, units: str) -> Optional[float]:
    """
    Convert various units into mg / 100 g.

    Supported examples:
      - mg/100g   -> as-is
      - g/100g    -> * 1000
      - mg/g      -> * 100
      - g/kg      -> * 100
    """
    u = units.lower().replace(" ", "")

    try:
        if u in ("mg/100g", "mgper100g"):
            return amount

        if u in ("g/100g", "gper100g"):
            return amount * 1000.0

        if u in ("mg/g", "mgperg"):
            return amount * 100.0

        if u in ("g/kg", "gperkg"):
            return amount * 100.0

    except Exception:
        return None

    return None


# ---------------------------------------------------------
# Core extraction
# ---------------------------------------------------------

def extract_amino_tables_from_pmc_xml(pmc_xml: str) -> list[dict]:
    """
    Parse PMC XML and attempt to extract amino-acid composition tables.

    Returns:
      List of dicts with:
        - amino_acid
        - amount_mg_per_100g
        - raw_units
        - context
    """

    soup = BeautifulSoup(pmc_xml, "lxml")

    results: list[dict] = []

    # PMC tables usually appear as <table-wrap>
    table_wraps = soup.find_all("table-wrap")

    for table_wrap in table_wraps:
        caption_text = ""
        caption = table_wrap.find("caption")
        if caption:
            caption_text = caption.get_text(" ", strip=True)

        table = table_wrap.find("table")
        if not table:
            continue

        rows = table.find_all("tr")
        if not rows:
            continue

        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            row_text = " ".join(c.get_text(" ", strip=True) for c in cells).lower()

            for canonical, aliases in AMINO_ALIASES.items():
                if not any(alias in row_text for alias in aliases):
                    continue

                # Look for a numeric value + unit
                # Example matches: "2.4 g/100 g", "2400 mg/100g"
                m = re.search(
                    r"([\d\.]+)\s*(mg|g)\s*/\s*(100g|g|kg)",
                    row_text,
                )
                if not m:
                    continue

                amount = float(m.group(1))
                unit = f"{m.group(2)}/{m.group(3)}"

                normalized = normalize_to_mg_per_100g(amount, unit)
                if normalized is None:
                    continue

                results.append(
                    {
                        "amino_acid": canonical,
                        "amount_mg_per_100g": normalized,
                        "raw_units": unit,
                        "context": caption_text[:300],
                    }
                )

    return results
