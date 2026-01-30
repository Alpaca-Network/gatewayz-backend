#!/usr/bin/env python3
"""
Script to sync models for the 4 recently added providers
"""
import os
import sys
from pathlib import Path

# Add src to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from src.services.model_catalog_sync import sync_provider_models

# Providers that need to be synced
PROVIDERS_TO_SYNC = [
    "zai",
    "morpheus",
    "sybil",
    "canopywave",
]


def main():
    """Sync models for missing providers"""
    print("=" * 80)
    print("SYNCING MODELS FOR RECENTLY ADDED PROVIDERS")
    print("=" * 80)
    print()

    results = {}

    for provider_slug in PROVIDERS_TO_SYNC:
        print(f"\n{'=' * 80}")
        print(f"Syncing provider: {provider_slug}")
        print("=" * 80)

        try:
            result = sync_provider_models(provider_slug, dry_run=False)
            results[provider_slug] = result

            if result["success"]:
                print(f"✓ SUCCESS")
                print(f"  Models fetched: {result.get('models_fetched', 0)}")
                print(f"  Models transformed: {result.get('models_transformed', 0)}")
                print(f"  Models synced: {result.get('models_synced', 0)}")
                print(f"  Models skipped: {result.get('models_skipped', 0)}")
            else:
                print(f"✗ FAILED")
                print(f"  Error: {result.get('error', 'Unknown error')}")

        except Exception as e:
            print(f"✗ ERROR: {e}")
            results[provider_slug] = {
                "success": False,
                "error": str(e)
            }

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    successful = [p for p, r in results.items() if r.get("success")]
    failed = [p for p, r in results.items() if not r.get("success")]

    print(f"\nSuccessful syncs: {len(successful)}/{len(PROVIDERS_TO_SYNC)}")
    if successful:
        for p in successful:
            print(f"  ✓ {p}: {results[p].get('models_synced', 0)} models")

    if failed:
        print(f"\nFailed syncs: {len(failed)}")
        for p in failed:
            print(f"  ✗ {p}: {results[p].get('error', 'Unknown error')}")

    print("\n" + "=" * 80)

    # Return exit code
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
