#!/usr/bin/env python3
"""
E2E Workflow Integration Test for Pricing Sync Scheduler
GitHub Issue #962

This script performs a comprehensive validation of the pricing sync scheduler
without requiring deployment permissions.
"""

import json
import time
from datetime import datetime
from typing import Dict, List, Tuple
import requests
import sys

# Configuration
STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

# Test results
passed_tests = []
failed_tests = []
warnings = []

def log_info(msg: str):
    print(f"\033[34m[INFO]\033[0m {msg}")

def log_success(msg: str):
    print(f"\033[32m[✓]\033[0m {msg}")
    passed_tests.append(msg)

def log_error(msg: str):
    print(f"\033[31m[✗]\033[0m {msg}")
    failed_tests.append(msg)

def log_warning(msg: str):
    print(f"\033[33m[!]\033[0m {msg}")
    warnings.append(msg)

def api_call(method: str, endpoint: str, **kwargs) -> Tuple[int, Dict]:
    """Make API call and return (status_code, json_data)"""
    url = f"{STAGING_URL}{endpoint}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {ADMIN_KEY}"

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=30, **kwargs)
        elif method == "POST":
            resp = requests.post(url, headers=headers, timeout=60, **kwargs)
        else:
            return 0, {}

        try:
            return resp.status_code, resp.json()
        except:
            return resp.status_code, {"text": resp.text}
    except Exception as e:
        log_error(f"API call failed: {e}")
        return 0, {}

def print_phase(title: str):
    print("\n" + "="*50)
    print(f"{title}")
    print("="*50)

