#!/usr/bin/env python3
"""
Pricing Format Diagnostic Script

This script investigates the current pricing format in the database to determine
if pricing is stored per-token, per-1K tokens, or per-1M tokens.

Run this BEFORE applying any migrations to understand the current state.

Usage:
    python scripts/utilities/diagnose_pricing_format.py

Output:
    - Summary of pricing value ranges
    - Suspected format for each provider
    - Sample pricing values
    - Recommendations for migration
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from decimal import Decimal
from src.config.supabase_config import get_supabase_client
import json


def analyze_pricing_distribution():
    """Analyze distribution of pricing values"""
    print("=" * 80)
    print("PRICING FORMAT DIAGNOSTIC REPORT")
    print("=" * 80)
    print()

    client = get_supabase_client()

    # Get all models with pricing
    result = client.table("models").select(
        "id, model_name, provider_id, pricing_prompt, pricing_completion, providers(name, slug)"
    ).not_.is_("pricing_prompt", "null").order("pricing_prompt", desc=True).execute()

    models = result.data

    if not models:
        print("⚠️  No models with pricing found in database!")
        return

    print(f"📊 Total models with pricing: {len(models)}")
    print()

    # Analyze value ranges
    pricing_values = [float(m["pricing_prompt"]) for m in models if m.get("pricing_prompt")]

    if not pricing_values:
        print("⚠️  No valid pricing values found!")
        return

    max_price = max(pricing_values)
    min_price = min(pricing_values)
    avg_price = sum(pricing_values) / len(pricing_values)

    print("📈 PRICING VALUE STATISTICS")
    print("-" * 80)
    print(f"  Maximum: ${max_price:,.10f}")
    print(f"  Minimum: ${min_price:,.10f}")
    print(f"  Average: ${avg_price:,.10f}")
    print()

    # Categorize by suspected format
    per_token_count = 0
    per_1k_count = 0
    per_1m_count = 0

    for price in pricing_values:
        if price < 0.000001:
            per_token_count += 1
        elif price < 0.001:
            per_1k_count += 1
        else:
            per_1m_count += 1

    print("🔍 SUSPECTED FORMAT DISTRIBUTION")
    print("-" * 80)
    print(f"  Per-token (< 0.000001):  {per_token_count:4d} models ({per_token_count/len(pricing_values)*100:.1f}%)")
    print(f"  Per-1K (0.000001-0.001): {per_1k_count:4d} models ({per_1k_count/len(pricing_values)*100:.1f}%)")
    print(f"  Per-1M (> 0.001):        {per_1m_count:4d} models ({per_1m_count/len(pricing_values)*100:.1f}%)")
    print()

    # Overall assessment
    if per_1m_count > len(pricing_values) * 0.8:
        format_guess = "per-1M tokens"
        confidence = "HIGH"
    elif per_1k_count > len(pricing_values) * 0.8:
        format_guess = "per-1K tokens"
        confidence = "HIGH"
    elif per_token_count > len(pricing_values) * 0.8:
        format_guess = "per-token"
        confidence = "HIGH"
    else:
        format_guess = "MIXED (inconsistent)"
        confidence = "UNCERTAIN"

    print("🎯 ASSESSMENT")
    print("-" * 80)
    print(f"  Most likely format: {format_guess}")
    print(f"  Confidence: {confidence}")
    print()

    # Show samples by provider
    print("📋 SAMPLE PRICING BY PROVIDER")
    print("-" * 80)

    providers_seen = set()
    for model in models[:50]:  # Show first 50
        provider_info = model.get("providers", {})
        provider_slug = provider_info.get("slug", "unknown") if isinstance(provider_info, dict) else "unknown"

        if provider_slug not in providers_seen:
            providers_seen.add(provider_slug)

            prompt_price = float(model.get("pricing_prompt") or 0)
            completion_price = float(model.get("pricing_completion") or 0)

            # Determine suspected format
            if prompt_price < 0.000001:
                suspected_format = "per-token"
            elif prompt_price < 0.001:
                suspected_format = "per-1K"
            else:
                suspected_format = "per-1M"

            print(f"  {provider_slug:20s} | {model['model_name'][:40]:40s}")
            print(f"    Prompt: ${prompt_price:,.10f} | Completion: ${completion_price:,.10f}")
            print(f"    Suspected: {suspected_format}")
            print()

    # Cost calculation examples
    print("💰 COST CALCULATION EXAMPLES")
    print("-" * 80)
    print("Testing 1000 tokens with different interpretations:")
    print()

    test_model = models[0]
    test_price = float(test_model.get("pricing_prompt") or 0)

    print(f"  Sample model: {test_model['model_name']}")
    print(f"  Stored price: ${test_price}")
    print()

    # Calculate cost under different assumptions
    tokens = 1000

    cost_as_per_token = tokens * test_price
    cost_as_per_1k = tokens * (test_price / 1000)
    cost_as_per_1m = tokens * (test_price / 1000000)

    print(f"  If stored as per-token:   ${cost_as_per_token:,.8f}")
    print(f"  If stored as per-1K:      ${cost_as_per_1k:,.8f}")
    print(f"  If stored as per-1M:      ${cost_as_per_1m:,.8f}")
    print()

    # Reality check
    print("  ✓ Realistic cost range for 1000 tokens: $0.00001 - $0.10")
    print()

    if 0.00001 <= cost_as_per_token <= 0.10:
        print("  ✅ Per-token interpretation seems realistic")
    elif 0.00001 <= cost_as_per_1k <= 0.10:
        print("  ✅ Per-1K interpretation seems realistic")
    elif 0.00001 <= cost_as_per_1m <= 0.10:
        print("  ✅ Per-1M interpretation seems realistic")
    else:
        print("  ⚠️  None of the interpretations produce realistic costs!")

    print()

    # Recommendations
    print("📌 RECOMMENDATIONS")
    print("-" * 80)

    if format_guess == "per-1M tokens":
        print("  🔴 CRITICAL: Pricing appears to be stored per-1M tokens")
        print("  🔴 Database schema claims pricing is per-token")
        print("  🔴 Cost calculations will be 1,000,000× TOO HIGH")
        print()
        print("  Action Required:")
        print("  1. Run database migration to convert values to per-token")
        print("     (Divide all pricing values by 1,000,000)")
        print("  2. Update provider normalization to convert API responses")
        print("  3. Verify cost calculations after migration")
        print()
    elif format_guess == "per-1K tokens":
        print("  🟡 WARNING: Pricing appears to be stored per-1K tokens")
        print("  🟡 Database schema claims pricing is per-token")
        print("  🟡 Cost calculations will be 1,000× TOO HIGH")
        print()
        print("  Action Required:")
        print("  1. Run database migration to convert values to per-token")
        print("     (Divide all pricing values by 1,000)")
        print("  2. Update provider normalization to convert API responses")
        print("  3. Verify cost calculations after migration")
        print()
    elif format_guess == "per-token":
        print("  ✅ Pricing appears to be correctly stored per-token")
        print("  ✅ Schema matches actual data format")
        print()
        print("  Recommendation:")
        print("  - Verify a few cost calculations manually")
        print("  - Ensure all providers normalize their API responses correctly")
        print()
    else:
        print("  🟠 UNCERTAIN: Pricing format is inconsistent")
        print("  🟠 Different providers may be using different formats")
        print()
        print("  Action Required:")
        print("  1. Investigate each provider individually")
        print("  2. Standardize all providers to per-token format")
        print("  3. Run comprehensive migration")
        print()

    # Export data for further analysis
    print("💾 EXPORTING DATA")
    print("-" * 80)

    export_data = {
        "summary": {
            "total_models": len(models),
            "max_price": max_price,
            "min_price": min_price,
            "avg_price": avg_price,
            "per_token_count": per_token_count,
            "per_1k_count": per_1k_count,
            "per_1m_count": per_1m_count,
            "suspected_format": format_guess,
            "confidence": confidence,
        },
        "samples": [
            {
                "model_name": m["model_name"],
                "provider": m.get("providers", {}).get("slug") if isinstance(m.get("providers"), dict) else None,
                "pricing_prompt": float(m.get("pricing_prompt") or 0),
                "pricing_completion": float(m.get("pricing_completion") or 0),
            }
            for m in models[:100]
        ]
    }

    output_file = "pricing_diagnostic_report.json"
    with open(output_file, "w") as f:
        json.dump(export_data, f, indent=2)

    print(f"  ✓ Exported detailed report to: {output_file}")
    print()

    print("=" * 80)
    print("END OF DIAGNOSTIC REPORT")
    print("=" * 80)


def check_recent_costs():
    """Check recent cost calculations"""
    print()
    print("💵 RECENT COST CALCULATIONS")
    print("-" * 80)

    client = get_supabase_client()

    result = client.table("chat_completion_requests").select(
        "request_id, input_tokens, output_tokens, cost_usd, input_cost_usd, output_cost_usd, "
        "models(model_name, pricing_prompt, pricing_completion)"
    ).not_.is_("cost_usd", "null").order("created_at", desc=True).limit(10).execute()

    requests = result.data

    if not requests:
        print("  ℹ️  No recent requests with cost data found")
        return

    print(f"  Showing {len(requests)} most recent requests with cost data:")
    print()

    for req in requests:
        model_info = req.get("models", {})
        model_name = model_info.get("model_name", "unknown") if isinstance(model_info, dict) else "unknown"

        input_tokens = req.get("input_tokens", 0)
        output_tokens = req.get("output_tokens", 0)
        cost = req.get("cost_usd", 0)

        if input_tokens > 0:
            cost_per_1k_tokens = (cost / (input_tokens + output_tokens)) * 1000
            print(f"  {model_name[:50]}")
            print(f"    Tokens: {input_tokens} in + {output_tokens} out = {input_tokens + output_tokens} total")
            print(f"    Cost: ${float(cost):.8f} (${cost_per_1k_tokens:.6f} per 1K tokens)")

            # Reality check
            if cost_per_1k_tokens < 0.00001:
                print(f"    ⚠️  Suspiciously LOW cost per 1K tokens")
            elif cost_per_1k_tokens > 100:
                print(f"    🔴 Suspiciously HIGH cost per 1K tokens")
            else:
                print(f"    ✅ Cost seems reasonable")
            print()


if __name__ == "__main__":
    try:
        analyze_pricing_distribution()
        check_recent_costs()
    except Exception as e:
        print(f"\n❌ Error during diagnostic: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
