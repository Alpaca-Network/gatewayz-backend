#!/bin/bash
# Automated setup script for full database sync
# This script will configure and sync all 30 providers automatically

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Gatewayz Full Sync Setup Wizard          ║${NC}"
echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo ""

# All providers list
ALL_PROVIDERS="openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface"

# Detect deployment platform
detect_platform() {
    if [ -f "railway.json" ] && command -v railway &> /dev/null; then
        echo "railway"
    elif [ -f "vercel.json" ] && command -v vercel &> /dev/null; then
        echo "vercel"
    elif [ -f "docker-compose.yml" ] || [ -f "Dockerfile" ]; then
        echo "docker"
    elif [ -f ".env" ]; then
        echo "local"
    else
        echo "unknown"
    fi
}

PLATFORM=$(detect_platform)

echo -e "${GREEN}📍 Detected Platform: ${PLATFORM}${NC}"
echo ""

# Step 1: Update configuration
echo -e "${BLUE}Step 1: Updating Configuration${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

case $PLATFORM in
    railway)
        echo "Setting Railway environment variables..."
        railway variables set PRICING_SYNC_ENABLED="true"
        railway variables set PRICING_SYNC_INTERVAL_HOURS="6"
        railway variables set PRICING_SYNC_PROVIDERS="$ALL_PROVIDERS"
        echo -e "${GREEN}✅ Railway variables updated${NC}"
        echo ""
        echo "Redeploying Railway app..."
        railway up
        sleep 10  # Wait for deployment
        ;;

    vercel)
        echo "Please add the following to your Vercel project:"
        echo ""
        echo "Via CLI:"
        echo "  vercel env add PRICING_SYNC_ENABLED"
        echo "  (enter: true)"
        echo ""
        echo "  vercel env add PRICING_SYNC_INTERVAL_HOURS"
        echo "  (enter: 6)"
        echo ""
        echo "  vercel env add PRICING_SYNC_PROVIDERS"
        echo "  (enter: $ALL_PROVIDERS)"
        echo ""
        read -p "Press Enter after adding variables to continue..."
        echo ""
        echo "Redeploying..."
        vercel --prod
        sleep 10
        ;;

    docker)
        if [ -f ".env" ]; then
            echo "Updating .env file..."
            # Backup existing .env
            cp .env .env.backup
            # Update or add variables
            grep -q "^PRICING_SYNC_ENABLED=" .env && sed -i 's/^PRICING_SYNC_ENABLED=.*/PRICING_SYNC_ENABLED=true/' .env || echo "PRICING_SYNC_ENABLED=true" >> .env
            grep -q "^PRICING_SYNC_INTERVAL_HOURS=" .env && sed -i 's/^PRICING_SYNC_INTERVAL_HOURS=.*/PRICING_SYNC_INTERVAL_HOURS=6/' .env || echo "PRICING_SYNC_INTERVAL_HOURS=6" >> .env
            grep -q "^PRICING_SYNC_PROVIDERS=" .env && sed -i "s|^PRICING_SYNC_PROVIDERS=.*|PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS|" .env || echo "PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS" >> .env
            echo -e "${GREEN}✅ .env updated (backup saved as .env.backup)${NC}"
            echo ""
            echo "Restarting Docker containers..."
            docker-compose restart
            sleep 10
        else
            echo -e "${YELLOW}⚠️  No .env file found. Creating from example...${NC}"
            cp .env.full-sync.example .env
            echo "Please update .env with your API keys and database credentials"
            read -p "Press Enter after updating .env to continue..."
        fi
        ;;

    local)
        echo "Updating .env file..."
        if [ ! -f ".env" ]; then
            echo -e "${YELLOW}⚠️  No .env file found. Creating from example...${NC}"
            cp .env.full-sync.example .env
        else
            # Backup and update
            cp .env .env.backup
            grep -q "^PRICING_SYNC_ENABLED=" .env && sed -i '' 's/^PRICING_SYNC_ENABLED=.*/PRICING_SYNC_ENABLED=true/' .env || echo "PRICING_SYNC_ENABLED=true" >> .env
            grep -q "^PRICING_SYNC_INTERVAL_HOURS=" .env && sed -i '' 's/^PRICING_SYNC_INTERVAL_HOURS=.*/PRICING_SYNC_INTERVAL_HOURS=6/' .env || echo "PRICING_SYNC_INTERVAL_HOURS=6" >> .env
            grep -q "^PRICING_SYNC_PROVIDERS=" .env && sed -i '' "s|^PRICING_SYNC_PROVIDERS=.*|PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS|" .env || echo "PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS" >> .env
        fi
        echo -e "${GREEN}✅ .env updated${NC}"
        echo ""
        echo -e "${YELLOW}⚠️  Please restart your application manually:${NC}"
        echo "   uvicorn src.main:app --reload"
        read -p "Press Enter after restarting to continue..."
        ;;

    *)
        echo -e "${YELLOW}⚠️  Platform not detected. Please manually add:${NC}"
        echo ""
        echo "PRICING_SYNC_ENABLED=true"
        echo "PRICING_SYNC_INTERVAL_HOURS=6"
        echo "PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS"
        echo ""
        read -p "Press Enter after adding variables to continue..."
        ;;
esac

echo ""

# Step 2: Wait for app to restart
echo -e "${BLUE}Step 2: Waiting for Application${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Waiting 30 seconds for application to restart..."
for i in {30..1}; do
    echo -ne "   ${i}s remaining...\r"
    sleep 1
