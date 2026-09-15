#!/bin/bash
set -e

# Activate virtual environment if it exists (for Nixpacks/Railway deployments)
if [ -d "/opt/venv" ]; then
    source /opt/venv/bin/activate
fi

# FREEZE FIX: Version-check reinstall is only needed when packages are genuinely
# out of sync (e.g. after a bad base image layer). We gate it behind a stamp file
# so subsequent container restarts (Railway keeps the container alive and just
# re-runs start.sh on crash/deploy) skip the 30-60s pip install entirely.
# The stamp records the required versions; if requirements change, update REQUIRED_VERSIONS.
REQUIRED_VERSIONS="httpx==0.27.0,openai==1.109.1"
STAMP_FILE="/tmp/.dep_versions_ok"

needs_reinstall=false

if [ ! -f "$STAMP_FILE" ] || [ "$(cat "$STAMP_FILE" 2>/dev/null)" != "$REQUIRED_VERSIONS" ]; then
    echo "🔍 Checking dependency versions..."
    HTTPX_VERSION=$(python -c "import httpx; print(httpx.__version__)" 2>/dev/null || echo "not found")
    OPENAI_VERSION=$(python -c "import openai; print(openai.__version__)" 2>/dev/null || echo "not found")

    echo "Current httpx version: $HTTPX_VERSION"
    echo "Current openai version: $OPENAI_VERSION"

    if [ "$HTTPX_VERSION" != "0.27.0" ] || [ "$OPENAI_VERSION" != "1.109.1" ]; then
        echo "⚠️  Wrong versions detected! Fixing..."
        python -m pip install --no-cache-dir --force-reinstall httpx==0.27.0 openai==1.109.1
        echo "✅ Dependencies fixed!"
    else
        echo "✅ Correct versions already installed"
    fi

    # Write stamp so next restart skips this check
    echo "$REQUIRED_VERSIONS" > "$STAMP_FILE"
else
    echo "✅ Dependency versions verified (cached)"
fi

# Set PYTHONPATH to include src directory
export PYTHONPATH="${PYTHONPATH}:${PWD}/src"

# Start the application
echo "🚀 Starting Gatewayz API..."
# Note: No --reload to avoid Prometheus metric duplication
# Timeout settings to prevent 504 Gateway Timeouts:
# - timeout-keep-alive: 75s (slightly more than typical load balancer timeout of 60s)
# - timeout-graceful-shutdown: 30s (time for graceful shutdown)
# - proxy-headers: Railway terminates TLS and forwards over plain HTTP. Without
#   this the app believes its own scheme is http, so every URL it builds is
#   http://: FastAPI's trailing-slash redirect answered
#   https://api.gatewayz.ai/v1/status with 307 -> http://api.gatewayz.ai/v1/status/,
#   downgrading the connection. Harmless on a public status route; not on an
#   authenticated one, where the client would resend its key in cleartext.
#   forwarded-allow-ips=* trusts X-Forwarded-Proto/For from the peer, which is
#   sound here because Railway's edge is the only route to this container — and
#   request-attributed client IPs (rate limits, allowlists) already prefer
#   X-Real-IP / the rightmost X-Forwarded-For entry, which this does not change.
exec uvicorn src.main:app \
  --host 0.0.0.0 \
  --port ${PORT:-8000} \
  --workers 1 \
  --timeout-keep-alive 75 \
  --timeout-graceful-shutdown 30 \
  --proxy-headers \
  --forwarded-allow-ips '*'
