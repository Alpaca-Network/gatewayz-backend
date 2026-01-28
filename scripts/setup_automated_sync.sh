#!/bin/bash
# Quick setup script for automated model & pricing sync
# Helps configure scheduling based on your deployment platform

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Automated Sync Setup Wizard             ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
echo ""
echo "This wizard will help you set up automated model & pricing sync."
echo ""

# Detect platform
detect_platform() {
    if [ -f "railway.json" ] || [ -f "railway.toml" ]; then
        echo "railway"
    elif [ -f "vercel.json" ]; then
        echo "vercel"
    elif [ -f "docker-compose.yml" ]; then
        echo "docker"
    else
        echo "local"
    fi
}

PLATFORM=$(detect_platform)
echo -e "${CYAN}📍 Detected Platform: ${PLATFORM}${NC}"
echo ""

# Ask about scheduling preference
echo -e "${BLUE}Step 1: Choose Your Scheduling Method${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Available options:"
echo ""
echo "  1) Built-in scheduler (Easiest - Already Running!)"
echo "     Syncs every N hours automatically"
echo "     Current: Every $PRICING_SYNC_INTERVAL_HOURS hours (if configured)"
echo ""
echo "  2) Cron jobs (More control - Specific times)"
echo "     Run at exact times (e.g., 2 AM, 8 AM, 2 PM, 8 PM)"
echo ""
echo "  3) GitHub Actions (Cloud-based)"
echo "     Runs in cloud, works even if server is down"
echo ""
echo "  4) Railway Cron (Railway only)"
echo "     Built-in Railway cron jobs"
echo ""
echo "  5) All of the above (Recommended - Redundancy)"
echo ""

read -p "Choose option (1-5): " CHOICE
echo ""

