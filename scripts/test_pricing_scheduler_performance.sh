#!/bin/bash

# Pricing Scheduler Performance Test Script
# Tests resource usage, duration, and stability of pricing sync scheduler

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
STAGING_URL="https://gatewayz-staging.up.railway.app"
RESULTS_FILE="pricing_scheduler_performance_results_$(date +%Y%m%d_%H%M%S).txt"

# Check for admin key
if [ -z "$STAGING_ADMIN_KEY" ]; then
    echo -e "${RED}Error: STAGING_ADMIN_KEY environment variable not set${NC}"
    echo "Export it with: export STAGING_ADMIN_KEY='your-key'"
    exit 1
fi

# Initialize results file
echo "Pricing Scheduler Performance Test Results" > "$RESULTS_FILE"
echo "=========================================" >> "$RESULTS_FILE"
echo "Date: $(date)" >> "$RESULTS_FILE"
echo "Environment: Staging ($STAGING_URL)" >> "$RESULTS_FILE"
echo "" >> "$RESULTS_FILE"

# Helper functions
log_section() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
    echo "" >> "$RESULTS_FILE"
    echo "=== $1 ===" >> "$RESULTS_FILE"
}

log_test() {
    echo -e "${YELLOW}$1${NC}"
    echo "$1" >> "$RESULTS_FILE"
}

log_pass() {
    echo -e "${GREEN}✓ $1${NC}"
    echo "✓ $1" >> "$RESULTS_FILE"
}

log_fail() {
    echo -e "${RED}✗ $1${NC}"
    echo "✗ $1" >> "$RESULTS_FILE"
}

log_info() {
    echo "$1"
    echo "$1" >> "$RESULTS_FILE"
}

log_warn() {
    echo -e "${YELLOW}⚠ $1${NC}"
    echo "⚠ $1" >> "$RESULTS_FILE"
}

get_metric() {
    local metric_name=$1
    local value=$(curl -s --max-time 30 -H "Authorization: Bearer $STAGING_ADMIN_KEY" "$STAGING_URL/metrics" | grep "^$metric_name" | grep -v "^#" | grep -v "_created" | awk '{print $2}' | head -1)
    echo "${value:-0}"
}

# Test 1: Baseline Resource Usage
log_section "1. Baseline Connection Pool Status"

log_test "Checking baseline connection pool metrics..."
BASELINE_POOL_SIZE=$(get_metric "connection_pool_size")
BASELINE_POOL_ACTIVE=$(get_metric "connection_pool_active_connections")
BASELINE_POOL_IDLE=$(get_metric "connection_pool_idle_connections")

log_info "Baseline Pool Size: $BASELINE_POOL_SIZE"
log_info "Baseline Active Connections: $BASELINE_POOL_ACTIVE"
log_info "Baseline Idle Connections: $BASELINE_POOL_IDLE"

sleep 2

# Test 2: Measure Sync Duration (Single Run)
log_section "2. Single Sync Duration Test"

log_test "Triggering manual sync..."
START=$(date +%s)

RESPONSE=$(curl -s --max-time 180 -w "\n%{http_code}" -X POST \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  "$STAGING_URL/admin/pricing/scheduler/trigger")

END=$(date +%s)
API_DURATION=$((END - START))

# Split response and HTTP code
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

log_info "HTTP Status Code: $HTTP_CODE"

# Check if response is valid JSON
if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | jq empty 2>/dev/null; then
    SYNC_DURATION=$(echo "$BODY" | jq -r '.duration_seconds // empty')
    SYNC_STATUS=$(echo "$BODY" | jq -r '.status // "unknown"')
    MODELS_UPDATED=$(echo "$BODY" | jq -r '.models_updated // 0')
    PROVIDERS_SYNCED=$(echo "$BODY" | jq -r '.providers_synced // 0')

    log_info "API Response Time: ${API_DURATION}s"
    log_info "Sync Duration: ${SYNC_DURATION}s"
    log_info "Sync Status: ${SYNC_STATUS}"
    log_info "Models Updated: ${MODELS_UPDATED}"
    log_info "Providers Synced: ${PROVIDERS_SYNCED}"

    # Validate sync duration
    if [ -n "$SYNC_DURATION" ] && (( $(echo "$SYNC_DURATION < 60" | bc -l 2>/dev/null || echo 0) )); then
        log_pass "Sync duration < 60s"
    else
        log_warn "Sync duration >= 60s or unavailable"
    fi

    # Validate API response time
    if [ "$API_DURATION" -lt 5 ]; then
        log_pass "API response time < 5s"
    else
        log_warn "API response time >= 5s"
    fi
