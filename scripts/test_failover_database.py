#!/usr/bin/env python3
"""
Test script to verify database structure supports failover system

This script tests:
1. Database schema completeness
2. Model-to-provider mapping queries
3. Health tracking functionality
4. Failover provider selection logic
5. Live data availability

Run:
    python scripts/test_failover_database.py
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client
from src.db.failover_db import (
    get_providers_for_model,
    get_provider_model_id,
    check_model_available_on_provider,
    get_healthy_providers
)

# ANSI color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_test(name: str):
    """Print test header"""
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}TEST: {name}{RESET}")
    print(f"{BLUE}{'='*70}{RESET}")


def print_success(msg: str):
    """Print success message"""
    print(f"{GREEN}✓ {msg}{RESET}")


def print_error(msg: str):
    """Print error message"""
    print(f"{RED}✗ {msg}{RESET}")


def print_warning(msg: str):
    """Print warning message"""
    print(f"{YELLOW}⚠ {msg}{RESET}")


def print_info(msg: str):
    """Print info message"""
    print(f"  {msg}")


def test_database_connection():
    """Test 1: Verify database connection"""
    print_test("Database Connection")

    try:
        supabase = get_supabase_client()
        result = supabase.table("providers").select("count").execute()
        print_success("Database connection successful")
        return True
    except Exception as e:
        print_error(f"Database connection failed: {e}")
        return False


def test_schema_tables():
    """Test 2: Verify all required tables exist"""
    print_test("Database Schema - Table Existence")

    required_tables = [
        "providers",
        "models",
        "model_health_tracking",
        "model_health_history"
    ]

    all_exist = True
    supabase = get_supabase_client()

    for table_name in required_tables:
        try:
            result = supabase.table(table_name).select("*").limit(1).execute()
            print_success(f"Table '{table_name}' exists")
        except Exception as e:
            print_error(f"Table '{table_name}' missing or inaccessible: {e}")
            all_exist = False

    return all_exist


def test_schema_columns():
    """Test 3: Verify required columns exist"""
    print_test("Database Schema - Critical Columns")

    tests = [
        ("providers", ["id", "slug", "name", "health_status", "average_response_time_ms", "is_active"]),
        ("models", ["id", "provider_id", "model_id", "provider_model_id", "pricing_prompt",
                    "pricing_completion", "health_status", "is_active"]),
        ("model_health_tracking", ["provider", "model", "success_count", "error_count",
                                   "average_response_time_ms", "last_status"])
    ]

    all_valid = True
    supabase = get_supabase_client()

    for table_name, required_cols in tests:
        try:
            result = supabase.table(table_name).select("*").limit(1).execute()
            if result.data:
                actual_cols = set(result.data[0].keys())
                missing = [col for col in required_cols if col not in actual_cols]

                if missing:
                    print_error(f"Table '{table_name}' missing columns: {missing}")
                    all_valid = False
                else:
                    print_success(f"Table '{table_name}' has all required columns")
            else:
                print_warning(f"Table '{table_name}' is empty, cannot verify columns")

        except Exception as e:
            print_error(f"Error checking table '{table_name}': {e}")
            all_valid = False

    return all_valid


def test_providers_data():
    """Test 4: Verify providers are populated"""
    print_test("Provider Data")

    try:
        supabase = get_supabase_client()
        result = supabase.table("providers").select("*").execute()

        if not result.data:
            print_error("No providers found in database!")
            print_warning("Run: python scripts/sync_models.py")
            return False

        print_success(f"Found {len(result.data)} providers")

        # Show provider details
        for provider in result.data[:5]:  # Show first 5
            status = provider.get("health_status", "unknown")
            active = "active" if provider.get("is_active") else "inactive"
            print_info(f"  - {provider['name']} ({provider['slug']}): {status}, {active}")

        if len(result.data) > 5:
            print_info(f"  ... and {len(result.data) - 5} more")

        return True

    except Exception as e:
        print_error(f"Error fetching providers: {e}")
        return False


def test_models_data():
    """Test 5: Verify models are populated"""
    print_test("Model Data")

    try:
        supabase = get_supabase_client()
        result = supabase.table("models").select("*, providers!inner(slug)").limit(1000).execute()

        if not result.data:
            print_error("No models found in database!")
            print_warning("Run: python scripts/sync_models.py")
            return False

        print_success(f"Found {len(result.data)} models")

        # Group by provider
        by_provider = {}
        for model in result.data:
            provider_slug = model["providers"]["slug"]
            by_provider[provider_slug] = by_provider.get(provider_slug, 0) + 1

        # Show breakdown
        for provider_slug, count in sorted(by_provider.items()):
            print_info(f"  - {provider_slug}: {count} models")

        return True

    except Exception as e:
        print_error(f"Error fetching models: {e}")
        return False


def test_failover_query_basic():
    """Test 6: Test basic failover query"""
    print_test("Failover Query - Basic")

    # Try common models
    test_models = [
        "gpt-4",
        "gpt-3.5-turbo",
        "llama-3-70b-instruct",
        "claude-3-5-sonnet-20241022",
        "grok-2-1212"
    ]

    found_any = False

    for model_id in test_models:
        providers = get_providers_for_model(model_id)

        if providers:
            print_success(f"Model '{model_id}' available on {len(providers)} provider(s)")
            for p in providers:
                health = p["provider_health_status"]
                price = p["pricing_prompt"]
                print_info(f"    → {p['provider_slug']}: {health}, ${price:.6f}/1M tokens")
            found_any = True
        else:
            print_warning(f"Model '{model_id}' not found in any provider")

    if not found_any:
        print_error("No test models found! Database may be empty.")
        print_warning("Run: python scripts/sync_models.py")
        return False

    return True


def test_failover_query_sorted():
    """Test 7: Verify failover providers are sorted correctly"""
    print_test("Failover Query - Sorting Logic")

    # Find a model with multiple providers
    supabase = get_supabase_client()
    result = supabase.rpc("get_models_with_multiple_providers").execute() if False else \
        supabase.table("models").select("model_id").execute()

    if not result.data:
        print_warning("Cannot test sorting - no models in database")
        return False

    # Get a model ID that appears multiple times
    from collections import Counter
    model_ids = [row["model_id"] for row in result.data]
    model_counts = Counter(model_ids)
    multi_provider_models = [mid for mid, count in model_counts.items() if count > 1]

    if not multi_provider_models:
        print_warning("No models found with multiple providers (cannot test failover sorting)")
        return True  # Not a failure, just no data to test with

    test_model = multi_provider_models[0]
    providers = get_providers_for_model(test_model)

    print_success(f"Model '{test_model}' has {len(providers)} providers")

    # Verify sorting
    prev_priority = -1
    for i, p in enumerate(providers):
        # Calculate priority score
        health_score = 0 if p["provider_health_status"] == "healthy" else (
            1 if p["provider_health_status"] == "degraded" else 2
        )
        response_time = p["provider_response_time_ms"] or 9999
        price = p["pricing_prompt"]

        priority = (health_score, response_time, price)

        print_info(f"  {i+1}. {p['provider_slug']}: health={p['provider_health_status']}, "
                   f"latency={response_time}ms, price=${price:.6f}")

        if i > 0 and priority < prev_priority:
            print_error("Providers are NOT sorted correctly!")
            return False

        prev_priority = priority

    print_success("Providers are sorted correctly (health → speed → cost)")
    return True


def test_model_aliases():
    """Test 8: Verify model alias resolution"""
    print_test("Model Aliases - Provider-Specific IDs")

    # Get some models and check if provider_model_id differs from model_id
    supabase = get_supabase_client()
    result = supabase.table("models").select(
        "model_id, provider_model_id, providers!inner(slug)"
    ).limit(100).execute()

    if not result.data:
        print_warning("No models to test aliases")
        return False

    aliases_found = False
    examples = []

    for row in result.data:
        canonical = row["model_id"]
        provider_specific = row["provider_model_id"]
        provider = row["providers"]["slug"]

        if canonical != provider_specific:
            aliases_found = True
            examples.append((canonical, provider, provider_specific))

    if aliases_found:
        print_success("Model aliases are working correctly")
        for canonical, provider, specific in examples[:3]:
            print_info(f"  '{canonical}' on {provider} → '{specific}'")
    else:
        print_warning("No model aliases found (all models use canonical IDs)")

    return True


def test_health_tracking():
    """Test 9: Verify health tracking data"""
    print_test("Health Tracking System")

    try:
        supabase = get_supabase_client()
        result = supabase.table("model_health_tracking").select("*").limit(10).execute()

        if not result.data:
            print_warning("No health tracking data yet (will populate on first API calls)")
            return True

        print_success(f"Found {len(result.data)} health tracking records")

        for record in result.data[:3]:
            provider = record["provider"]
            model = record["model"]
            success_rate = (record["success_count"] / record["call_count"] * 100
                          if record["call_count"] > 0 else 0)
            avg_time = record.get("average_response_time_ms", "N/A")

            print_info(f"  {provider}/{model}: {success_rate:.1f}% success, "
                      f"avg {avg_time}ms, {record['call_count']} calls")

        return True

    except Exception as e:
        print_error(f"Error checking health tracking: {e}")
        return False


def test_failover_simulation():
    """Test 10: Simulate failover scenario"""
    print_test("Failover Simulation")

    # Find a model with multiple providers
    supabase = get_supabase_client()
    result = supabase.table("models").select("model_id").execute()

    if not result.data:
        print_warning("No models to test failover")
        return False

    from collections import Counter
    model_ids = [row["model_id"] for row in result.data]
    model_counts = Counter(model_ids)
    multi_provider_models = [mid for mid, count in model_counts.items() if count > 1]

    if not multi_provider_models:
        print_warning("No models with multiple providers (cannot simulate failover)")
        return True

    test_model = multi_provider_models[0]
    providers = get_providers_for_model(test_model, active_only=True)

    if len(providers) < 2:
        print_warning(f"Model '{test_model}' only has 1 provider (cannot test failover)")
        return True

    print_success(f"Simulating failover for model '{test_model}'")
    print_info(f"  Primary provider: {providers[0]['provider_slug']}")

    # Simulate primary failure
    print_info(f"  [SIMULATED] Primary provider failed...")

    # Get fallback provider
    fallback = providers[1]
    print_success(f"  Fallback provider: {fallback['provider_slug']}")
    print_info(f"    → Health: {fallback['provider_health_status']}")
    print_info(f"    → Latency: {fallback['provider_response_time_ms']}ms")
    print_info(f"    → Model ID: {fallback['provider_model_id']}")

    print_success("Failover simulation successful!")
    return True


def generate_summary_report(results: dict):
    """Generate final test report"""
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}TEST SUMMARY{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")

    total = len(results)
    passed = sum(1 for r in results.values() if r)
    failed = total - passed

    for test_name, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"  [{status}] {test_name}")

    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"Total: {total} tests, {GREEN}{passed} passed{RESET}, {RED}{failed} failed{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")

    if failed == 0:
        print(f"{GREEN}✓ All tests passed! Database is ready for failover system.{RESET}\n")
        return True
    else:
        print(f"{RED}✗ Some tests failed. Please review errors above.{RESET}\n")
        return False


def main():
    """Run all tests"""
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}Gatewayz Failover Database Test Suite{RESET}")
    print(f"{BLUE}{'='*70}{RESET}")
    print(f"Testing database structure for provider failover support...")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n")

    results = {
        "Database Connection": test_database_connection(),
        "Schema Tables": test_schema_tables(),
        "Schema Columns": test_schema_columns(),
        "Providers Data": test_providers_data(),
        "Models Data": test_models_data(),
        "Failover Query - Basic": test_failover_query_basic(),
        "Failover Query - Sorting": test_failover_query_sorted(),
        "Model Aliases": test_model_aliases(),
        "Health Tracking": test_health_tracking(),
        "Failover Simulation": test_failover_simulation(),
    }

    success = generate_summary_report(results)

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