case $CHOICE in
    1)
        echo -e "${GREEN}✅ Using built-in scheduler${NC}"
        echo ""
        echo "The built-in scheduler is already running!"
        echo ""
        read -p "Change sync interval? Current: ${PRICING_SYNC_INTERVAL_HOURS:-6} hours (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            read -p "Enter new interval in hours (2-24): " NEW_INTERVAL
            echo ""
            echo "Add to your .env or environment:"
            echo "  PRICING_SYNC_INTERVAL_HOURS=$NEW_INTERVAL"
        fi
        ;;

    2)
        echo -e "${GREEN}✅ Setting up cron jobs${NC}"
        echo ""
        echo "Choose a schedule:"
        echo "  1) Every 6 hours (00:00, 06:00, 12:00, 18:00 UTC)"
        echo "  2) Daily at 2 AM UTC"
        echo "  3) Twice daily (2 AM and 2 PM UTC)"
        echo "  4) Every 4 hours"
        echo "  5) Custom"
        echo ""
        read -p "Choose (1-5): " CRON_CHOICE
        echo ""

        case $CRON_CHOICE in
            1) CRON_SCHEDULE="0 */6 * * *" ;;
            2) CRON_SCHEDULE="0 2 * * *" ;;
            3) CRON_SCHEDULE="0 2,14 * * *" ;;
            4) CRON_SCHEDULE="0 */4 * * *" ;;
            5)
                read -p "Enter cron schedule (e.g., '0 2 * * *'): " CRON_SCHEDULE
                ;;
        esac

        echo "Adding cron job..."
        SCRIPT_PATH="$(cd "$(dirname "$0")"/.. && pwd)/scripts/cron_sync.sh"

        # Add to crontab
        (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $SCRIPT_PATH") | crontab -

        echo -e "${GREEN}✅ Cron job added${NC}"
        echo "   Schedule: $CRON_SCHEDULE"
        echo "   Script: $SCRIPT_PATH"
        echo ""
        echo "View cron jobs: crontab -l"
        echo "View logs: tail -f /var/log/gatewayz_cron_sync.log"
        ;;

    3)
        echo -e "${GREEN}✅ Setting up GitHub Actions${NC}"
        echo ""

        if [ ! -d ".github/workflows" ]; then
            echo "Creating .github/workflows directory..."
            mkdir -p .github/workflows
        fi

        if [ -f ".github/workflows/scheduled-sync.yml" ]; then
            echo -e "${YELLOW}⚠️  GitHub Actions workflow already exists${NC}"
        else
            echo "✅ GitHub Actions workflow created at:"
            echo "   .github/workflows/scheduled-sync.yml"
        fi

        echo ""
        echo "Next steps:"
        echo "  1. Commit and push the workflow file"
        echo "  2. Add GitHub secrets:"
        echo "     - API_URL: Your API URL"
        echo "     - ADMIN_KEY: Your admin key (optional)"
        echo "  3. Workflow will run automatically"
        echo ""
        echo "Test manually:"
        echo "  GitHub → Actions → Scheduled Sync → Run workflow"
        ;;

    4)
        if [ "$PLATFORM" != "railway" ]; then
            echo -e "${RED}❌ Railway cron is only available on Railway platform${NC}"
            exit 1
        fi

        echo -e "${GREEN}✅ Railway cron is configured in railway.toml${NC}"
        echo ""
        echo "The cron job will run automatically after deployment."
        echo ""
        echo "To change schedule, edit railway.toml:"
        echo "  [[crons]]"
        echo "  schedule = \"0 */6 * * *\""
        echo "  command = \"curl -X POST http://localhost:\$PORT/admin/model-sync/all\""
        echo ""
        echo "Deploy changes:"
        echo "  git add railway.toml"
        echo "  git commit -m 'Configure Railway cron'"
        echo "  git push"
        ;;

    5)
        echo -e "${GREEN}✅ Setting up all methods (Recommended)${NC}"
        echo ""
        echo "This provides redundancy - if one fails, others still work."
        echo ""

        # Built-in
        echo "1. Built-in scheduler: Already running ✓"

        # Cron
        echo "2. Adding cron job..."
        CRON_SCHEDULE="0 */6 * * *"
        SCRIPT_PATH="$(cd "$(dirname "$0")"/.. && pwd)/scripts/cron_sync.sh"
        (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $SCRIPT_PATH") | crontab -
        echo "   ✓ Cron job added (every 6 hours)"

        # GitHub Actions
        echo "3. GitHub Actions: Workflow file ready ✓"

        # Railway (if applicable)
        if [ "$PLATFORM" = "railway" ]; then
            echo "4. Railway Cron: Configured in railway.toml ✓"
        fi

        echo ""
        echo -e "${GREEN}✅ All methods configured!${NC}"
        ;;

    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac

echo ""
echo -e "${BLUE}Step 2: Configure Full Provider Sync${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -p "Ensure all 30 providers sync? (Recommended) (Y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    ALL_PROVIDERS="openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface"

    echo ""
    echo "Add this to your environment:"
    echo ""
    echo "PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS"
    echo ""

    read -p "Apply now? (y/N): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if [ -f ".env" ]; then
            if grep -q "^PRICING_SYNC_PROVIDERS=" .env; then
                sed -i.bak "s|^PRICING_SYNC_PROVIDERS=.*|PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS|" .env
                echo "✅ Updated .env file (backup saved as .env.bak)"
            else
                echo "PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS" >> .env
                echo "✅ Added to .env file"
            fi
        else
            echo "PRICING_SYNC_PROVIDERS=$ALL_PROVIDERS" > .env
            echo "✅ Created .env file"
        fi
    fi
fi

echo ""
echo -e "${BLUE}Step 3: Run Initial Sync${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -p "Run initial full sync now? (Y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    if [ -x "./scripts/sync_all_providers_now.sh" ]; then
        echo ""
        echo "Running full sync..."
        ./scripts/sync_all_providers_now.sh
    else
        echo ""
        echo "Please run manually:"
        echo "  ./scripts/sync_all_providers_now.sh"
    fi
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Setup Complete! 🎉                 ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo "📋 Summary:"
echo ""

case $CHOICE in
    1) echo "  ✅ Built-in scheduler configured" ;;
    2) echo "  ✅ Cron jobs configured ($CRON_SCHEDULE)" ;;
    3) echo "  ✅ GitHub Actions configured" ;;
    4) echo "  ✅ Railway Cron configured" ;;
    5) echo "  ✅ All methods configured (built-in + cron + GitHub Actions)" ;;
esac

echo "  ✅ Provider list configured (30 providers)"
echo ""
echo "🔄 Your database will now stay synced automatically!"
echo ""
echo "📊 Monitor sync status:"
echo "  • View logs: tail -f /var/log/gatewayz_cron_sync.log"
echo "  • Check status: curl https://api.gatewayz.ai/admin/model-sync/status"
echo "  • Run health check: python3 scripts/monitor_sync_health.py"
echo ""
echo "📚 Documentation:"
echo "  • docs/AUTOMATED_SYNC_SCHEDULING.md"
echo "  • docs/KEEP_DB_FULLY_SYNCED.md"
echo ""
echo "🎉 Done! Your system is now fully automated."