else
    log_fail "Invalid response from sync trigger (HTTP $HTTP_CODE)"
    log_info "Response: $BODY"
fi

sleep 3

# Test 3: Check Connection Pool After Sync
log_section "3. Connection Pool Usage After Sync"

log_test "Checking connection pool after sync..."
AFTER_SYNC_POOL_SIZE=$(get_metric "connection_pool_size")
AFTER_SYNC_POOL_ACTIVE=$(get_metric "connection_pool_active_connections")
AFTER_SYNC_POOL_IDLE=$(get_metric "connection_pool_idle_connections")
POOL_ERRORS=$(get_metric "connection_pool_errors_total")

log_info "Pool Size: $AFTER_SYNC_POOL_SIZE"
log_info "Active Connections: $AFTER_SYNC_POOL_ACTIVE"
log_info "Idle Connections: $AFTER_SYNC_POOL_IDLE"
log_info "Pool Errors: $POOL_ERRORS"

# Check if active connections are reasonable
if [ "$AFTER_SYNC_POOL_ACTIVE" != "0" ]; then
    ACTIVE_INT=$(echo "$AFTER_SYNC_POOL_ACTIVE" | cut -d'.' -f1)
    if [ "$ACTIVE_INT" -lt 10 ]; then
        log_pass "Active connections < 10"
    else
        log_warn "Active connections >= 10"
    fi
else
    log_info "No active connections (expected after sync completes)"
fi

# Check for connection pool errors
ERRORS_INT=$(echo "$POOL_ERRORS" | cut -d'.' -f1)
if [ "$ERRORS_INT" = "0" ]; then
    log_pass "No connection pool errors"
else
    log_warn "Connection pool errors detected: $POOL_ERRORS"
fi

# Test 4: Check Database Query Performance
log_section "4. Database Query Performance"

log_test "Checking database query metrics..."
DB_QUERY_COUNT=$(get_metric "database_queries_total")
DB_QUERY_SUM=$(curl -s --max-time 30 -H "Authorization: Bearer $STAGING_ADMIN_KEY" "$STAGING_URL/metrics" | grep "^database_query_duration_seconds_sum" | awk '{print $2}' | head -1)
DB_QUERY_COUNT_VAL=$(curl -s --max-time 30 -H "Authorization: Bearer $STAGING_ADMIN_KEY" "$STAGING_URL/metrics" | grep "^database_query_duration_seconds_count" | awk '{print $2}' | head -1)

log_info "Total Database Queries: $DB_QUERY_COUNT"

if [ -n "$DB_QUERY_SUM" ] && [ -n "$DB_QUERY_COUNT_VAL" ] && [ "$DB_QUERY_COUNT_VAL" != "0" ]; then
    AVG_QUERY_TIME=$(echo "scale=4; $DB_QUERY_SUM / $DB_QUERY_COUNT_VAL" | bc 2>/dev/null || echo "N/A")
    log_info "Average Query Time: ${AVG_QUERY_TIME}s"

    # Convert to milliseconds and check
    AVG_QUERY_MS=$(echo "scale=2; $AVG_QUERY_TIME * 1000" | bc 2>/dev/null || echo "0")
    if (( $(echo "$AVG_QUERY_MS < 100" | bc -l 2>/dev/null || echo 0) )); then
        log_pass "Average query time < 100ms"
    else
        log_info "Average query time: ${AVG_QUERY_MS}ms"
    fi
else
    log_info "Database query metrics not available or no queries recorded"
fi

# Test 5: Cache Performance
log_section "5. Cache Performance"

log_test "Checking cache metrics..."
CACHE_HITS=$(get_metric "cache_hits_total")
CACHE_MISSES=$(get_metric "cache_misses_total")

log_info "Cache Hits: $CACHE_HITS"
log_info "Cache Misses: $CACHE_MISSES"

if [ "$CACHE_HITS" != "0" ] || [ "$CACHE_MISSES" != "0" ]; then
    TOTAL_CACHE=$(echo "$CACHE_HITS + $CACHE_MISSES" | bc)
    if [ "$TOTAL_CACHE" != "0" ]; then
        HIT_RATE=$(echo "scale=2; $CACHE_HITS / $TOTAL_CACHE * 100" | bc 2>/dev/null || echo "0")
        log_info "Cache Hit Rate: ${HIT_RATE}%"
    fi
