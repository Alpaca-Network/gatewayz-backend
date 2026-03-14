#!/bin/bash
set -e

# Activate virtual environment if it exists (for Nixpacks/Railway deployments)
if [ -d "/opt/venv" ]; then
    source /opt/venv/bin/activate
fi

# Set PYTHONPATH to include src directory
export PYTHONPATH="${PYTHONPATH}:${PWD}/src"

# Quick sanity check (no pip install — Nixpacks handles dependencies)
echo "🔍 Python: $(python --version 2>&1)"
echo "🔍 httpx: $(python -c 'import httpx; print(httpx.__version__)' 2>/dev/null || echo 'not installed')"

# Start the application
echo "🚀 Starting Gatewayz API..."
# Note: No --reload to avoid Prometheus metric duplication
# Timeout settings to prevent 504 Gateway Timeouts:
# - timeout-keep-alive: 75s (slightly more than typical load balancer timeout of 60s)
# - timeout-graceful-shutdown: 30s (time for graceful shutdown)
exec uvicorn src.main:app \
  --host 0.0.0.0 \
  --port ${PORT:-8000} \
  --workers 1 \
  --timeout-keep-alive 75 \
  --timeout-graceful-shutdown 30
