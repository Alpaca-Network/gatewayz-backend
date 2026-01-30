#!/usr/bin/env python3
"""Test script to verify invalid provider handling."""

import asyncio
import os
import sys

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_invalid_provider():
    """Test that invalid provider is handled gracefully."""
    from src.services.pricing_sync_service import PricingSyncService

    service = PricingSyncService()

    print("Testing invalid provider: 'invalid_provider'")
    print("-" * 60)

    # This should handle the error gracefully
    result = await service.sync_provider_pricing(
        provider_slug="invalid_provider",
        dry_run=True,  # Don't write to DB
        triggered_by="test"
    )

    print("\nResult:")
    print(f"  Status: {result.get('status')}")
    print(f"  Error message: {result.get('error_message')}")
    print(f"  Models fetched: {result.get('models_fetched')}")
    print(f"  Errors: {result.get('errors')}")

    if result.get('status') == 'failed':
        print("\n✅ Invalid provider handled correctly - no crash!")
        return True
    else:
        print("\n❌ Unexpected result")
        return False


async def test_valid_provider():
    """Test that valid provider works."""
    from src.services.pricing_sync_service import PricingSyncService

    service = PricingSyncService()

    print("\n\nTesting valid provider: 'openrouter'")
    print("-" * 60)

    result = await service.sync_provider_pricing(
        provider_slug="openrouter",
        dry_run=True,  # Don't write to DB
        triggered_by="test"
    )

    print("\nResult:")
    print(f"  Status: {result.get('status')}")
    print(f"  Models fetched: {result.get('models_fetched')}")
    print(f"  Models updated: {result.get('models_updated')}")

    if result.get('status') == 'success':
        print("\n✅ Valid provider works correctly!")
        return True
    else:
        print("\n❌ Valid provider failed")
        return False


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Invalid Provider Error Handling")
    print("=" * 60)

    test1 = await test_invalid_provider()
    test2 = await test_valid_provider()

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Invalid provider test: {'✅ PASS' if test1 else '❌ FAIL'}")
    print(f"  Valid provider test: {'✅ PASS' if test2 else '❌ FAIL'}")
    print("=" * 60)

    return test1 and test2


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