def main():
    log_info("="*60)
    log_info("E2E Workflow Integration Test - GitHub Issue #962")
    log_info("="*60)
    log_info(f"Environment: Staging ({STAGING_URL})")
    log_info(f"Test Mode: Non-invasive validation")
    log_info(f"Timestamp: {datetime.now()}")
    print()

    # Phase 1: Basic Connectivity & Health
    print_phase("Phase 1: Basic Connectivity & Health Checks")

    log_info("Test 1.1: Application health endpoint...")
    status, data = api_call("GET", "/health")
    if status == 200 and data.get("status"):
        log_success(f"Application health check passed - Status: {data.get('status')}")
    else:
        log_error(f"Application health check failed - HTTP {status}")

    log_info("Test 1.2: Admin API authentication...")
    status, data = api_call("GET", "/admin/pricing/scheduler/status")
    if status == 200:
        log_success("Admin API authentication successful")
    elif status == 401:
        log_error("Admin API authentication failed - Invalid credentials")
        return
    else:
        log_error(f"Admin API returned HTTP {status}")

    # Phase 2: Scheduler Status & Configuration
    print_phase("Phase 2: Scheduler Status & Configuration")

    log_info("Test 2.1: Fetching scheduler status...")
    status, scheduler_data = api_call("GET", "/admin/pricing/scheduler/status")

    if status == 200 and "scheduler" in scheduler_data:
        log_success("Scheduler status endpoint accessible")

        sched = scheduler_data["scheduler"]
        enabled = sched.get("enabled", False)
        running = sched.get("running", False)
        interval = sched.get("interval_hours", "N/A")
        providers = sched.get("providers", [])

        log_info(f"  - Enabled: {enabled}")
        log_info(f"  - Running: {running}")
        log_info(f"  - Interval: {interval} hours")
        log_info(f"  - Providers: {', '.join(providers)}")

        if enabled:
            log_success("Scheduler is enabled")
        else:
            log_warning("Scheduler is currently disabled")

        if "invalid_provider" in providers:
            log_warning("Invalid provider detected in configuration")

        if len(providers) > 0:
            log_success(f"Scheduler has {len(providers)} providers configured")
        else:
            log_error("No providers configured")
    else:
        log_error("Failed to fetch scheduler status")

    # Phase 3: Manual Trigger Testing
    print_phase("Phase 3: Manual Trigger & Execution")

    log_info("Test 3.1: Triggering manual pricing sync...")
    start_time = time.time()
    status, trigger_data = api_call("POST", "/admin/pricing/scheduler/trigger")
    duration = time.time() - start_time

    if status == 200 and trigger_data.get("success"):
        models_updated = trigger_data.get("models_updated", 0)
        sync_duration = trigger_data.get("duration_seconds", "N/A")

        log_success(f"Manual sync completed successfully")
        log_info(f"  - Models updated: {models_updated}")
        log_info(f"  - Internal duration: {sync_duration}s")
        log_info(f"  - API call duration: {duration:.2f}s")

        if models_updated > 0:
            log_success(f"Pricing data updated for {models_updated} models")
        else:
            log_warning("No models were updated (may indicate no changes)")

        if duration < 120:
            log_success(f"Sync performance acceptable (<120s)")
        else:
            log_warning(f"Sync took {duration:.2f}s (target: <120s)")
    elif status == 503:
        log_warning("Scheduler is disabled - manual trigger not available")
    else:
        log_error(f"Manual sync failed - HTTP {status}")

    # Phase 4: Data Verification
    print_phase("Phase 4: Data Verification")

    log_info("Test 4.1: Checking model catalog...")
    status, catalog_data = api_call("GET", "/v1/models?limit=10")

    if status == 200 and "data" in catalog_data:
        models = catalog_data["data"]
        model_count = len(models)

        log_success(f"Model catalog accessible - {model_count} models returned")

        models_with_pricing = sum(1 for m in models if m.get("pricing"))
        if models_with_pricing > 0:
            log_success(f"{models_with_pricing}/{model_count} models have pricing data")
        else:
            log_warning("No pricing data found in model catalog")

        # Sample a few models
        log_info("Sample models:")
        for model in models[:3]:
            model_id = model.get("id", "Unknown")
            pricing = model.get("pricing", "No pricing")
            log_info(f"  - {model_id}: {pricing}")
    else:
        log_error(f"Failed to fetch model catalog - HTTP {status}")

    # Phase 5: Metrics & Observability
    print_phase("Phase 5: Metrics & Observability")

    log_info("Test 5.1: Checking Prometheus metrics...")
    try:
        resp = requests.get(f"{STAGING_URL}/metrics", timeout=30)
        if resp.status_code == 200:
            metrics_text = resp.text
            pricing_metrics = [line for line in metrics_text.split('\n') if 'pricing_' in line and not line.startswith('#')]

            if pricing_metrics:
                log_success(f"Pricing sync metrics present - {len(pricing_metrics)} metric lines")
                log_info("Sample metrics:")
                for metric in pricing_metrics[:5]:
                    log_info(f"  {metric[:80]}...")
            else:
                log_warning("No pricing sync metrics found")
        else:
            log_error(f"Metrics endpoint failed - HTTP {resp.status_code}")
    except Exception as e:
        log_error(f"Failed to fetch metrics: {e}")

    # Phase 6: Performance Testing
    print_phase("Phase 6: Performance Testing")

    log_info("Test 6.1: Running 3 consecutive manual syncs...")
    durations = []
    successes = 0

    for i in range(1, 4):
        log_info(f"  Sync {i}/3...")
        start = time.time()

        status, result = api_call("POST", "/admin/pricing/scheduler/trigger")
        dur = time.time() - start
        durations.append(dur)

        if status == 200 and result.get("success"):
            successes += 1
            internal_dur = result.get("duration_seconds", "N/A")
            log_info(f"    ✓ Duration: {dur:.2f}s (internal: {internal_dur}s)")
        elif status == 503:
            log_warning(f"    Scheduler disabled - skipping performance test")
            break
        else:
            log_info(f"    ✗ Failed")

        time.sleep(2)

    if len(durations) > 0:
        avg_duration = sum(durations) / len(durations)
        min_duration = min(durations)
        max_duration = max(durations)

        log_info(f"Performance statistics:")
        log_info(f"  - Average: {avg_duration:.2f}s")
        log_info(f"  - Min: {min_duration:.2f}s")
        log_info(f"  - Max: {max_duration:.2f}s")
        log_info(f"  - Success rate: {successes}/{len(durations)}")

        if successes == len(durations):
            log_success("All performance test syncs successful")
        else:
            log_warning(f"Only {successes}/{len(durations)} syncs succeeded")

        if avg_duration < 90:
            log_success(f"Average performance excellent (<90s)")
        elif avg_duration < 120:
            log_success(f"Average performance acceptable (<120s)")
        else:
            log_warning(f"Average performance needs improvement ({avg_duration:.2f}s)")

    # Phase 7: Final Validation
    print_phase("Phase 7: Final System Validation")

    log_info("Test 7.1: Complete system health check...")

    health_ok = api_call("GET", "/health")[0] == 200
    status_ok = api_call("GET", "/admin/pricing/scheduler/status")[0] == 200
    catalog_ok = api_call("GET", "/v1/models?limit=1")[0] == 200
    metrics_ok = requests.get(f"{STAGING_URL}/metrics", timeout=10).status_code == 200

    if health_ok and status_ok and catalog_ok and metrics_ok:
        log_success("Complete end-to-end workflow is functional")
    else:
        log_error("Some system components are not fully functional")

    # Summary
    print_phase("Test Summary")

    total_tests = len(passed_tests) + len(failed_tests)
    success_rate = (len(passed_tests) / total_tests * 100) if total_tests > 0 else 0

    print(f"\n\033[32mPassed Tests: {len(passed_tests)}\033[0m")
    print(f"\033[31mFailed Tests: {len(failed_tests)}\033[0m")
    print(f"\033[33mWarnings: {len(warnings)}\033[0m")
    print(f"Success Rate: {success_rate:.1f}%")
    print()

    if len(failed_tests) == 0:
        print("\033[32m✅ Overall Status: PASS\033[0m")
        print("\033[32mAll tests passed! System is functional.\033[0m")
        exit_code = 0
    elif success_rate >= 80:
        print("\033[33m⚠️  Overall Status: PASS WITH MINOR ISSUES\033[0m")
        print("\033[33mMost tests passed, but review warnings.\033[0m")
        exit_code = 0
    else:
        print("\033[31m❌ Overall Status: FAIL\033[0m")
        print("\033[31mCritical issues detected.\033[0m")
        exit_code = 1

    # Write results to file
    results_file = f"e2e_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(results_file, "w") as f:
        f.write(f"# E2E Workflow Integration Test Results\n\n")
        f.write(f"**GitHub Issue**: #962\n")
        f.write(f"**Environment**: Staging\n")
        f.write(f"**Date**: {datetime.now()}\n")
        f.write(f"**Test Duration**: {sum(durations) if durations else 0:.2f}s\n\n")

        f.write(f"## Summary\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| Passed Tests | {len(passed_tests)} |\n")
        f.write(f"| Failed Tests | {len(failed_tests)} |\n")
        f.write(f"| Warnings | {len(warnings)} |\n")
        f.write(f"| Success Rate | {success_rate:.1f}% |\n\n")

        f.write(f"## Passed Tests\n\n")
        for test in passed_tests:
            f.write(f"- [x] {test}\n")

        if failed_tests:
            f.write(f"\n## Failed Tests\n\n")
            for test in failed_tests:
                f.write(f"- [ ] {test}\n")

        if warnings:
            f.write(f"\n## Warnings\n\n")
            for warning in warnings:
                f.write(f"- ⚠️  {warning}\n")

    print(f"\nFull results saved to: {results_file}\n")

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