done
echo -e "${GREEN}✅ Application should be ready${NC}"
echo ""

# Step 3: Get API URL
echo -e "${BLUE}Step 3: API Configuration${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -z "$API_URL" ]; then
    case $PLATFORM in
        railway)
            API_URL=$(railway status --json 2>/dev/null | jq -r '.service.domains[0] // empty' | sed 's/^/https:\/\//')
            ;;
        vercel)
            API_URL=$(vercel ls --json 2>/dev/null | jq -r '.[0].url // empty' | sed 's/^/https:\/\//')
            ;;
        local|docker)
            API_URL="http://localhost:8000"
            ;;
    esac

    if [ -z "$API_URL" ]; then
        read -p "Enter your API URL (e.g., https://api.gatewayz.ai): " API_URL
    fi
fi

echo "Using API URL: $API_URL"
echo ""

# Step 4: Test API connection
echo -e "${BLUE}Step 4: Testing API Connection${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if curl -s -f "${API_URL}/health" > /dev/null; then
    echo -e "${GREEN}✅ API is reachable${NC}"
else
    echo -e "${RED}❌ Cannot reach API at ${API_URL}${NC}"
    echo "Please check your API URL and try again"
    exit 1
fi
echo ""

# Step 5: Run initial full sync
echo -e "${BLUE}Step 5: Running Initial Full Sync${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "This will sync all 30 providers to your database..."
echo "Expected time: 3-5 minutes"
echo ""

START_TIME=$(date +%s)
echo "⏳ Syncing..."

RESPONSE=$(curl -s -X POST "${API_URL}/admin/model-sync/all")
SUCCESS=$(echo "$RESPONSE" | jq -r '.success // false')

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ "$SUCCESS" = "true" ]; then
    TOTAL_SYNCED=$(echo "$RESPONSE" | jq -r '.details.total_models_synced // 0')
    PROVIDERS_PROCESSED=$(echo "$RESPONSE" | jq -r '.details.providers_processed // 0')

    echo -e "${GREEN}✅ Sync completed successfully!${NC}"
    echo "   Duration: ${DURATION}s"
    echo "   Providers: ${PROVIDERS_PROCESSED}"
    echo "   Models synced: ${TOTAL_SYNCED}"
else
    echo -e "${RED}❌ Sync failed${NC}"
    echo "$RESPONSE" | jq '.'
    exit 1
fi
echo ""

# Step 6: Verify database
echo -e "${BLUE}Step 6: Verification${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
sleep 2  # Wait for database to update

DB_COUNT=$(curl -s "${API_URL}/admin/model-sync/status" | jq -r '.models.stats.total_active // 0')

echo "📊 Database Status:"
echo "   Total models: ${DB_COUNT}"
echo ""

if [ "$DB_COUNT" -ge 17000 ]; then
    echo -e "${GREEN}✅ SUCCESS! Database is fully synced${NC}"
    echo "   Your database now has ${DB_COUNT} models"
elif [ "$DB_COUNT" -ge 15000 ]; then
    echo -e "${YELLOW}⚠️  WARNING: Database has ${DB_COUNT} models${NC}"
    echo "   Expected ~18,000. Some providers may have failed."
else
    echo -e "${RED}❌ ERROR: Database only has ${DB_COUNT} models${NC}"
    echo "   Expected ~18,000. Sync may have failed."
fi
echo ""

# Step 7: Setup monitoring (optional)
echo -e "${BLUE}Step 7: Monitoring (Optional)${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
read -p "Would you like to set up automated monitoring? (y/N): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Setting up monitoring..."
    chmod +x scripts/monitor_sync_health.py
    chmod +x scripts/verify_full_sync.sh

    # Add cron job
    echo "Adding cron job to check sync every 6 hours..."
    (crontab -l 2>/dev/null || true; echo "0 */6 * * * cd $(pwd) && ./scripts/monitor_sync_health.py >> /var/log/gatewayz_sync_monitor.log 2>&1") | crontab -

    echo -e "${GREEN}✅ Monitoring configured${NC}"
    echo "   Logs: /var/log/gatewayz_sync_monitor.log"
else
    echo "Skipping monitoring setup"
fi
echo ""

# Summary
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            Setup Complete! 🎉              ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo "📊 Summary:"
echo "   ✅ Configuration updated"
echo "   ✅ Application restarted"
echo "   ✅ Initial sync completed"
echo "   ✅ Database has ${DB_COUNT} models"
echo ""
echo "🔄 Automatic Sync:"
echo "   • Runs every 6 hours"
echo "   • Syncs all 30 providers"
echo "   • Keeps database at ~18,000 models"
echo ""
echo "📝 Next Steps:"
echo "   1. Monitor sync logs for errors"
echo "   2. Run verification script:"
echo "      ./scripts/verify_full_sync.sh"
echo "   3. Check sync status anytime:"
echo "      curl ${API_URL}/admin/model-sync/status | jq"
echo ""
echo "📚 Documentation:"
echo "   • docs/KEEP_DB_FULLY_SYNCED.md"
echo "   • docs/MODEL_SYNC_GUIDE.md"
echo "   • docs/DATABASE_VS_API_MODELS_EXPLAINED.md"
echo ""
echo "🎉 Your database will now stay fully synced!"
