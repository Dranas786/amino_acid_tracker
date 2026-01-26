# app/models.py
# ---------------------------------------------------------
# This file defines the DATABASE TABLES for the application.
#
# Each class = one table
# Each attribute = one column
#
# SQLAlchemy uses these classes to:
# - create tables
# - run queries
# - map rows <-> Python objects
# ---------------------------------------------------------

from sqlalchemy import (
    String,
    Integer,
    Float,
    Boolean,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# ---------------------------------------------------------
# Source table
# ---------------------------------------------------------
# This table stores WHERE the data came from.
# Example:
# - USDA FoodData Central
# - FAO INFOODS
# - A research paper (DOI link)
# ---------------------------------------------------------

class Source(Base):
    __tablename__ = "sources"

    # Primary key (auto-incrementing integer)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # "dataset" or "publication"
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Human-readable name
    # e.g. "USDA FoodData Central"
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Clickable link shown in the UI
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)

    # Short citation text for tooltips / info buttons
    citation_text: Mapped[str] = mapped_column(String(500), nullable=False)

    # Optional version info (dataset version, year, etc.)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)


# ---------------------------------------------------------
# Food table
# ---------------------------------------------------------
# This represents a FOOD ITEM the user can search for.
# Example:
# - "Chicken breast, cooked"
# - "Lentils, boiled"
# ---------------------------------------------------------

class Food(Base):
    __tablename__ = "foods"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Name shown to the user
    name: Mapped[str] = mapped_column(String(300), nullable=False)

    # Where this food record came from
    # e.g. "USDA", "INFOODS"
    external_source: Mapped[str] = mapped_column(String(50), nullable=False)

    # ID from the external dataset
    # e.g. USDA foodId
    external_food_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # ---------------------------------------------------------
    # Amino acid data coverage (for UI warnings + Way 2 pipeline)
    # ---------------------------------------------------------
    # How many of the 9 essential amino acids we currently have values for
    essential_aa_present_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )

    # Total number of essential amino acids we care about (usually 9)
    essential_aa_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=9
    )

    # If True, USDA (or the current source) did NOT provide enough AA coverage,
    # meaning missing values are "not measured / not reported" (unknown, not zero).
    # The UI can show: "Incomplete amino acid data — consider additional sources."
    amino_data_incomplete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )


    # Relationship to amino acid values
    # One food -> many amino acid rows
    amino_acids: Mapped[list["FoodAminoAcid"]] = relationship(
        back_populates="food",
        cascade="all, delete-orphan"
    )

    # Enforce uniqueness so we don’t import the same food twice
    __table_args__ = (
        UniqueConstraint(
            "external_source",
            "external_food_id",
            name="uq_food_external"
        ),
    )


# ---------------------------------------------------------
# FoodAminoAcid table
# ---------------------------------------------------------
# This table stores the ACTUAL amino acid values.
#
# Each row answers:
# "Food X has Y mg of amino acid Z per 100g"
# ---------------------------------------------------------

class FoodAminoAcid(Base):
    __tablename__ = "food_amino_acids"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Foreign key to foods table
    food_id: Mapped[int] = mapped_column(
        ForeignKey("foods.id"),
        nullable=False
    )

    # Name of the amino acid
    # (one of the 9 essential amino acids)
    amino_acid: Mapped[str] = mapped_column(String(50), nullable=False)

    # Amount in mg per 100g of food
    amount_mg_per_100g: Mapped[float] = mapped_column(Float, nullable=False)

    # Units (always mg/100g after normalization)
    units: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="mg/100g"
    )

    # Confidence score (1.0 = dataset, <1 = estimated/extracted)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Foreign key to source table
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id"),
        nullable=False
    )

    # Relationships
    food: Mapped["Food"] = relationship(back_populates="amino_acids")
    source: Mapped["Source"] = relationship()

    # Prevent duplicate amino acids per food
    __table_args__ = (
        UniqueConstraint(
            "food_id",
            "amino_acid",
            name="uq_food_amino"
        ),
    )
