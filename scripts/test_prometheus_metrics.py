#!/usr/bin/env python3
"""
Test script to verify Prometheus metrics implementation for pricing sync.

This script:
1. Checks that all pricing sync metrics are defined
2. Verifies metric helper functions are accessible
3. Tests that metrics can be recorded
4. Validates metrics endpoint returns pricing sync metrics
"""

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_metrics_definitions():
    """Test that all pricing sync metrics are defined."""
    print("=" * 60)
    print("TEST 1: Metrics Definitions")
    print("=" * 60)

    try:
        from src.services.prometheus_metrics import (
            pricing_sync_duration_seconds,
            pricing_sync_total,
            pricing_sync_models_updated_total,
            pricing_sync_models_skipped_total,
            pricing_sync_errors_total,
            pricing_sync_last_success_timestamp,
            pricing_sync_job_duration_seconds,
            pricing_sync_job_queue_size,
            pricing_sync_models_fetched_total,
            pricing_sync_price_changes_total,
        )

        print("✅ All 10 pricing sync metrics are defined:")
        print("  - pricing_sync_duration_seconds")
        print("  - pricing_sync_total")
        print("  - pricing_sync_models_updated_total")
        print("  - pricing_sync_models_skipped_total")
        print("  - pricing_sync_errors_total")
        print("  - pricing_sync_last_success_timestamp")
        print("  - pricing_sync_job_duration_seconds")
        print("  - pricing_sync_job_queue_size")
        print("  - pricing_sync_models_fetched_total")
        print("  - pricing_sync_price_changes_total")
        print()
        return True

    except ImportError as e:
        print(f"❌ Failed to import metrics: {e}")
        return False


def test_helper_functions():
    """Test that all helper functions are accessible."""
    print("=" * 60)
    print("TEST 2: Helper Functions")
    print("=" * 60)

    try:
        from src.services.prometheus_metrics import (
            track_pricing_sync,
            record_pricing_sync_models_updated,
            record_pricing_sync_models_skipped,
            record_pricing_sync_models_fetched,
            record_pricing_sync_price_changes,
            record_pricing_sync_error,
            set_pricing_sync_job_queue_size,
            track_pricing_sync_job,
        )

        print("✅ All 8 helper functions are defined:")
        print("  - track_pricing_sync (context manager)")
        print("  - record_pricing_sync_models_updated")
        print("  - record_pricing_sync_models_skipped")
        print("  - record_pricing_sync_models_fetched")
        print("  - record_pricing_sync_price_changes")
        print("  - record_pricing_sync_error")
        print("  - set_pricing_sync_job_queue_size")
        print("  - track_pricing_sync_job (context manager)")
        print()
        return True

    except ImportError as e:
        print(f"❌ Failed to import helper functions: {e}")
        return False


def test_metric_recording():
    """Test that metrics can be recorded."""
    print("=" * 60)
    print("TEST 3: Metric Recording")
    print("=" * 60)

    try:
        from src.services.prometheus_metrics import (
            record_pricing_sync_models_updated,
            record_pricing_sync_models_skipped,
            record_pricing_sync_models_fetched,
            record_pricing_sync_price_changes,
            record_pricing_sync_error,
            set_pricing_sync_job_queue_size,
        )

        # Test recording metrics
        print("Testing metric recording...")

        record_pricing_sync_models_fetched("test-provider", 100)
        print("  ✅ Recorded models_fetched: 100")

        record_pricing_sync_models_updated("test-provider", 50)
        print("  ✅ Recorded models_updated: 50")

        record_pricing_sync_price_changes("test-provider", 25)
        print("  ✅ Recorded price_changes: 25")

        record_pricing_sync_models_skipped("test-provider", "dynamic_pricing", 30)
        print("  ✅ Recorded models_skipped: 30 (reason: dynamic_pricing)")

        record_pricing_sync_error("test-provider", "api_error")
        print("  ✅ Recorded error: api_error")

        set_pricing_sync_job_queue_size("queued", 5)
        print("  ✅ Set job_queue_size: 5 (status: queued)")

        print()
        print("✅ All metric recording functions work correctly")
        print()
        return True

    except Exception as e:
        print(f"❌ Failed to record metrics: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_context_manager():
    """Test the track_pricing_sync context manager."""
    print("=" * 60)
    print("TEST 4: Context Manager")
    print("=" * 60)

    try:
        from src.services.prometheus_metrics import track_pricing_sync

        # Test successful sync
        print("Testing track_pricing_sync context manager...")
        with track_pricing_sync("test-provider", "test-trigger"):
            time.sleep(0.1)  # Simulate some work
            print("  ✅ Context manager executed successfully")

        # Test failed sync
        try:
            with track_pricing_sync("test-provider", "test-trigger"):
                raise Exception("Simulated error")
        except Exception:
            print("  ✅ Context manager handled error correctly")

        print()
        print("✅ Context manager works correctly")
        print()
        return True

    except Exception as e:
        print(f"❌ Failed context manager test: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_metrics_endpoint():
    """Test that metrics endpoint includes pricing sync metrics."""
    print("=" * 60)
    print("TEST 5: Metrics Endpoint")
    print("=" * 60)

    try:
        from prometheus_client import generate_latest

        # Generate metrics output
        metrics_output = generate_latest().decode('utf-8')

        # Check for pricing sync metrics
        expected_metrics = [
            "pricing_sync_duration_seconds",
            "pricing_sync_total",
            "pricing_sync_models_updated_total",
            "pricing_sync_models_skipped_total",
            "pricing_sync_errors_total",
            "pricing_sync_last_success_timestamp",
            "pricing_sync_job_duration_seconds",
            "pricing_sync_job_queue_size",
            "pricing_sync_models_fetched_total",
            "pricing_sync_price_changes_total",
        ]

        print("Checking metrics endpoint output...")
        missing_metrics = []
        for metric in expected_metrics:
            if metric in metrics_output:
                print(f"  ✅ Found: {metric}")
            else:
                print(f"  ❌ Missing: {metric}")
                missing_metrics.append(metric)

        print()
        if missing_metrics:
            print(f"❌ Missing {len(missing_metrics)} metrics")
            return False
        else:
            print("✅ All pricing sync metrics are exposed via /metrics endpoint")
            print()
            return True

    except Exception as e:
        print(f"❌ Failed to test metrics endpoint: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print()
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "PROMETHEUS METRICS TEST SUITE" + " " * 18 + "║")
    print("║" + " " * 15 + "Pricing Sync Metrics" + " " * 23 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    results = []

    # Run all tests
    results.append(("Metrics Definitions", test_metrics_definitions()))
    results.append(("Helper Functions", test_helper_functions()))
    results.append(("Metric Recording", test_metric_recording()))
    results.append(("Context Manager", test_context_manager()))
    results.append(("Metrics Endpoint", test_metrics_endpoint()))

    # Print summary
    print()
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print()
    print(f"Total: {passed}/{total} tests passed ({passed * 100 // total}%)")
    print("=" * 60)
    print()

    if passed == total:
        print("🎉 All tests passed! Prometheus metrics implementation is complete.")
        return 0
    else:
        print("⚠️  Some tests failed. Please review the output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