fi

# Test 6: Load Test - Multiple Consecutive Syncs
log_section "6. Load Test - 5 Consecutive Syncs"

declare -a SYNC_DURATIONS
declare -a API_DURATIONS
declare -a SYNC_STATUSES

SUCCESS_COUNT=0
FAIL_COUNT=0

for i in {1..5}; do
    log_test "Sync $i/5..."

    START=$(date +%s)

    RESPONSE=$(curl -s --max-time 120 -w "\n%{http_code}" -X POST \
      -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
      "$STAGING_URL/admin/pricing/scheduler/trigger")

    END=$(date +%s)
    API_DUR=$((END - START))

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | jq empty 2>/dev/null; then
        SYNC_DUR=$(echo "$BODY" | jq -r '.duration_seconds // 0')
        STATUS=$(echo "$BODY" | jq -r '.status // "unknown"')

        SYNC_DURATIONS+=("$SYNC_DUR")
        API_DURATIONS+=("$API_DUR")
        SYNC_STATUSES+=("$STATUS")

        if [ "$STATUS" = "success" ] || [ "$STATUS" = "completed" ]; then
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
            log_info "  ✓ API Time: ${API_DUR}s, Sync Time: ${SYNC_DUR}s, Status: ${STATUS}"
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
            log_warn "  ~ API Time: ${API_DUR}s, Sync Time: ${SYNC_DUR}s, Status: ${STATUS}"
        fi
    else
        log_fail "  ✗ Sync $i failed - HTTP $HTTP_CODE"
        SYNC_DURATIONS+=("999")
        API_DURATIONS+=("999")
        SYNC_STATUSES+=("failed")
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    sleep 5
done

