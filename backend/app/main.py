# app/main.py
# ---------------------------------------------------------
# This file is the entry point of your FastAPI backend.
# Docker will run: uvicorn app.main:app
# Which means:
# - import module "app.main"
# - find variable named "app" (the FastAPI instance)
# ---------------------------------------------------------

from fastapi import FastAPI

# Create the FastAPI application object.
# This "app" variable is what Uvicorn looks for when starting the server.
app = FastAPI(
    title="Amino Acid Tracker API",  # Name shown in the auto-generated docs
    version="0.1.0",                 # Your API version (useful when you change endpoints later)
)

# A simple "health check" endpoint.
# This lets you quickly verify the API is running.
# Example: GET http://localhost:8000/health
@app.get("/health")
def health_check():
    """
    Health check endpoint.

    Returns a simple JSON response.
    We use this to verify the server is up and responding.
    """
    return {"status": "ok"}
