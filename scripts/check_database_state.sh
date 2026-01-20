#!/bin/bash

# Database State Diagnostic Script
# Checks if pricing tables exist and are accessible

echo "========================================"
echo "DATABASE STATE DIAGNOSTIC"
echo "========================================"
echo ""

# Check if SUPABASE_URL is set
if [ -z "$SUPABASE_URL" ]; then
    echo "❌ SUPABASE_URL environment variable not set"
    echo ""
    echo "Please set it first:"
    echo "  export SUPABASE_URL='your-connection-string'"
    exit 1
fi

# Check if SUPABASE_KEY is set
if [ -z "$SUPABASE_KEY" ]; then
    echo "❌ SUPABASE_KEY environment variable not set"
    echo ""
    echo "Please set it first:"
    echo "  export SUPABASE_KEY='your-service-role-key'"
    exit 1
fi

echo "✓ Environment variables configured"
echo ""

# Try to query via REST API
echo "=== Checking API Access ==="
echo ""

# Check pricing_tiers (we know this works)
echo "→ Testing API connectivity..."
response=$(curl -s -X GET "${SUPABASE_URL}/rest/v1/pricing_tiers?select=count" \
    -H "apikey: ${SUPABASE_KEY}" \
    -H "Authorization: Bearer ${SUPABASE_KEY}" \
    -H "Range: 0-0")

if [ $? -eq 0 ]; then
    echo "  ✓ API is responding"
else
    echo "  ✗ API is not responding"
    exit 1
fi

echo ""
echo "=== Checking Tables ==="
echo ""

# Function to check table
check_table() {
    local table=$1
    local response=$(curl -s -X GET "${SUPABASE_URL}/rest/v1/${table}?select=*&limit=0" \
        -H "apikey: ${SUPABASE_KEY}" \
        -H "Authorization: Bearer ${SUPABASE_KEY}" \
        -H "Prefer: count=exact" \
        2>&1)

    if echo "$response" | grep -q "PGRST205"; then
        echo "  ✗ $table: NOT FOUND (not in schema cache)"
        return 1
    elif echo "$response" | grep -q "error"; then
        echo "  ✗ $table: ERROR - $(echo $response | head -c 80)"
        return 1
    else
        # Try to get count from headers or response
        echo "  ✓ $table: ACCESSIBLE"
        return 0
    fi
}

# Check critical tables
check_table "models"
check_table "model_pricing"
check_table "providers"
check_table "pricing_tiers"

echo ""
echo "=== Recommendations ==="
echo ""

# Determine next steps based on results
if check_table "models" && check_table "model_pricing"; then
    echo "✅ Tables are accessible!"
    echo ""
    echo "Next steps:"
    echo "  1. Run verification: PYTHONPATH=. python3 scripts/verify_pricing_migration.py"
    echo "  2. Populate pricing data: See docs/PRICING_CONSOLIDATION_PLAN.md"
else
    echo "❌ Tables are not accessible"
    echo ""
    echo "This could mean:"
    echo "  A) Migrations haven't been applied to database"
    echo "  B) Schema cache needs refresh"
    echo ""
    echo "To diagnose further:"
    echo "  1. Check Supabase Dashboard → Table Editor"
    echo "     Do you see 'models', 'model_pricing', 'providers' tables?"
    echo ""
    echo "  2. If YES (tables exist):"
    echo "     - Schema cache needs refresh"
    echo "     - Run in Supabase SQL Editor:"
    echo "       SELECT pg_notify('pgrst', 'reload schema');"
    echo ""
    echo "  3. If NO (tables don't exist):"
    echo "     - Migrations haven't been applied"
    echo "     - Run: supabase db push"
    echo "     - Or apply migrations manually via SQL Editor"
fi

echo ""
echo "========================================"
