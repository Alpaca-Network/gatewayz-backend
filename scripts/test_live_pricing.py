#!/usr/bin/env python3
"""
Test Live Pricing Fetching

This script tests the live pricing system by fetching pricing for various models
from different providers and verifying the results.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.pricing import get_model_pricing_async, clear_pricing_cache
from src.services.pricing_live_fetch import get_live_pricing_fetcher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def test_model_pricing(model_id: str):
    """Test pricing fetch for a single model"""
    print(f"\n{'='*80}")
    print(f"Testing: {model_id}")
    print(f"{'='*80}")

    try:
        pricing = await get_model_pricing_async(model_id)

        print(f"✅ Success!")
        print(f"   Source: {pricing.get('source', 'unknown')}")
        print(f"   Prompt price: ${pricing['prompt']:.10f} per token")
        print(f"   Completion price: ${pricing['completion']:.10f} per token")
        print(f"   Found: {pricing.get('found', False)}")

        # Calculate example cost
        example_tokens = 1_000_000  # 1M tokens
        example_cost_prompt = example_tokens * pricing['prompt']
        example_cost_completion = example_tokens * pricing['completion']

        print(f"\n   Example cost for 1M tokens:")
        print(f"   Prompt: ${example_cost_prompt:.2f}")
        print(f"   Completion: ${example_cost_completion:.2f}")

        return pricing

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """Main test function"""
    print("\n" + "="*80)
    print("LIVE PRICING SYSTEM TEST")
    print("="*80)

    # Clear cache to force live fetches
    clear_pricing_cache()
    print("\n✅ Cleared pricing cache to force live API fetches\n")

    # Test models from different providers
    test_models = [
        # OpenRouter models
        "openai/gpt-4",
        "openai/gpt-4o-mini",
        "anthropic/claude-3-5-sonnet",
        "meta-llama/llama-3.1-8b-instruct",

        # Featherless models
        "featherless/meta-llama-3.1-8b-instruct",

        # Near AI models
        "near/llama-3.1-8b",

        # DeepInfra (should fall back to JSON)
        "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct",
    ]

    results = {}

    for model_id in test_models:
        result = await test_model_pricing(model_id)
        results[model_id] = result

        # Small delay between requests to be nice to APIs
        await asyncio.sleep(0.5)

    # Summary
    print(f"\n\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}\n")

    live_api_count = sum(1 for r in results.values() if r and 'live_api' in r.get('source', ''))
    fallback_count = sum(1 for r in results.values() if r and 'cache_fallback' in r.get('source', ''))
    default_count = sum(1 for r in results.values() if r and r.get('source') == 'default')
    error_count = sum(1 for r in results.values() if r is None)

    print(f"Total models tested: {len(test_models)}")
    print(f"✅ Live API fetches: {live_api_count}")
    print(f"⚠️  JSON fallbacks: {fallback_count}")
    print(f"⚠️  Default pricing: {default_count}")
    print(f"❌ Errors: {error_count}")

    if live_api_count > 0:
        print(f"\n🎉 SUCCESS! Live pricing is working for {live_api_count}/{len(test_models)} models!")
    else:
        print(f"\n⚠️  WARNING: No models fetched via live API. Check provider endpoints.")

    # Cleanup
    fetcher = get_live_pricing_fetcher()
    await fetcher.close()

    print("\n" + "="*80)
    print("Test complete!")
    print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
