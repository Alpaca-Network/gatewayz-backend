#!/usr/bin/env python3
"""
Script to count models per provider across all gateways.
"""

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

# Add repo root to path (works from any location)
sys.path.insert(0, str(Path(__file__).parent))

from src.services.models import get_all_models_parallel


async def main():
    """Fetch all models and count by provider."""
    print("Fetching models from all gateways...")
    print("This may take 30-60 seconds...\n")

    try:
        # Fetch all models from all gateways
        catalog = await get_all_models_parallel()

        if not catalog:
            print("❌ Failed to fetch models")
            return

        print(f"✅ Fetched {len(catalog)} total models\n")

        # Count by provider
        provider_counts = defaultdict(int)

        for model in catalog:
            # Extract provider slug
            provider_slug = None

            # Method 1: Check provider_slug field
            if isinstance(model, dict) and "provider_slug" in model:
                provider_slug = model["provider_slug"]

            # Method 2: Parse from ID (format: provider/model-name)
            elif isinstance(model, dict) and "id" in model:
                model_id = model["id"]
                if "/" in model_id:
                    provider_slug = model_id.split("/")[0]

            # Method 3: Check source_gateway
            elif isinstance(model, dict) and "source_gateway" in model:
                provider_slug = model["source_gateway"]

            if provider_slug:
                provider_counts[provider_slug] += 1

        # Sort by count (descending)
        sorted_providers = sorted(provider_counts.items(), key=lambda x: x[1], reverse=True)

        # Print results
        print("=" * 60)
        print("MODELS PER PROVIDER")
        print("=" * 60)
        print(f"{'Provider':<30} {'Model Count':>15}")
        print("-" * 60)

        total_models = 0
        for provider, count in sorted_providers:
            print(f"{provider:<30} {count:>15,}")
            total_models += count

        print("-" * 60)
        print(f"{'TOTAL':<30} {total_models:>15,}")
        print(f"{'UNIQUE PROVIDERS':<30} {len(provider_counts):>15,}")
        print("=" * 60)

        # Also show canonical models if available
        if hasattr(catalog, "canonical_models") and catalog.canonical_models:
            print(f"\n📊 Canonical (deduplicated) models: {len(catalog.canonical_models):,}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