# Calculate statistics
TOTAL_SYNCS=${#SYNC_DURATIONS[@]}
SUM=0
VALID_COUNT=0

for dur in "${SYNC_DURATIONS[@]}"; do
    if [ "$dur" != "999" ] && [ -n "$dur" ]; then
        SUM=$(echo "$SUM + $dur" | bc)
        VALID_COUNT=$((VALID_COUNT + 1))
    fi
done

if [ "$VALID_COUNT" -gt 0 ]; then
    AVG_SYNC_DURATION=$(echo "scale=2; $SUM / $VALID_COUNT" | bc)
else
    AVG_SYNC_DURATION="N/A"
fi

log_info ""
log_info "Load Test Summary:"
log_info "  Total Syncs: $TOTAL_SYNCS"
log_info "  Successful: $SUCCESS_COUNT"
log_info "  Failed: $FAIL_COUNT"
log_info "  Average Sync Duration: ${AVG_SYNC_DURATION}s"

if [ "$SUCCESS_COUNT" -eq 5 ]; then
    log_pass "All 5 syncs completed successfully"
elif [ "$SUCCESS_COUNT" -ge 4 ]; then
    log_warn "Most syncs succeeded ($SUCCESS_COUNT/5)"
else
    log_fail "Too many failures ($FAIL_COUNT/5)"
fi

if [ "$AVG_SYNC_DURATION" != "N/A" ]; then
    if (( $(echo "$AVG_SYNC_DURATION < 30" | bc -l) )); then
        log_pass "Average sync duration < 30s (excellent)"
    elif (( $(echo "$AVG_SYNC_DURATION < 60" | bc -l) )); then
        log_pass "Average sync duration < 60s (acceptable)"
    else
        log_warn "Average sync duration >= 60s"
    fi
fi

# Test 7: Verify System Stability
log_section "7. System Stability Check"

log_test "Checking health endpoint..."
HEALTH_RESPONSE=$(curl -s --max-time 30 "$STAGING_URL/health")

if echo "$HEALTH_RESPONSE" | jq empty 2>/dev/null; then
    HEALTH_STATUS=$(echo "$HEALTH_RESPONSE" | jq -r '.status // "unknown"')
    log_info "Health Status: $HEALTH_STATUS"

    if [ "$HEALTH_STATUS" = "healthy" ] || [ "$HEALTH_STATUS" = "ok" ]; then
        log_pass "System healthy after load test"
    else
        log_warn "System status: $HEALTH_STATUS"
    fi
else
    log_fail "Health check failed - Invalid response"
fi

# Test 8: Final Connection Pool Check
log_section "8. Post-Test Connection Pool Status"

sleep 5  # Wait for connections to settle

log_test "Checking final connection pool state..."
FINAL_POOL_SIZE=$(get_metric "connection_pool_size")
FINAL_POOL_ACTIVE=$(get_metric "connection_pool_active_connections")
FINAL_POOL_IDLE=$(get_metric "connection_pool_idle_connections")
FINAL_POOL_ERRORS=$(get_metric "connection_pool_errors_total")

log_info "Final Pool Size: $FINAL_POOL_SIZE"
log_info "Final Active Connections: $FINAL_POOL_ACTIVE"
log_info "Final Idle Connections: $FINAL_POOL_IDLE"
log_info "Total Pool Errors: $FINAL_POOL_ERRORS"

# Check for connection leaks
ACTIVE_INCREASE=$((FINAL_POOL_ACTIVE - BASELINE_POOL_ACTIVE))
if [ "$ACTIVE_INCREASE" -le 2 ]; then
    log_pass "No significant connection leak detected"
else
    log_warn "Active connections increased by $ACTIVE_INCREASE"
fi

# Performance Benchmarks Summary
log_section "Performance Benchmarks Summary"

cat << EOF >> "$RESULTS_FILE"

| Metric                    | Target      | Actual              | Status |
|---------------------------|-------------|---------------------|--------|
| Sync Duration (avg)       | < 30s       | ${AVG_SYNC_DURATION}s | $(if [ "$AVG_SYNC_DURATION" != "N/A" ] && (( $(echo "$AVG_SYNC_DURATION < 30" | bc -l) )); then echo "✓ PASS"; elif [ "$AVG_SYNC_DURATION" != "N/A" ] && (( $(echo "$AVG_SYNC_DURATION < 60" | bc -l) )); then echo "~ ACCEPTABLE"; else echo "✗ FAIL"; fi) |
| Sync Duration (single)    | < 60s       | ${SYNC_DURATION}s        | $(if [ -n "$SYNC_DURATION" ] && (( $(echo "$SYNC_DURATION < 60" | bc -l 2>/dev/null || echo 0) )); then echo "✓ PASS"; else echo "~ CHECK"; fi) |
| API Response Time         | < 5s        | ${API_DURATION}s         | $(if [ "$API_DURATION" -lt 5 ]; then echo "✓ PASS"; else echo "✗ FAIL"; fi) |
| Success Rate              | 100%        | $((SUCCESS_COUNT * 20))%           | $(if [ "$SUCCESS_COUNT" -eq 5 ]; then echo "✓ PASS"; elif [ "$SUCCESS_COUNT" -ge 4 ]; then echo "~ ACCEPTABLE"; else echo "✗ FAIL"; fi) |
| Active Connections        | < 10        | ${AFTER_SYNC_POOL_ACTIVE}        | $(if [ "${AFTER_SYNC_POOL_ACTIVE%.*}" -lt 10 ]; then echo "✓ PASS"; else echo "~ CHECK"; fi) |
| Connection Pool Errors    | 0           | ${POOL_ERRORS}           | $(if [ "${POOL_ERRORS%.*}" = "0" ]; then echo "✓ PASS"; else echo "✗ FAIL"; fi) |
| System Health             | Healthy     | ${HEALTH_STATUS}        | $(if [ "$HEALTH_STATUS" = "healthy" ] || [ "$HEALTH_STATUS" = "ok" ]; then echo "✓ PASS"; else echo "✗ FAIL"; fi) |

EOF

# Additional Notes
log_section "Additional Notes"
log_info "For system-level metrics (Memory, CPU), check Railway dashboard:"
log_info "  https://railway.app/project/<project-id>"
log_info ""
log_info "For database query performance, run this SQL in Supabase:"
log_info "  SELECT query, calls, mean_time, max_time"
log_info "  FROM pg_stat_statements"
log_info "  WHERE query LIKE '%model_pricing%'"
log_info "  ORDER BY mean_time DESC LIMIT 10;"

echo ""
log_section "Test Complete!"
echo -e "${GREEN}Results saved to: ${RESULTS_FILE}${NC}"
echo ""
echo "Summary:"
echo "  Successful syncs: $SUCCESS_COUNT/5"
echo "  Average duration: ${AVG_SYNC_DURATION}s"
echo "  System health: $HEALTH_STATUS"
echo ""
echo "To view full results:"
echo "  cat $RESULTS_FILE"
