#!/bin/bash
# Startup script for Railway deployment.
# Runs migrations then starts the app.

set -e

echo "Running database migrations..."
python -m alembic upgrade head

echo "Starting Sentinel..."
exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
