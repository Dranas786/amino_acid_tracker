# app/routes.py
# ---------------------------------------------------------
# This file defines API ROUTES (endpoints).
#
# A route is basically:
# - a URL path (like "/foods")
# - an HTTP method (GET, POST, etc.)
# - a Python function that runs when that endpoint is called
#
# Think:
# - Frontend calls GET /foods?q=chicken
# - FastAPI runs search_foods_endpoint()
# - That function calls crud.search_foods()
# - FastAPI returns JSON back to frontend
#
# IMPORTANT DESIGN IDEA:
# - routes.py should be THIN
# - it should NOT do heavy computation
# - it should:
#   1) read request input
#   2) call crud/logic functions
#   3) return the result
# ---------------------------------------------------------

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app import crud, logic
from app.schemas import FoodOut, FoodAminoOut, MixIn, MixOut, RecommendIn, RecommendationOut
from app.failed_searches import log_failed_search


# ---------------------------------------------------------
# APIRouter
# ---------------------------------------------------------
# APIRouter is like a "mini FastAPI app" that holds routes.
# We will later "attach" this router to the main FastAPI app in main.py.
# ---------------------------------------------------------

router = APIRouter()


# ---------------------------------------------------------
# GET /foods?q=...
# ---------------------------------------------------------
# Purpose:
# - Search foods by name (for the search bar)
#
# Example request:
#   GET /foods?q=chicken
#
# Example response:
#   [
#     {"id": 1, "name": "Chicken breast ...", ...},
#     {"id": 2, "name": "Chicken thigh ...", ...}
#   ]
# ---------------------------------------------------------

@router.get("/foods", response_model=list[FoodOut])
def search_foods_endpoint(
    q: str = Query(..., min_length=2), # ... means q is required and has length at least 2
    db: Session = Depends(get_db),  # connect with db using session
):
    results = crud.search_foods(db, q)
    if not results:
        log_failed_search(db, q)
        db.commit()
    return results



# ---------------------------------------------------------
# GET /foods/{food_id}/amino
# ---------------------------------------------------------
# Purpose:
# - Get amino acid values + sources for ONE food
#
# Example request:
#   GET /foods/1/amino
# ---------------------------------------------------------

@router.get("/foods/{food_id}/amino", response_model=FoodAminoOut)
def food_amino_endpoint(
    food_id: int,                        # pulled from URL path
    db: Session = Depends(get_db),
):
    try:
        # Use CRUD function that returns FoodAminoOut
        return crud.get_food_amino(db, food_id)
    except ValueError as e:
        # Convert our ValueError into a proper HTTP 404
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------
# POST /mix
# ---------------------------------------------------------
# Purpose:
# - User sends multiple foods + grams
# - We compute total amino acids across everything
#
# Example request JSON:
# {
#   "items": [
#     {"food_id": 1, "grams": 200},
#     {"food_id": 2, "grams": 150}
#   ]
# }
# ---------------------------------------------------------

@router.post("/mix", response_model=MixOut)
def mix_endpoint(
    req: MixIn,                          # FastAPI validates JSON into this schema
    db: Session = Depends(get_db),
):
    # Call business logic function to compute totals
    return logic.compute_mix(db, req)


# ---------------------------------------------------------
# POST /recommend
# ---------------------------------------------------------
# Purpose:
# - User sends limiting amino acids
# - We return foods that are high in those amino acids
#
# Example request JSON:
# {
#   "limiting_amino_acids": ["lysine", "methionine"],
#   "top_n": 10
# }
# ---------------------------------------------------------

@router.post("/recommend", response_model=list[RecommendationOut])
def recommend_endpoint(
    req: RecommendIn,
    db: Session = Depends(get_db),
):
    # Call business logic function to get ranked recommendations
    return logic.recommend_foods(db, req)
