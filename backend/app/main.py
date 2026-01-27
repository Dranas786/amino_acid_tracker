# app/main.py
# ---------------------------------------------------------
# This is the ENTRY POINT of the FastAPI application.
#
# Responsibilities of this file:
# 1) Create the FastAPI app
# 2) Create database tables on startup (dev-only)
# 3) Attach API routes
#
# This file should stay SMALL.
# Business logic and DB code live elsewhere.
# ---------------------------------------------------------

from fastapi import FastAPI
import app.models  # ensures FailedSearch is registered on Base metadata as others were imported from crud.py etc.


# Import Base + engine so tables can be created
from app.db import Base, engine

# Import router that contains all endpoints
from app.routes import router

# static html file for testing ui
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse


# ---------------------------------------------------------
# Create FastAPI application
# ---------------------------------------------------------

app = FastAPI(
    title="Amino Acid Tracker API",
    version="0.1.0",
    description="Backend API for tracking essential amino acids and food combinations",
)

# ---------------------------------------------------------
# Create database tables (DEV ONLY)
# ---------------------------------------------------------
# This tells SQLAlchemy:
# - Look at all classes that inherit from Base (models.py)
# - Create the corresponding tables in Postgres if they do not exist
#
# IMPORTANT:
# - This is fine for learning and local development
# - In production, we will replace this with Alembic migrations
# ---------------------------------------------------------

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------
# Attach routes to the app
# ---------------------------------------------------------
# This makes all routes defined in routes.py active:
# - /foods
# - /foods/{id}/amino
# - /mix
# - /recommend
# ---------------------------------------------------------

app.include_router(router)

# ---------------------------------------------------------
# Simple root endpoint (optional but useful)
# ---------------------------------------------------------
# Lets you open http://localhost:8000/ and see that API is alive
# ---------------------------------------------------------

app.mount("/ui", StaticFiles(directory="app/static", html=True), name="ui")

@app.get("/")
def root():
    return RedirectResponse(url="/ui")

