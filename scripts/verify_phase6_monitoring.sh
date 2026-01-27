#!/bin/bash
# Phase 6 Monitoring Infrastructure Verification Script
# Tests all monitoring components for the pricing sync scheduler

# Don't exit on error - we want to run all tests
# set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
STAGING_URL="${STAGING_URL:-https://gatewayz-staging.up.railway.app}"
PRODUCTION_URL="${PRODUCTION_URL:-https://api.gatewayz.ai}"
ADMIN_API_KEY="${ADMIN_KEY:-gw_live_wTfpLJ5VB28qMXpOAhr7Uw}"

# Test counters
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Helper functions
print_header() {
    echo -e "\n${BLUE}============================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
    ((PASSED_TESTS++))
    ((TOTAL_TESTS++))
}

print_failure() {
    echo -e "${RED}❌ $1${NC}"
    ((FAILED_TESTS++))
    ((TOTAL_TESTS++))
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Test 1: Validate monitoring files exist
test_monitoring_files() {
    print_header "Test 1: Monitoring Files Validation"

    # Check alert rules
    if [ -f "monitoring/prometheus/pricing_sync_alerts.yml" ]; then
        print_success "Prometheus alert rules file exists"
    else
        print_failure "Prometheus alert rules file missing"
    fi

    # Check Grafana dashboards
    if [ -f "monitoring/grafana/pricing_sync_scheduler_health.json" ]; then
        print_success "Health dashboard JSON exists"
    else
        print_failure "Health dashboard JSON missing"
    fi

    if [ -f "monitoring/grafana/pricing_sync_system_impact.json" ]; then
        print_success "System impact dashboard JSON exists"
    else
        print_failure "System impact dashboard JSON missing"
    fi

    # Check runbooks
    local runbook_count=$(ls docs/runbooks/pricing_sync_*.md 2>/dev/null | wc -l)
    if [ "$runbook_count" -ge 3 ]; then
        print_success "Found $runbook_count runbooks"
    else
        print_failure "Expected at least 3 runbooks, found $runbook_count"
    fi

    # Check setup guide
    if [ -f "docs/PHASE_6_MONITORING_SETUP_GUIDE.md" ]; then
        print_success "Setup guide exists"
    else
        print_failure "Setup guide missing"
    fi
}

# Test 2: Validate YAML/JSON syntax
test_file_syntax() {
    print_header "Test 2: File Syntax Validation"

    # Validate alert rules YAML
    if python3 -c "import yaml; yaml.safe_load(open('monitoring/prometheus/pricing_sync_alerts.yml'))" 2>/dev/null; then
        print_success "Alert rules YAML syntax is valid"
    else
        print_failure "Alert rules YAML syntax is invalid"
    fi

    # Validate dashboard JSONs
    if python3 -c "import json; json.load(open('monitoring/grafana/pricing_sync_scheduler_health.json'))" 2>/dev/null; then
        print_success "Health dashboard JSON syntax is valid"
    else
        print_failure "Health dashboard JSON syntax is invalid"
    fi

    if python3 -c "import json; json.load(open('monitoring/grafana/pricing_sync_system_impact.json'))" 2>/dev/null; then
        print_success "System impact dashboard JSON syntax is valid"
    else
        print_failure "System impact dashboard JSON syntax is invalid"
    fi
}

# Test 3: Test metrics endpoint
test_metrics_endpoint() {
    print_header "Test 3: Metrics Endpoint Verification"

    local url="$1"
    local env="$2"

    print_info "Testing $env environment: $url"

    # Test metrics endpoint accessibility
    local http_code=$(curl -s -o /dev/null -w "%{http_code}" "$url/metrics")

    if [ "$http_code" = "200" ] || [ "$http_code" = "401" ]; then
        print_success "Metrics endpoint is accessible (HTTP $http_code)"
    else
        print_failure "Metrics endpoint returned HTTP $http_code"
        return
    fi

    # Test for pricing metrics
    local metrics_output=$(curl -s "$url/metrics" 2>/dev/null | grep "^pricing_" | head -5)

    if [ -n "$metrics_output" ]; then
        print_success "Pricing sync metrics are being exposed"
        print_info "Sample metrics:"
        echo "$metrics_output" | while read line; do
            echo "    $line"
        done
    else
        print_warning "No pricing sync metrics found (scheduler may be disabled)"
    fi
}

# Test 4: Test scheduler status endpoint
test_scheduler_status() {
    print_header "Test 4: Scheduler Status Endpoint"

    local url="$1"
    local env="$2"

    print_info "Testing $env environment: $url"

    if [ -z "$ADMIN_API_KEY" ]; then
        print_warning "ADMIN_API_KEY not set, skipping scheduler status test"
        return
    fi

    local response=$(curl -s -H "Authorization: Bearer $ADMIN_API_KEY" "$url/admin/pricing/scheduler/status")

    if echo "$response" | python3 -c "import sys, json; json.load(sys.stdin)" 2>/dev/null; then
        print_success "Scheduler status endpoint returns valid JSON"

        local enabled=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('scheduler', {}).get('enabled', 'unknown'))")
        local running=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('scheduler', {}).get('running', 'unknown'))")

        print_info "Scheduler enabled: $enabled"
        print_info "Scheduler running: $running"
    else
        print_failure "Scheduler status endpoint returned invalid JSON"
    fi
}

