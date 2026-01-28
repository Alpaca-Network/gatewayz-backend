#!/bin/bash
# Automated cron sync script for Gatewayz
# Runs full model and pricing sync and logs results
# Usage: Add to crontab with desired schedule

set -e

# Configuration
API_URL="${API_URL:-https://api.gatewayz.ai}"
LOG_FILE="${LOG_FILE:-/var/log/gatewayz_cron_sync.log}"
MAX_LOG_SIZE=10485760  # 10MB

# Rotate log if too large
if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null) -gt $MAX_LOG_SIZE ]; then
    mv "$LOG_FILE" "${LOG_FILE}.old"
fi

# Log header
{
    echo "========================================"
    echo "Gatewayz Automated Sync"
    echo "Started: $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
    echo "API URL: $API_URL"
    echo "========================================"
} >> "$LOG_FILE" 2>&1

# Function to log with timestamp
log() {
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S")] $1" >> "$LOG_FILE" 2>&1
}

# Check API health
log "Checking API health..."
if ! curl -s -f "${API_URL}/health" > /dev/null 2>&1; then
    log "❌ API not reachable at ${API_URL}"
    log "Sync aborted"
    exit 1
fi
log "✅ API is healthy"

# Run full model sync
log "Starting full model sync..."
START_TIME=$(date +%s)

RESPONSE=$(curl -s -X POST "${API_URL}/admin/model-sync/all" 2>&1)
CURL_EXIT=$?

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ $CURL_EXIT -eq 0 ]; then
    # Check if response is valid JSON
    if echo "$RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
        SUCCESS=$(echo "$RESPONSE" | jq -r '.success')

        if [ "$SUCCESS" = "true" ]; then
            MODELS_SYNCED=$(echo "$RESPONSE" | jq -r '.details.total_models_synced // 0')
            PROVIDERS=$(echo "$RESPONSE" | jq -r '.details.providers_processed // 0')

            log "✅ Model sync completed successfully"
            log "   Duration: ${DURATION}s"
            log "   Providers: $PROVIDERS"
            log "   Models synced: $MODELS_SYNCED"
        else
            ERROR=$(echo "$RESPONSE" | jq -r '.details.error // "Unknown error"')
            log "❌ Model sync failed: $ERROR"
            exit 1
        fi
    else
        log "❌ Invalid JSON response from API"
        log "Response: $RESPONSE"
        exit 1
    fi
else
    log "❌ Curl failed with exit code $CURL_EXIT"
    log "Response: $RESPONSE"
    exit 1
fi

# Get current database count
log "Verifying database..."
DB_STATUS=$(curl -s "${API_URL}/admin/model-sync/status" 2>&1)
if echo "$DB_STATUS" | jq -e '.models.stats.total_active' > /dev/null 2>&1; then
    DB_COUNT=$(echo "$DB_STATUS" | jq -r '.models.stats.total_active')
    log "   Database models: $DB_COUNT"

    if [ "$DB_COUNT" -ge 17000 ]; then
        log "✅ Database is fully synced"
    elif [ "$DB_COUNT" -ge 15000 ]; then
        log "⚠️  Database sync is partial (${DB_COUNT} models)"
    else
        log "⚠️  Database needs attention (only ${DB_COUNT} models)"
    fi
else
    log "⚠️  Could not retrieve database status"
fi

# Success
{
    echo "Completed: $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
    echo "========================================"
    echo ""
} >> "$LOG_FILE" 2>&1

exit 0
