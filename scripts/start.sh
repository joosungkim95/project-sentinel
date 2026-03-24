#!/bin/bash
# Startup script for Railway deployment.
# Runs migrations then starts the app.

set -e

echo "=== Sentinel Startup ==="
echo "Python: $(python --version)"
echo "SHADOW_MODE=$SHADOW_MODE"
echo "Installed packages:"
pip list 2>/dev/null | grep -iE "anthropic|apscheduler|sqlalchemy|numpy|pydantic"

echo "Testing imports..."
python -c "
try:
    from api.main import app
    print('All imports OK')
except Exception as e:
    print(f'IMPORT ERROR: {e}')
    import traceback
    traceback.print_exc()
"

echo "Running database migrations..."
python -m alembic upgrade head || echo "Migration failed but continuing..."

echo "Starting Sentinel..."
exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info
