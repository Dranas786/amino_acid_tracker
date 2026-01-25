# app/schemas.py
# ---------------------------------------------------------
# This file defines Pydantic SCHEMAS.
#
# Schemas:
# - describe the SHAPE of data
# - validate incoming requests
# - control outgoing responses
#
# Think of schemas as:
# "The contract between backend and frontend"
# ---------------------------------------------------------

from pydantic import BaseModel, Field
from typing import Dict, List, Optional

# ---------------------------------------------------------
# Constants
# ---------------------------------------------------------
# The 9 essential amino acids we care about
# Keeping this centralized avoids typos everywhere else
# ---------------------------------------------------------

ESSENTIAL_AMINO_ACIDS = [
    "histidine",
    "isoleucine",
    "leucine",
    "lysine",
    "methionine",
    "phenylalanine",
    "threonine",
    "tryptophan",
    "valine",
]

# ---------------------------------------------------------
# Source (provenance) schema
# ---------------------------------------------------------
# This is what the frontend sees when it clicks
# an "info" or "source" button.
# ---------------------------------------------------------

class SourceOut(BaseModel):
    source_type: str
    source_name: str
    source_url: str
    citation_text: str
    version: Optional[str] = None
    confidence: float

    class Config:
        from_attributes = True  # Allows ORM objects → schema conversion


# ---------------------------------------------------------
# Food schema
# ---------------------------------------------------------
# Basic food info used in search results and responses
# ---------------------------------------------------------

class FoodOut(BaseModel):
    id: int
    name: str
    external_source: str
    external_food_id: str

    class Config:
        from_attributes = True


# ---------------------------------------------------------
# Food amino acid response schema
# ---------------------------------------------------------
# Returned when the frontend asks:
# "Give me amino acids for this food"
# ---------------------------------------------------------

class FoodAminoOut(BaseModel):
    food: FoodOut

    # Example:
    # {
    #   "lysine": 2600,
    #   "leucine": 1900
    # }
    amino_acids_mg_per_100g: Dict[str, float]

    # Per–amino-acid provenance
    # {
    #   "lysine": { source info },
    #   "leucine": { source info }
    # }
    sources: Dict[str, SourceOut]


# ---------------------------------------------------------
# Mix input schemas
# ---------------------------------------------------------
# Used when user adds multiple foods to a meal
# ---------------------------------------------------------

class MixItem(BaseModel):
    food_id: int
    grams: float = Field(..., gt=0)  # must be > 0

class MixIn(BaseModel):
    items: List[MixItem]


# ---------------------------------------------------------
# Mix output schema
# ---------------------------------------------------------
# Summed amino acids for all foods combined
# ---------------------------------------------------------

class MixOut(BaseModel):
    totals_mg: Dict[str, float]


# ---------------------------------------------------------
# Recommendation schemas
# ---------------------------------------------------------
# Used to suggest foods that fix amino acid deficits
# ---------------------------------------------------------

class RecommendIn(BaseModel):
    limiting_amino_acids: List[str]
    top_n: int = Field(10, gt=0)

class RecommendationOut(BaseModel):
    food: FoodOut
    score: float
    reason: str