# Test 5: Validate alert rule structure
test_alert_rules_structure() {
    print_header "Test 5: Alert Rules Structure Validation"

    python3 << 'EOF'
import yaml
import sys

with open('monitoring/prometheus/pricing_sync_alerts.yml', 'r') as f:
    alert_rules = yaml.safe_load(f)

errors = []
warnings = []

# Check required fields
if 'groups' not in alert_rules:
    errors.append("Missing 'groups' key in alert rules")
    sys.exit(1)

group = alert_rules['groups'][0]
rules = group.get('rules', [])

print(f"Found {len(rules)} alert rules")

critical_count = 0
warning_count = 0
info_count = 0

for rule in rules:
    alert_name = rule.get('alert', 'Unknown')

    # Check required fields
    if 'expr' not in rule:
        errors.append(f"{alert_name}: Missing 'expr' field")
    if 'labels' not in rule:
        errors.append(f"{alert_name}: Missing 'labels' field")
    if 'annotations' not in rule:
        errors.append(f"{alert_name}: Missing 'annotations' field")

    # Check severity
    severity = rule.get('labels', {}).get('severity', None)
    if not severity:
        warnings.append(f"{alert_name}: Missing severity label")
    else:
        if severity == 'critical':
            critical_count += 1
        elif severity == 'warning':
            warning_count += 1
        elif severity == 'info':
            info_count += 1

    # Check annotations
    annotations = rule.get('annotations', {})
    if 'summary' not in annotations:
        warnings.append(f"{alert_name}: Missing summary annotation")
    if 'description' not in annotations:
        warnings.append(f"{alert_name}: Missing description annotation")

print(f"Critical alerts: {critical_count}")
print(f"Warning alerts: {warning_count}")
print(f"Info alerts: {info_count}")

if errors:
    print("\nErrors:")
    for err in errors:
        print(f"  ❌ {err}")
    sys.exit(1)

if warnings:
    print("\nWarnings:")
    for warn in warnings:
        print(f"  ⚠️  {warn}")

sys.exit(0)
EOF

    if [ $? -eq 0 ]; then
        print_success "Alert rules structure is valid"
    else
        print_failure "Alert rules structure has errors"
    fi
}

# Test 6: Validate dashboard structure
test_dashboard_structure() {
    print_header "Test 6: Dashboard Structure Validation"

    python3 << 'EOF'
import json
import sys

dashboards = [
    'monitoring/grafana/pricing_sync_scheduler_health.json',
    'monitoring/grafana/pricing_sync_system_impact.json'
]

total_errors = 0

for dashboard_path in dashboards:
    with open(dashboard_path, 'r') as f:
        dashboard = json.load(f)

    errors = []

    # Check structure
    if 'dashboard' not in dashboard:
        errors.append(f"{dashboard_path}: Missing 'dashboard' key")
        continue

    dash = dashboard['dashboard']

    # Check required fields
    if 'title' not in dash:
        errors.append("Missing 'title' field")
    if 'panels' not in dash:
        errors.append("Missing 'panels' field")
    else:
        panels = dash['panels']
        print(f"{dash.get('title', 'Unknown')}: {len(panels)} panels")

        # Check panels have queries
        panels_with_targets = sum(1 for p in panels if 'targets' in p and len(p['targets']) > 0)
        print(f"  Panels with targets: {panels_with_targets}/{len(panels)}")

    if errors:
        print(f"\nErrors in {dashboard_path}:")
        for err in errors:
            print(f"  ❌ {err}")
        total_errors += len(errors)

sys.exit(1 if total_errors > 0 else 0)
EOF

    if [ $? -eq 0 ]; then
        print_success "Dashboard structure is valid"
    else
        print_failure "Dashboard structure has errors"
    fi
}

# Test 7: Check runbook accessibility
test_runbooks() {
    print_header "Test 7: Runbook Validation"

    local runbooks=(
        "docs/runbooks/pricing_sync_scheduler_stopped.md"
        "docs/runbooks/pricing_sync_high_error_rate.md"
        "docs/runbooks/pricing_sync_slow_performance.md"
    )

    for runbook in "${runbooks[@]}"; do
        if [ -f "$runbook" ]; then
            local word_count=$(wc -w < "$runbook")
            print_success "$(basename $runbook) exists ($word_count words)"
        else
            print_failure "$(basename $runbook) not found"
        fi
    done
}

# Main execution
main() {
    print_header "Phase 6 Monitoring Infrastructure Verification"

    echo "Starting comprehensive monitoring verification..."
    echo ""

    # Run all tests
    test_monitoring_files
    test_file_syntax
    test_alert_rules_structure
    test_dashboard_structure
    test_runbooks

    # Test staging environment
    if [ -n "$STAGING_URL" ]; then
        test_metrics_endpoint "$STAGING_URL" "Staging"
        test_scheduler_status "$STAGING_URL" "Staging"
    fi

    # Test production environment (optional)
    # Uncomment to test production
    # if [ -n "$PRODUCTION_URL" ]; then
    #     test_metrics_endpoint "$PRODUCTION_URL" "Production"
    #     test_scheduler_status "$PRODUCTION_URL" "Production"
    # fi

    # Print summary
    print_header "Test Summary"

    echo "Total tests run: $TOTAL_TESTS"
    echo -e "${GREEN}Passed: $PASSED_TESTS${NC}"
    echo -e "${RED}Failed: $FAILED_TESTS${NC}"

    local pass_rate=0
    if [ $TOTAL_TESTS -gt 0 ]; then
        pass_rate=$((PASSED_TESTS * 100 / TOTAL_TESTS))
    fi
    echo "Pass rate: $pass_rate%"

    echo ""

    if [ $FAILED_TESTS -eq 0 ]; then
        print_success "All tests passed! Monitoring infrastructure is ready for deployment."
        exit 0
    else
        print_failure "Some tests failed. Please review and fix issues before deployment."
        exit 1
    fi
}

# Run main function
main
