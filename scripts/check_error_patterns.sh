#!/bin/bash
# Quick script to check error pattern status
# Run this after applying the migration to verify the fix

echo "======================================"
echo "Error Pattern Status Check"
echo "======================================"
echo ""

# Check if API is running locally or remotely
API_URL="${API_URL:-http://localhost:8000}"

echo "📊 Checking error monitor status..."
curl -s "$API_URL/error-monitor/autonomous/status" | python3 -m json.tool 2>/dev/null || echo "❌ API not responding"

echo ""
echo "📈 Checking error dashboard..."
curl -s "$API_URL/error-monitor/dashboard" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    summary = data.get('summary', {})
    print(f\"Total Error Patterns: {summary.get('total_patterns', 'N/A')}\")
    print(f\"Critical Errors: {summary.get('critical_errors', 'N/A')}\")
    print(f\"Fixable Errors: {summary.get('fixable_errors', 'N/A')}\")
    print(f\"Generated Fixes: {summary.get('generated_fixes', 'N/A')}\")
    print(\"\nPatterns by Category:\")
    for cat, count in summary.get('patterns_by_category', {}).items():
        print(f\"  - {cat}: {count}\")
except Exception as e:
    print(f\"Error parsing response: {e}\")
" 2>/dev/null || echo "❌ Dashboard not available"

echo ""
echo "======================================"
echo "Check the pattern count - should be <10 after fixes"
echo "======================================"
