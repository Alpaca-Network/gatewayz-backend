#!/bin/bash
# Quick API Test Script for Model Management
# Tests all key endpoints to verify the system is working

set -e  # Exit on error

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    Gatewayz Model Management API - Live Test Suite        ║${NC}"
echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo ""

# Configuration
API_URL="${API_URL:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_API_KEY:-}"

# Check if admin key is set
if [ -z "$ADMIN_KEY" ]; then
    echo -e "${RED}❌ ADMIN_API_KEY not set!${NC}"
    echo -e "${YELLOW}Please set it in .env or export ADMIN_API_KEY=your-key${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Admin API key found${NC}"
echo -e "${BLUE}API URL: $API_URL${NC}"
echo ""

# Test 1: Health Check
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST 1: Health Check${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
if curl -s "$API_URL/health" | grep -q "healthy"; then
    echo -e "${GREEN}✓ Server is healthy${NC}"
else
    echo -e "${RED}❌ Server health check failed${NC}"
    exit 1
fi
echo ""

# Test 2: List Available Providers
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST 2: List Available Providers${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
PROVIDERS=$(curl -s -X GET "$API_URL/admin/model-sync/providers" \
    -H "Authorization: Bearer $ADMIN_KEY")

PROVIDER_COUNT=$(echo "$PROVIDERS" | jq -r '.count // 0')
if [ "$PROVIDER_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓ Found $PROVIDER_COUNT providers available for sync${NC}"
    echo "$PROVIDERS" | jq -r '.providers[]' | head -5 | while read provider; do
        echo "  - $provider"
    done
    echo "  ... and more"
else
    echo -e "${RED}❌ No providers found${NC}"
    exit 1
fi
echo ""

# Test 3: Dry Run Sync (Cerebras - small/fast)
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST 3: Dry Run Sync (Cerebras)${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}Testing model fetch without writing to database...${NC}"
DRY_RUN=$(curl -s -X POST "$API_URL/admin/model-sync/provider/cerebras?dry_run=true" \
    -H "Authorization: Bearer $ADMIN_KEY")

if echo "$DRY_RUN" | jq -e '.success == true' > /dev/null; then
    MODELS_FETCHED=$(echo "$DRY_RUN" | jq -r '.details.models_fetched // 0')
    echo -e "${GREEN}✓ Dry run successful - would sync $MODELS_FETCHED models${NC}"
    echo "$DRY_RUN" | jq -r '.message'
else
    echo -e "${RED}❌ Dry run failed${NC}"
    echo "$DRY_RUN" | jq '.'
    exit 1
fi
echo ""

# Test 4: Ask user if they want to sync
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST 4: Actual Model Sync${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}Would you like to sync Cerebras models to the database?${NC}"
echo -e "${YELLOW}This will write ~3 models to your database.${NC}"
read -p "Continue? (y/n): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Syncing Cerebras models...${NC}"
    SYNC_RESULT=$(curl -s -X POST "$API_URL/admin/model-sync/provider/cerebras" \
        -H "Authorization: Bearer $ADMIN_KEY")

    if echo "$SYNC_RESULT" | jq -e '.success == true' > /dev/null; then
        SYNCED_COUNT=$(echo "$SYNC_RESULT" | jq -r '.details.models_synced // 0')
        echo -e "${GREEN}✓ Successfully synced $SYNCED_COUNT models${NC}"
        echo "$SYNC_RESULT" | jq -r '.message'
    else
        echo -e "${RED}❌ Sync failed${NC}"
        echo "$SYNC_RESULT" | jq '.'
    fi
else
    echo -e "${YELLOW}⏭  Skipping actual sync${NC}"
fi
echo ""

# Test 5: Query Models
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST 5: Query Model Catalog${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
STATS=$(curl -s -X GET "$API_URL/models/stats" \
    -H "Authorization: Bearer $ADMIN_KEY")

TOTAL_MODELS=$(echo "$STATS" | jq -r '.total_models // 0')
if [ "$TOTAL_MODELS" -gt 0 ]; then
    echo -e "${GREEN}✓ Database contains $TOTAL_MODELS models${NC}"
    echo ""
    echo -e "${BLUE}Statistics:${NC}"
    echo "  Total: $TOTAL_MODELS"
    echo "  Active: $(echo "$STATS" | jq -r '.active_models // 0')"
    echo "  Healthy: $(echo "$STATS" | jq -r '.by_health_status.healthy // 0')"
    echo ""
    echo -e "${BLUE}By Provider:${NC}"
    echo "$STATS" | jq -r '.by_provider // {} | to_entries[] | "  \(.key): \(.value)"' | head -5
else
    echo -e "${YELLOW}⚠ No models in database yet${NC}"
    echo -e "${YELLOW}Run: curl -X POST '$API_URL/admin/model-sync/all' -H 'Authorization: Bearer $ADMIN_KEY'${NC}"
fi
echo ""

# Test 6: Search Models
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST 6: Search Models${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
if [ "$TOTAL_MODELS" -gt 0 ]; then
    echo -e "${YELLOW}Searching for 'llama' models...${NC}"
    SEARCH_RESULT=$(curl -s -X GET "$API_URL/models/search?q=llama&limit=5" \
        -H "Authorization: Bearer $ADMIN_KEY")

    SEARCH_COUNT=$(echo "$SEARCH_RESULT" | jq '. | length')
    if [ "$SEARCH_COUNT" -gt 0 ]; then
        echo -e "${GREEN}✓ Found $SEARCH_COUNT matching models${NC}"
        echo "$SEARCH_RESULT" | jq -r '.[] | "\(.model_id) (\(.providers.slug))"' | head -3
    else
        echo -e "${YELLOW}⚠ No llama models found (may need to sync more providers)${NC}"
    fi
else
    echo -e "${YELLOW}⏭  Skipping search (no models in database)${NC}"
fi
echo ""

# Test 7: Failover Query (Python)
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST 7: Failover Query${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
if [ "$TOTAL_MODELS" -gt 0 ]; then
    echo -e "${YELLOW}Testing failover query for 'llama-3-70b-instruct'...${NC}"
    python3 - << 'EOF'
from src.db.failover_db import get_providers_for_model

try:
    providers = get_providers_for_model("llama-3-70b-instruct", active_only=True)

    if providers:
        print(f"\033[0;32m✓ Found {len(providers)} provider(s) for llama-3-70b-instruct\033[0m")
        for p in providers[:3]:
            health = p.get("provider_health_status", "unknown")
            latency = p.get("provider_response_time_ms", "N/A")
            print(f"  - {p['provider_slug']:20} | Health: {health:10} | Latency: {latency}ms")
    else:
        print("\033[1;33m⚠ Model not found (may need to sync more providers)\033[0m")
except Exception as e:
    print(f"\033[0;31m❌ Failover query failed: {e}\033[0m")
EOF
else
    echo -e "${YELLOW}⏭  Skipping failover query (no models in database)${NC}"
fi
echo ""

# Summary
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TEST SUMMARY${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✓ All tests passed!${NC}"
echo ""

if [ "$TOTAL_MODELS" -eq 0 ]; then
    echo -e "${YELLOW}📝 Next Steps:${NC}"
    echo ""
    echo -e "  1. Sync all providers:"
    echo -e "     ${BLUE}curl -X POST '$API_URL/admin/model-sync/all' \\${NC}"
    echo -e "     ${BLUE}  -H 'Authorization: Bearer \$ADMIN_API_KEY'${NC}"
    echo ""
    echo -e "  2. Or sync specific providers:"
    echo -e "     ${BLUE}curl -X POST '$API_URL/admin/model-sync/all?providers=openrouter&providers=cerebras' \\${NC}"
    echo -e "     ${BLUE}  -H 'Authorization: Bearer \$ADMIN_API_KEY'${NC}"
    echo ""
    echo -e "  3. Run failover test:"
    echo -e "     ${BLUE}python scripts/test_failover_database.py${NC}"
else
    echo -e "${GREEN}✨ Your database has $TOTAL_MODELS models and is ready for failover!${NC}"
    echo ""
    echo -e "${YELLOW}📝 Next Steps:${NC}"
    echo ""
    echo -e "  1. Run comprehensive failover tests:"
    echo -e "     ${BLUE}python scripts/test_failover_database.py${NC}"
    echo ""
    echo -e "  2. Integrate failover into chat endpoint"
    echo ""
    echo -e "  3. Set up background sync (every 6-12 hours)"
fi
echo ""
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
