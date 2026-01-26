# app/logic.py
# ---------------------------------------------------------
# This file contains BUSINESS LOGIC.
#
# What is "business logic"?
# - It is the "math" and "rules" of your app.
# - It is NOT basic database reading/writing (CRUD).
#
# In our app, business logic includes:
# 1) Mixing multiple foods and summing amino acid totals
# 2) Recommending foods that are high in amino acids you are lacking
#
# Think of it like:
# - crud.py  -> "get me data from the DB"
# - logic.py -> "use that data to compute something useful"
# ---------------------------------------------------------

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Food, FoodAminoAcid
from app.schemas import (
    ESSENTIAL_AMINO_ACIDS,
    MixIn,
    MixOut,
    RecommendIn,
    RecommendationOut,
    FoodOut,
)


# ---------------------------------------------------------
# Helper: ensure amino acid names are consistent
# ---------------------------------------------------------
# Users might send "Lysine" or "lysine" or "LYSINE"
# We normalize to lowercase for matching.
# ---------------------------------------------------------

def _normalize_aa_name(name: str) -> str:
    return name.strip().lower()


# ---------------------------------------------------------
# 1) MIX: Sum amino acids across multiple foods
# ---------------------------------------------------------
# Frontend sends something like:
# {
#   "items": [
#     {"food_id": 1, "grams": 200},
#     {"food_id": 2, "grams": 150}
#   ]
# }
#
# We respond with totals (in mg) for each essential AA:
# {
#   "totals_mg": {
#      "lysine": 5300,
#      "leucine": 4100,
#      ...
#   }
# }
# ---------------------------------------------------------

def compute_mix(db: Session, req: MixIn) -> MixOut:
    """
    Compute total essential amino acids for a set of foods.

    VERY IMPORTANT UNIT IDEA:
      - We store amino acids as "mg per 100g" in the database.
      - The user gives us grams eaten (like 150g chicken).
      - So we convert using a factor: (grams / 100).

    Example:
      If lysine = 2600 mg/100g, and you ate 150g:
      factor = 150 / 100 = 1.5
      lysine_total = 2600 * 1.5 = 3900 mg
    """

    # Start totals at 0 for every essential amino acid.
    # This ensures the response ALWAYS contains all 9, even if some foods are missing values.
    totals: dict[str, float] = {aa: 0.0 for aa in ESSENTIAL_AMINO_ACIDS}

    # Loop over each food item the user added.
    for item in req.items:
        # Convert grams to "how many 100g units?"
        # Example: 150g -> 1.5
        factor = item.grams / 100.0

        # Fetch all amino acids for this food from the DB.
        # This query returns rows from food_amino_acids where food_id matches.
        stmt = select(FoodAminoAcid).where(FoodAminoAcid.food_id == item.food_id)
        aa_rows = db.execute(stmt).scalars().all()

        # Add each amino acid amount into the totals.
        for row in aa_rows:
            aa_name = _normalize_aa_name(row.amino_acid)

            # Only sum the essential amino acids we care about.
            # (In case the dataset contains other amino acids or alternate naming.)
            if aa_name in totals:
                # row.amount_mg_per_100g is mg per 100g.
                # Multiply by factor to get mg for the user’s grams.
                totals[aa_name] += float(row.amount_mg_per_100g) * factor

    # Return totals as a MixOut schema (FastAPI will convert it to JSON).
    return MixOut(totals_mg=totals)


# ---------------------------------------------------------
# 2) RECOMMEND: Suggest foods for limiting amino acids
# ---------------------------------------------------------
# Frontend sends something like:
# {
#   "limiting_amino_acids": ["lysine", "methionine"],
#   "top_n": 10
# }
#
# We respond with foods that have high values for those AAs (per 100g).
# ---------------------------------------------------------

def recommend_foods(db: Session, req: RecommendIn) -> list[RecommendationOut]:
    """
    Recommend foods high in the amino acids the user is lacking.

    Our first simple scoring method:
      - For each food, sum the mg/100g amounts for the limiting amino acids.
      - Higher sum = better recommendation.

    This is a good MVP.
    Later improvements could include:
      - diet filters (vegan, halal, allergies)
      - calories per serving
      - "best per gram of protein"
      - user preferences
    """

    # Normalize the amino acids the user asked for.
    limiting = [_normalize_aa_name(x) for x in req.limiting_amino_acids]

    # Keep only amino acids we actually support.
    limiting = [aa for aa in limiting if aa in ESSENTIAL_AMINO_ACIDS]

    # If nothing valid was provided, return empty list.
    if not limiting:
        return []

    # ---------------------------------------------------------
    # Query all relevant amino-acid rows from the DB
    # ---------------------------------------------------------
    # We fetch rows for amino acids in the limiting list.
    # This makes the query smaller than fetching the entire table.
    #
    # SQL idea:
    # SELECT food_id, amino_acid, amount_mg_per_100g
    # FROM food_amino_acids
    # WHERE amino_acid IN ('lysine', 'methionine');
    stmt = (
        select(FoodAminoAcid.food_id, FoodAminoAcid.amino_acid, FoodAminoAcid.amount_mg_per_100g)
        .where(FoodAminoAcid.amino_acid.in_(limiting))
    )

    rows = db.execute(stmt).all()

    # ---------------------------------------------------------
    # Build a score per food
    # ---------------------------------------------------------
    # scores[food_id] = total mg/100g across limiting amino acids
    scores: dict[int, float] = {}

    for food_id, aa_name, amount in rows:
        # Add the amount to the food's score
        scores[food_id] = scores.get(food_id, 0.0) + float(amount)

    # If no foods match, return empty list.
    if not scores:
        return []

    # ---------------------------------------------------------
    # Pick the top foods by score
    # ---------------------------------------------------------
    # Sort foods by score descending, then take the top N.
    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[: req.top_n]

    top_food_ids = [food_id for food_id, _score in top]

    # Fetch the Food objects for those ids
    foods = db.execute(select(Food).where(Food.id.in_(top_food_ids))).scalars().all()
    food_map = {f.id: f for f in foods}

    # ---------------------------------------------------------
    # Build the response
    # ---------------------------------------------------------
    out: list[RecommendationOut] = []

    for food_id, score in top:
        f = food_map.get(food_id)
        if not f:
            continue

        out.append(
            RecommendationOut(
                food=FoodOut.model_validate(f),
                score=float(score),
                reason=f"High in {', '.join(limiting)} per 100g",
            )
        )

    return out
