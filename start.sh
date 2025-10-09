#!/bin/bash
set -e

echo "🔍 Checking dependency versions..."

# Check current httpx version
HTTPX_VERSION=$(python -c "import httpx; print(httpx.__version__)" 2>/dev/null || echo "not found")
OPENAI_VERSION=$(python -c "import openai; print(openai.__version__)" 2>/dev/null || echo "not found")

echo "Current httpx version: $HTTPX_VERSION"
echo "Current openai version: $OPENAI_VERSION"

# If versions are wrong, reinstall correct ones
if [ "$HTTPX_VERSION" != "0.27.0" ] || [ "$OPENAI_VERSION" != "1.44.0" ]; then
    echo "⚠️  Wrong versions detected! Fixing..."
    pip install --no-cache-dir --force-reinstall httpx==0.27.0 openai==1.44.0
    echo "✅ Dependencies fixed!"
else
    echo "✅ Correct versions already installed"
fi

# Start the application
echo "🚀 Starting Gatewayz API..."
exec uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}
