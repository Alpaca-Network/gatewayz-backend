#!/usr/bin/env python3
"""
Quick script to sync pricing manually

Usage:
    python3 scripts/utilities/sync_pricing_now.py              # Sync all
    python3 scripts/utilities/sync_pricing_now.py openrouter   # Sync provider
    python3 scripts/utilities/sync_pricing_now.py --stale      # Sync stale only
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from src.services.pricing_sync_background import get_pricing_sync_service

load_dotenv()


def main():
    """Main execution"""
    service = get_pricing_sync_service()

    # Parse command line args
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "--stale":
            print("🔄 Syncing stale pricing (older than 24 hours)...")
            stats = service.sync_stale_pricing(hours=24)
        elif arg == "--help" or arg == "-h":
            print(__doc__)
            return
        else:
            # Assume it's a provider name
            print(f"🔄 Syncing pricing for provider: {arg}...")
            stats = service.sync_provider_models(arg)
    else:
        print("🔄 Syncing pricing for ALL models...")
        print("⚠️  This may take a few minutes for 1000+ models...")
        stats = service.sync_all_models()

    print()
    print("✅ Sync complete!")
    print(f"   Synced: {stats['synced']}")
    print(f"   Failed: {stats['failed']}")
    print(f"   Skipped: {stats['skipped']}")
    print()


if __name__ == "__main__":
    main()
