# app/db.py
# ---------------------------------------------------------
# This file is responsible for:
# - Connecting to the PostgreSQL database
# - Creating database sessions
# - Providing a base class for all ORM models
#
# Think of this file as:
# "The database plumbing for the entire backend"
# ---------------------------------------------------------

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# ---------------------------------------------------------
# DATABASE_URL
# ---------------------------------------------------------
# This is the connection string SQLAlchemy uses to talk to Postgres.
#
# Example format:
# postgresql+psycopg://USER:PASSWORD@HOST:PORT/DATABASE
#
# If DATABASE_URL exists use it else uses the local postgres:
# - Docker Compose can inject it
# - Cloud Run can inject it later
# - We never hardcode secrets
# ---------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://app:app@localhost:5432/amino_tracker"
)

# ---------------------------------------------------------
# SQLAlchemy Engine
# ---------------------------------------------------------
# The engine is the core interface to the database.
# It manages:
# - Connection pooling
# - Talking to Postgres via psycopg
# - Executing SQL under the hood
# ---------------------------------------------------------

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Checks connections before using them (avoids stale connections)
)

# ---------------------------------------------------------
# Session factory
# ---------------------------------------------------------
# A Session is a "conversation" with the database.
# Each request gets its own session.
#
# autoflush=False:
# - Prevents SQLAlchemy from auto-writing changes
# autocommit=False:
# - Forces us to explicitly commit transactions
# ---------------------------------------------------------

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------
# Base class for ORM models
# ---------------------------------------------------------
# All database models (tables) will inherit from this class.
# SQLAlchemy uses it to:
# - Track tables
# - Create schemas
# - Generate migrations (later with Alembic)
# ---------------------------------------------------------

class Base(DeclarativeBase):
    pass

# ---------------------------------------------------------
# Dependency for FastAPI routes
# ---------------------------------------------------------
# This function:
# - Creates a DB session
# - Yields it to the request handler
# - Ensures the session is closed after the request finishes
#
# This is how FastAPI safely handles DB access per request.
# ---------------------------------------------------------

def get_db():
    db = SessionLocal()   # Open a new database session
    try:
        yield db         # Give the session to the route handler
    finally:
        db.close()       # Always close the session (even on error)
