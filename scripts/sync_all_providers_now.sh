#!/bin/bash
# Quick script to sync all providers and keep database fully updated
# Run this to immediately sync all 30 providers to database

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
API_URL="${API_URL:-https://api.gatewayz.ai}"
ADMIN_KEY="${ADMIN_KEY:-}"

echo -e "${GREEN}🚀 Gatewayz Full Database Sync${NC}"
echo -e "${GREEN}==============================${NC}"
echo ""

# Check if API is reachable
echo "📡 Checking API availability..."
if ! curl -s -f "${API_URL}/health" > /dev/null; then
    echo -e "${RED}❌ API not reachable at ${API_URL}${NC}"
    echo "   Please check your API_URL environment variable"
    exit 1
fi
echo -e "${GREEN}✅ API is reachable${NC}"
echo ""

# Get current database count
echo "📊 Current database status..."
DB_COUNT=$(curl -s "${API_URL}/admin/model-sync/status" | jq -r '.models.stats.total_active // 0')
echo "   Database models: ${DB_COUNT}"
echo ""

# List all providers
ALL_PROVIDERS="openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface"

echo "🔄 Starting full sync of 30 providers..."
echo "   This may take 3-5 minutes..."
echo ""

# Trigger full sync
START_TIME=$(date +%s)

echo "   POST ${API_URL}/admin/model-sync/all"
RESPONSE=$(curl -s -X POST "${API_URL}/admin/model-sync/all")

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# Check if successful
SUCCESS=$(echo "$RESPONSE" | jq -r '.success // false')

if [ "$SUCCESS" = "true" ]; then
    echo -e "${GREEN}✅ Sync completed successfully!${NC}"
    echo ""

    # Get sync results
    TOTAL_SYNCED=$(echo "$RESPONSE" | jq -r '.details.total_models_synced // 0')
    TOTAL_FETCHED=$(echo "$RESPONSE" | jq -r '.details.total_models_fetched // 0')
    PROVIDERS_PROCESSED=$(echo "$RESPONSE" | jq -r '.details.providers_processed // 0')

    echo "📈 Sync Results:"
    echo "   Duration: ${DURATION}s"
    echo "   Providers processed: ${PROVIDERS_PROCESSED}"
    echo "   Models fetched: ${TOTAL_FETCHED}"
    echo "   Models synced: ${TOTAL_SYNCED}"
    echo ""

    # Check new database count
    echo "📊 Verifying new database count..."
    sleep 2  # Wait for database to update
    NEW_DB_COUNT=$(curl -s "${API_URL}/admin/model-sync/status" | jq -r '.models.stats.total_active // 0')
    MODELS_ADDED=$((NEW_DB_COUNT - DB_COUNT))

    echo "   Previous: ${DB_COUNT} models"
    echo "   Current:  ${NEW_DB_COUNT} models"
    echo "   Added:    ${MODELS_ADDED} models"
    echo ""

    if [ "$NEW_DB_COUNT" -ge 17000 ]; then
        echo -e "${GREEN}✅ SUCCESS: Database is fully synced!${NC}"
        echo "   Your database now has ${NEW_DB_COUNT} models"
    elif [ "$NEW_DB_COUNT" -ge 15000 ]; then
        echo -e "${YELLOW}⚠️  WARNING: Database has ${NEW_DB_COUNT} models${NC}"
        echo "   Expected ~18,000. Some providers may have failed."
        echo "   Check logs for errors."
    else
        echo -e "${RED}❌ ERROR: Database only has ${NEW_DB_COUNT} models${NC}"
        echo "   Expected ~18,000. Sync may have failed."
        echo "   Try running again or check logs."
    fi
else
    echo -e "${RED}❌ Sync failed${NC}"
    echo ""
    echo "Error details:"
    echo "$RESPONSE" | jq '.'
    exit 1
fi

echo ""
echo "📝 Next Steps:"
echo "   1. Configure auto-sync for all providers:"
echo "      export PRICING_SYNC_PROVIDERS=\"${ALL_PROVIDERS}\""
echo ""
echo "   2. Add to .env file or Railway/Vercel environment variables"
echo ""
echo "   3. Restart your application"
echo ""
echo "   4. Set up monitoring (optional):"
echo "      ./scripts/monitor_sync_health.py"
echo ""
echo "🎉 Done!"
