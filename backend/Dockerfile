# ---------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------
# WHAT THIS FILE DOES:
# - Defines how to build the backend API container
# - Installs Python + dependencies
# - Copies your application code
# - Starts the FastAPI server
#
# IMPORTANT:
# This SAME Dockerfile will be used:
# - locally with Docker Compose
# - later in production on Google Cloud Run
# ---------------------------------------------------------

# -------------------------
# 1. Base image
# -------------------------
# Use a slim Python image to keep container size small
FROM python:3.12-slim

# -------------------------
# 2. Set working directory
# -------------------------
# All commands below will run inside /app
WORKDIR /app

# -------------------------
# 3. Install system dependencies (minimal)
# -------------------------
# - gcc: sometimes needed for Python packages with native extensions
# - curl: useful for health checks/debugging
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

# -------------------------
# 4. Install Python dependencies
# -------------------------
# Copy only requirements.txt first
# This allows Docker to cache dependencies if code changes
COPY requirements.txt .

# Install dependencies inside the container
RUN pip install --no-cache-dir -r requirements.txt

# -------------------------
# 5. Copy application code
# -------------------------
# Copy everything from backend/ into the container
COPY . .

# -------------------------
# 6. Expose application port (documentation only)
# -------------------------
# This tells Docker (and humans) that the app listens on port 8000
EXPOSE 8000

# -------------------------
# 7. Start the FastAPI server
# -------------------------
# - app.main:app  → module app.main, variable app
# - --host 0.0.0.0 → listen on all network interfaces (required in containers)
# - --port 8000 → internal container port
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
